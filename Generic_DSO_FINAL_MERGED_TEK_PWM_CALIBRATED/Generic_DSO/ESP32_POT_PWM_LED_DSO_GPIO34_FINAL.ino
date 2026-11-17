/*
  FINAL ESP32 PWM DSO FIRMWARE: ESP32 potentiometer -> PWM LED -> 2 real scopes
  Target board: classic ESP32 DevKit / ESP32-WROOM-32 (GPIO34, GPIO35).

  POTENTIOMETER (10 kohm):
    GPIO26 (OUTPUT HIGH ~3.3V) -- 10k potentiometer outer terminal
    GPIO35 -------------------- potentiometer CENTER / WIPER
    GND ----------------------- other outer terminal

  LED:
    GPIO25 -- 330 ohm -- LED ANODE (+)
    LED CATHODE (-) --------- GND

  PWM measurement for Python DSO:
    GPIO25 ---- 1k R1 -----+---- GPIO34
                           |
                          1k R2
                           |
                          GND

  Real Tektronix DSO: CH1 probe tip -> GPIO25, ground clip -> ESP32 GND.

  GPIO25 output = 100 Hz PWM, duty follows potentiometer 0..100%.
  Note: the two GPIO34 divider resistors are EACH 1k ohm;
  the LED needs its own 330-ohm to 1k-ohm current-limiting resistor.
  GPIO34 reads true PWM through the 1k/1k divider.
  Reported voltage to Python DSO = 2 x measured ADC input voltage.

  Python DSO: Serial, 921600 baud, CH1 1 V/div, 5 ms/div,
  trigger rising 1.65V, reset any previous non-unity channel gain.

  Compatible with Arduino-ESP32 core 2.x and 3.x.
  GPIO26 is for powering ONLY the 10k potentiometer; not external loads.
*/

#include <Arduino.h>
#include <esp_timer.h>
#include <esp_arduino_version.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/queue.h>

// ---------- Easy-to-change configuration ----------
constexpr int POT_POWER_PIN = 26; // GPIO is HIGH to power 10k pot
constexpr int POT_PIN       = 35; // pot middle pin
constexpr int PWM_PIN       = 25; // LED + Tektronix PWM output
constexpr int SENSE_PIN     = 34; // PWM measurement through divider

constexpr bool REVERSE_KNOB = false; // set true only if turning clockwise dims LED
constexpr uint32_t BAUD = 921600;
constexpr uint32_t PWM_HZ = 100;
constexpr uint8_t PWM_RES_BITS = 10;
constexpr uint16_t PWM_MAX = (1U << PWM_RES_BITS) - 1U;
constexpr uint8_t LEGACY_LEDC_CHANNEL = 0;
constexpr uint32_t SAMPLE_INTERVAL_US = 500; // target 2000 measured points/sec
constexpr uint32_t POT_INTERVAL_US = 20000;   // update brightness every 20ms
constexpr size_t FRAME_SAMPLES = 64;
constexpr float DIVIDER_MULTIPLIER = 2.0f;    // two equal 1k resistors

struct Frame {
  uint64_t timestamps[FRAME_SAMPLES];
  uint16_t adcMillivolts[FRAME_SAMPLES];
  uint8_t size;
  bool discontinuity;
};

QueueHandle_t txQueue = nullptr;
TaskHandle_t adcTaskHandle = nullptr;
esp_timer_handle_t adcTimer = nullptr;
uint32_t sequenceNumber = 0;
uint32_t lastHelloMs = 0;

void sendHello() {
  // Exact Generic DSO protocol v1 metadata, unchanged on repeat.
  Serial.println(F("{\"type\":\"hello\",\"protocol\":1,"
                   "\"device\":\"ESP32 POT to PWM Scope\","
                   "\"channels\":[{\"name\":\"GPIO25 PWM via GPIO34\",\"unit\":\"V\"}]}"));
}

bool initPwm() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  return ledcAttach(PWM_PIN, PWM_HZ, PWM_RES_BITS);
#else
  const double frequency = ledcSetup(LEGACY_LEDC_CHANNEL, PWM_HZ, PWM_RES_BITS);
  if (frequency <= 0) return false;
  ledcAttachPin(PWM_PIN, LEGACY_LEDC_CHANNEL);
  return true;
#endif
}

void updatePwm(uint16_t duty) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(PWM_PIN, duty);
#else
  ledcWrite(LEGACY_LEDC_CHANNEL, duty);
#endif
}

void adcTimerCallback(void *) {
  // Keep the timer callback short: let the separate task read actual ADC.
  if (adcTaskHandle) xTaskNotifyGive(adcTaskHandle);
}

