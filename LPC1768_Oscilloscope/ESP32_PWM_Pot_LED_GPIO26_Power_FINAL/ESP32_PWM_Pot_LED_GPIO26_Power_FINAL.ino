/*
  ESP32 potentiometer-controlled LED PWM + REAL waveform to Generic DSO.
  For classic ESP32 DevKit / ESP32-WROOM-32 with ADC1 GPIO34 and GPIO35.

  Wiring:
    POT 10k end ---- GPIO26 (OUTPUT HIGH), other end ---- GND, wiper ---- GPIO35.
    GPIO25 ---- 330 ohm ---- LED anode (+), LED cathode (-) ---- GND.
    GPIO25 ---- 10k R1 ---- GPIO34 ---- 10k R2 ---- GND.
    (optional) Tektronix tip ---- GPIO25, probe ground ---- GND.

  GPIO26 is a low-current supply ONLY for a 10k (or higher) potentiometer.
  Do NOT use GPIO26 to supply LEDs, motors, modules or low-resistance pots.
  GPIO25 outputs 100 Hz hardware PWM; potentiometer varies duty 0..100%.
  GPIO34 measures the PWM physically through a 1:2 resistor divider.
  The measured ADC voltage is multiplied by 2 to report GPIO25 volts.
  GPIO34 is never exposed to the full 3.3V PWM output.
  Samples carry actual acquisition timestamps (not a pretend sample rate).

  Generic DSO: Serial 921600, CH1 1 V/div, Time 5 ms/div,
               DC coupling, trigger rising at ~1.65 V, gain = 1.
  Do NOT use TEK PRESET's 500 ms/div for this 100 Hz PWM demonstration.
  Arduino-ESP32 cores v2.x and v3.x are supported.
*/

#include <Arduino.h>
#include <esp_timer.h>
#include <esp_arduino_version.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/queue.h>

constexpr uint8_t POT_POWER_PIN = 26; // Output HIGH -> 10k pot end
constexpr uint8_t PWM_PIN   = 25; // Output -> LED, real DSO and divider
constexpr uint8_t SENSE_PIN = 34; // Divider midpoint -> ADC1
constexpr uint8_t POT_PIN   = 35; // Potentiometer wiper -> ADC1
constexpr uint32_t SERIAL_BAUD = 921600;
constexpr uint32_t PWM_FREQUENCY_HZ = 100;
constexpr uint8_t PWM_BITS = 10;
constexpr uint16_t PWM_MAX_DUTY = (1U << PWM_BITS) - 1U;
constexpr uint8_t OLD_LEDC_CHANNEL = 0; // Used only by ESP32 core 2.x
constexpr int64_t SAMPLE_PERIOD_US = 500; // Target ~2000 samples/s
constexpr uint8_t BLOCK_SAMPLES = 64;
constexpr uint32_t POT_UPDATE_US = 20000; // ~50 pot readings/s
constexpr float INPUT_DIVIDER_RATIO = 2.0f; // 10k : 10k divider

struct SampleBlock {
  uint64_t timesUs[BLOCK_SAMPLES];
  uint16_t voltageMv[BLOCK_SAMPLES]; // Measured AT GPIO34
  uint8_t count;
  bool discontinuity;
};

static QueueHandle_t transmitQueue = nullptr;
static TaskHandle_t acquisitionHandle = nullptr;
static esp_timer_handle_t sampleTimer = nullptr;
static uint32_t sequenceNumber = 0;
static uint32_t lastHelloMs = 0;

void sendHello() {
  // Repeated unchanged metadata is safe for the Generic DSO JSON v1 parser.
  Serial.println(F("{\"type\":\"hello\",\"protocol\":1,"
                   "\"device\":\"ESP32 Potentiometer PWM 100 Hz\","
                   "\"channels\":[{\"name\":\"GPIO25 PWM via GPIO34\",\"unit\":\"V\"}]}"));
}

void setPwmDuty(uint16_t duty) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(PWM_PIN, duty); // Core 3.x: pin-based LEDC API
#else
  ledcWrite(OLD_LEDC_CHANNEL, duty); // Core 2.x: channel-based API
#endif
}

bool startPwm() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  return ledcAttach(PWM_PIN, PWM_FREQUENCY_HZ, PWM_BITS);
#else
  const double actualFrequency = ledcSetup(OLD_LEDC_CHANNEL, PWM_FREQUENCY_HZ, PWM_BITS);
  if (actualFrequency <= 0) return false;
  ledcAttachPin(PWM_PIN, OLD_LEDC_CHANNEL);
  return true;
#endif
}

// ESP_TIMER_TASK dispatch: only signal the acquisition task; no ADC or serial
// operations inside the timer callback.
void sampleTimerCallback(void *arg) {
  if (acquisitionHandle != nullptr) xTaskNotifyGive(acquisitionHandle);
}

