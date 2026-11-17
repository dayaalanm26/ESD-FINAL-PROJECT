#include <Arduino.h>
#include <esp_arduino_version.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

namespace {
constexpr uint8_t POT_POWER_PIN = 26;
constexpr uint8_t POT_PIN = 35;
constexpr uint8_t PWM_PIN = 25;
constexpr uint8_t SENSE_PIN = 34;
constexpr bool REVERSE_KNOB = false;
constexpr uint32_t BAUD = 921600;
constexpr uint32_t PWM_HZ = 100;
constexpr uint8_t PWM_BITS = 10;
constexpr uint16_t MAX_DUTY = (1U << PWM_BITS) - 1;
constexpr uint32_t SAMPLE_US = 500;
constexpr size_t BLOCK_SIZE = 48;

struct Block {
  int64_t times[BLOCK_SIZE];
  uint32_t millivolts[BLOCK_SIZE];
  bool gap;
};

QueueHandle_t queue = nullptr;
TaskHandle_t sampler = nullptr;
esp_timer_handle_t timer = nullptr;
uint32_t sequence = 0;
uint32_t previousHello = 0;

void hello() {
  Serial.println(F("{\"type\":\"hello\",\"protocol\":1,\"device\":\"ESP32 Potentiometer PWM DSO\","
                   "\"channels\":[{\"name\":\"GPIO25 PWM via GPIO34\",\"unit\":\"V\"}]}"));
  previousHello = millis();
}

bool configurePwm() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  return ledcAttach(PWM_PIN, PWM_HZ, PWM_BITS);
#else
  if (ledcSetup(0, PWM_HZ, PWM_BITS) <= 0) return false;
  ledcAttachPin(PWM_PIN, 0);
  return true;
#endif
}

void setDuty(uint16_t duty) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(PWM_PIN, duty);
#else
  ledcWrite(0, duty);
#endif
}

void requestSample(void *) {
  xTaskNotifyGive(sampler);
}

void acquire(void *) {
  Block block{};
  bool missingBlock = false;
  size_t count = 0;
  int64_t prevTime = 0;
  int64_t nextPot = 0;
  float pot = analogRead(POT_PIN);
  int previousDuty = -1;
  for (;;) {
    const uint32_t notifications = ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    const int64_t now = esp_timer_get_time();
    if (notifications > 1 || (prevTime && now - prevTime > SAMPLE_US * 4)) {
      count = 0;
      block.gap = true;
    }
    prevTime = now;
    if (now >= nextPot) {
      pot += 0.25f * (analogRead(POT_PIN) - pot);
      int position = constrain(static_cast<int>(pot + 0.5f), 0, 4095);
      if (REVERSE_KNOB) position = 4095 - position;
      const int duty = position <= 24 ? 0 : position >= 4071 ? MAX_DUTY
                                     : (position * MAX_DUTY + 2047) / 4095;
      if (duty != previousDuty) {
        setDuty(duty);
        previousDuty = duty;
      }
      nextPot = now + 20000;
    }
    block.times[count] = esp_timer_get_time();
    block.millivolts[count] = analogReadMilliVolts(SENSE_PIN);
    if (++count == BLOCK_SIZE) {
      block.gap = block.gap || missingBlock;
      missingBlock = xQueueSend(queue, &block, 0) != pdTRUE;
      count = 0;
      block.gap = false;
    }
  }
}

void send(const Block &block) {
  Serial.printf("{\"type\":\"samples\",\"seq\":%lu", static_cast<unsigned long>(sequence++));
  if (block.gap) Serial.print(F(",\"discontinuity\":true"));
  Serial.print(F(",\"times\":["));
  for (size_t i = 0; i < BLOCK_SIZE; ++i) {
    Serial.printf("%s%.6f", i ? "," : "", block.times[i] / 1000000.0);
  }
  Serial.print(F("],\"values\":["));
  for (size_t i = 0; i < BLOCK_SIZE; ++i) {
    // The equal 1 kΩ resistors halve GPIO25 voltage at GPIO34.
    Serial.printf("%s[%.4f]", i ? "," : "", block.millivolts[i] * 0.002);
  }
  Serial.println(F("]}"));
}

void fatal(const char *description) {
  Serial.println(description);
  for (;;) delay(1000);
}
}  // namespace

void setup() {
  Serial.begin(BAUD);
  pinMode(POT_POWER_PIN, OUTPUT);
  digitalWrite(POT_POWER_PIN, HIGH);
  pinMode(POT_PIN, INPUT);
  pinMode(SENSE_PIN, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(POT_PIN, ADC_11db);
  analogSetPinAttenuation(SENSE_PIN, ADC_11db);
  if (!configurePwm()) fatal("Could not start 100 Hz PWM on GPIO25.");
  setDuty(0);
  queue = xQueueCreate(8, sizeof(Block));
  if (!queue || xTaskCreate(acquire, "capture", 4096, nullptr, 3, &sampler) != pdPASS) {
    fatal("Could not allocate PWM acquisition task.");
  }
  esp_timer_create_args_t args{};
  args.callback = requestSample;
  args.dispatch_method = ESP_TIMER_TASK;
  args.name = "scope_pwm";
  if (esp_timer_create(&args, &timer) != ESP_OK ||
      esp_timer_start_periodic(timer, SAMPLE_US) != ESP_OK) {
    fatal("Could not start PWM acquisition timer.");
  }
  hello();
}

void loop() {
  Block block;
  if (xQueueReceive(queue, &block, pdMS_TO_TICKS(20)) == pdTRUE) send(block);
  if (millis() - previousHello >= 2000) hello();
}