void adcCaptureTask(void *) {
  Frame frame{};
  bool gapFromQueue = false;
  uint64_t previousSampleUs = 0;
  uint64_t nextPotReadUs = 0;
  float filteredPot = (float)analogRead(POT_PIN);
  int previousDuty = -1;

  while (true) {
    const uint32_t timerEvents = ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    const uint64_t nowUs = (uint64_t)esp_timer_get_time();

    if (timerEvents > 1 ||
        (previousSampleUs != 0 &&
         nowUs > previousSampleUs + 4ULL * SAMPLE_INTERVAL_US)) {
      frame.discontinuity = true;
    }
    previousSampleUs = nowUs;

    // Knob determines LED brightness and scope PWM duty; output frequency stays fixed.
    if (nowUs >= nextPotReadUs) {
      const int reading = analogRead(POT_PIN);
      filteredPot += 0.25f * ((float)reading - filteredPot); // reduce jitter
      int pot = constrain((int)(filteredPot + 0.5f), 0, 4095);
      if (REVERSE_KNOB) pot = 4095 - pot;

      uint16_t duty = (uint16_t)(((uint32_t)pot * PWM_MAX + 2047U) / 4095U);
      if (pot <= 24) duty = 0;
      if (pot >= 4071) duty = PWM_MAX;
      if ((int)duty != previousDuty) {
        updatePwm(duty);
        previousDuty = duty;
      }
      nextPotReadUs = nowUs + POT_INTERVAL_US;
    }

    // This is a real GPIO34 ADC measurement, not a generated square-wave value.
    const uint32_t mV = analogReadMilliVolts(SENSE_PIN);
    const uint8_t index = frame.size;
    frame.timestamps[index] = (uint64_t)esp_timer_get_time();
    frame.adcMillivolts[index] = (uint16_t)min(mV, (uint32_t)65535);
    frame.size++;

    if (frame.size == FRAME_SAMPLES) {
      frame.discontinuity |= gapFromQueue;
      if (xQueueSend(txQueue, &frame, 0) == pdTRUE) {
        gapFromQueue = false;
      } else {
        gapFromQueue = true;
      }
      frame.size = 0;
      frame.discontinuity = false;
    }
  }
}

void sendFrame(const Frame &frame) {
  Serial.print(F("{\"type\":\"samples\",\"seq\":"));
  Serial.print(sequenceNumber++);
  if (frame.discontinuity) Serial.print(F(",\"discontinuity\":true"));
  Serial.print(F(",\"times\":["));
  for (uint8_t i = 0; i < frame.size; ++i) {
    if (i) Serial.print(',');
    Serial.print((double)frame.timestamps[i] / 1000000.0, 6);
  }
  Serial.print(F("],\"values\":["));
  for (uint8_t i = 0; i < frame.size; ++i) {
    if (i) Serial.print(',');
    const float volts = frame.adcMillivolts[i] * 0.001f * DIVIDER_MULTIPLIER;
    Serial.print('[');
    Serial.print(volts, 4);
    Serial.print(']');
  }
  Serial.println(F("]}"));
}

void setup() {
  Serial.begin(BAUD);

  // No access to physical 3V3 pin required for 10k potentiometer.
  pinMode(POT_POWER_PIN, OUTPUT);
  digitalWrite(POT_POWER_PIN, HIGH);
  delay(10);

  pinMode(POT_PIN, INPUT);
  pinMode(SENSE_PIN, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(POT_PIN, ADC_11db);
  analogSetPinAttenuation(SENSE_PIN, ADC_11db);

  if (!initPwm()) while (true) delay(1000);
  updatePwm(0);

  txQueue = xQueueCreate(8, sizeof(Frame));
  if (!txQueue) while (true) delay(1000);

  if (xTaskCreatePinnedToCore(adcCaptureTask, "ADC_capture", 4096,
                              nullptr, 3, &adcTaskHandle, 1) != pdPASS) {
    while (true) delay(1000);
  }

  const esp_timer_create_args_t timerArgs = {
    .callback = &adcTimerCallback,
    .arg = nullptr,
    .dispatch_method = ESP_TIMER_TASK,
    .name = "ADC_scope"
  };
  if (esp_timer_create(&timerArgs, &adcTimer) != ESP_OK ||
      esp_timer_start_periodic(adcTimer, SAMPLE_INTERVAL_US) != ESP_OK) {
    while (true) delay(1000);
  }

  delay(100);
  sendHello();
  lastHelloMs = millis();
}

void loop() {
  Frame frame{};
  if (xQueueReceive(txQueue, &frame, pdMS_TO_TICKS(10)) == pdTRUE) {
    sendFrame(frame);
  }
  if (millis() - lastHelloMs >= 2000UL) {
    sendHello();
    lastHelloMs = millis();
  }
}