// ADC reading and PWM updates happen in the SAME task to avoid ADC conflicts.
// Serial sending is in loop(), so it cannot block regular acquisition.
void acquisitionTask(void *arg) {
  (void)arg;
  SampleBlock block{};
  bool nextBlockHasGap = false;
  uint64_t lastStampUs = 0;
  uint64_t nextPotUs = 0;
  float smoothedPot = (float)analogRead(POT_PIN);
  int lastDuty = -1;

  while (true) {
    uint32_t notifications = ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    const uint64_t stampUs = (uint64_t)esp_timer_get_time();

    // If timer events were missed, identify a discontinuity, do not invent data.
    if (notifications > 1 ||
        (lastStampUs && stampUs > lastStampUs + (uint64_t)SAMPLE_PERIOD_US * 4ULL)) {
      block.discontinuity = true;
    }
    lastStampUs = stampUs;

    // Update brightness at ~50 Hz; hardware LEDC keeps PWM toggling independently.
    if (stampUs >= nextPotUs) {
      const int potRaw = analogRead(POT_PIN);
      smoothedPot += 0.25f * ((float)potRaw - smoothedPot);
      int raw = (int)(smoothedPot + 0.5f);
      raw = constrain(raw, 0, 4095);
      uint16_t duty = (uint16_t)(((uint32_t)raw * PWM_MAX_DUTY + 2047U) / 4095U);
      if (raw < 24) duty = 0;
      if (raw > 4071) duty = PWM_MAX_DUTY;
      if ((int)duty != lastDuty) {
        setPwmDuty(duty);
        lastDuty = (int)duty;
      }
      nextPotUs = stampUs + POT_UPDATE_US;
    }

    // Measure the real PWM voltage at GPIO34 (~0..1.65 V through divider).
    const uint32_t measuredMv = analogReadMilliVolts(SENSE_PIN);
    const uint8_t i = block.count;
    block.timesUs[i] = (uint64_t)esp_timer_get_time(); // actual ADC-read completion time
    block.voltageMv[i] = (uint16_t)(measuredMv > 65535U ? 65535U : measuredMv);
    block.count = i + 1;

    if (block.count == BLOCK_SAMPLES) {
      block.discontinuity = block.discontinuity || nextBlockHasGap;
      if (xQueueSend(transmitQueue, &block, 0) == pdTRUE) {
        nextBlockHasGap = false;
      } else {
        // Queue full: the host must not treat later samples as continuous.
        nextBlockHasGap = true;
      }
      block.count = 0;
      block.discontinuity = false;
    }
  }
}

void sendSamples(const SampleBlock &block) {
  Serial.print(F("{\"type\":\"samples\",\"seq\":"));
  Serial.print(sequenceNumber++);
  if (block.discontinuity) Serial.print(F(",\"discontinuity\":true"));

  Serial.print(F(",\"times\":["));
  for (uint8_t i = 0; i < block.count; ++i) {
    if (i) Serial.print(',');
    Serial.print((double)block.timesUs[i] / 1000000.0, 6);
  }

  Serial.print(F("],\"values\":["));
  for (uint8_t i = 0; i < block.count; ++i) {
    if (i) Serial.print(',');
    // Correct for the external 10k+10k divider; report actual PWM volts.
    const float gpio25Volts = (float)block.voltageMv[i] * 0.001f * INPUT_DIVIDER_RATIO;
    Serial.print('[');
    Serial.print(gpio25Volts, 4);
    Serial.print(']');
  }
  Serial.println(F("]}"));
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  pinMode(POT_POWER_PIN, OUTPUT);
  digitalWrite(POT_POWER_PIN, HIGH); // Approx. 3.3 V at a light load
  delay(5); // Let the potentiometer voltage settle
  pinMode(PWM_PIN, OUTPUT);
  pinMode(SENSE_PIN, INPUT);
  pinMode(POT_PIN, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(SENSE_PIN, ADC_11db);
  analogSetPinAttenuation(POT_PIN, ADC_11db);

  if (!startPwm()) {
    while (true) delay(1000); // LEDC unavailable / bad pin
  }
  setPwmDuty(0);

  transmitQueue = xQueueCreate(8, sizeof(SampleBlock));
  if (transmitQueue == nullptr) while (true) delay(1000);

  // Dedicated sampler task, so transmitting 64 samples never pauses ADC capture.
  if (xTaskCreatePinnedToCore(acquisitionTask, "pwm_adc", 4096, nullptr, 3,
                              &acquisitionHandle, 1) != pdPASS) {
    while (true) delay(1000);
  }

  const esp_timer_create_args_t args = {
    .callback = &sampleTimerCallback,
    .arg = nullptr,
    .dispatch_method = ESP_TIMER_TASK,
    .name = "pwm_scope"
  };
  if (esp_timer_create(&args, &sampleTimer) != ESP_OK ||
      esp_timer_start_periodic(sampleTimer, SAMPLE_PERIOD_US) != ESP_OK) {
    while (true) delay(1000);
  }

  delay(100);
  sendHello();
  lastHelloMs = millis();
}

void loop() {
  SampleBlock received{};
  if (xQueueReceive(transmitQueue, &received, pdMS_TO_TICKS(10)) == pdTRUE) {
    sendSamples(received);
  }
  const uint32_t nowMs = millis();
  if (nowMs - lastHelloMs >= 10000) {
    sendHello();
    lastHelloMs = nowMs;
  }
}
