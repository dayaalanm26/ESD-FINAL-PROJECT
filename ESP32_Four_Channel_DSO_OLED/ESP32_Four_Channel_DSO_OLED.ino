// ESP32 DevKit / WROOM-32, four-channel serial DSO and 128x32 I2C OLED.
// Arduino libraries: Adafruit SSD1306 and Adafruit GFX Library.
// OLED: VCC -> 3.3 V, GND -> GND, SDA -> GPIO21, SCL -> GPIO22.
#include <Arduino.h>
#include <Adafruit_SSD1306.h>
#include <Wire.h>
#include <esp_arduino_version.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>
#include <freertos/task.h>
#include <stdio.h>
#include <string.h>

namespace {
constexpr uint8_t ADS_ADDRESS = 0x48;  // ADDR tied to GND
// Set false to use the ESP32 ADC1 pins in the wiring guide.
constexpr bool USE_ADS1115 = false;
constexpr uint8_t POT_ADC_PIN = 35;
// Enable only for a 10 kΩ pot with its high-side leg wired to GPIO26.
constexpr bool POWER_10K_POT_FROM_GPIO26 = false;
constexpr uint8_t POT_POWER_PIN = 26;
constexpr uint8_t LED_PWM_PIN = 25;
constexpr uint32_t LED_PWM_HZ = 1000;
constexpr uint8_t LED_PWM_BITS = 10;
constexpr uint16_t LED_MAX_DUTY = (1U << LED_PWM_BITS) - 1U;
constexpr uint8_t LEGACY_LED_CHANNEL = 0;
// Adjust to the CH2 readings at the two ends of the knob if needed.
constexpr float POT_OFF_V = USE_ADS1115 ? 0.03f : 0.15f;
constexpr float POT_FULL_V = USE_ADS1115 ? 3.24f : 3.00f;
constexpr float LED_FILTER_WEIGHT = 0.20f;
static_assert(POT_OFF_V < POT_FULL_V, "Set POT_FULL_V above POT_OFF_V");
constexpr uint8_t LIGHT_ADC_PIN = 32;
constexpr uint8_t SOUND_ADC_PIN = 33;
constexpr uint8_t IR_PIN = 34;   // Connect the module's DO/OUT pin.
// Select which pin level the app labels as detected.
constexpr bool IR_ACTIVE_LOW = true;
constexpr float IR_LOW_V = 1.0f;
constexpr float IR_HIGH_V = 2.0f;
constexpr int SDA_PIN = 21;
constexpr int SCL_PIN = 22;
constexpr uint32_t I2C_HZ = 400000;
constexpr bool ENABLE_OLED = true;
constexpr uint8_t OLED_ADDRESS = 0x3C;
constexpr uint8_t OLED_WIDTH = 128;
constexpr uint8_t OLED_HEIGHT = 32;
constexpr uint32_t OLED_REFRESH_MS = 250;
constexpr uint32_t BAUD = 921600;
constexpr uint32_t HELLO_INTERVAL_MS = 2000;
constexpr uint32_t SCAN_GAP_US = 25000;
constexpr uint8_t ROWS_PER_PACKET = 4;

constexpr uint8_t CONVERSION_REG = 0x00;
constexpr uint8_t CONFIG_REG = 0x01;
constexpr uint16_t START_OR_READY = 0x8000;
constexpr uint16_t SINGLE_ENDED_MUX = 0x4000;
constexpr uint16_t PGA_4V096 = 0x0200;
constexpr uint16_t SINGLE_SHOT = 0x0100;
constexpr uint16_t RATE_860_SPS = 0x00E0;
constexpr uint16_t COMPARATOR_OFF = 0x0003;
constexpr double VOLTS_PER_COUNT = 4.096 / 32768.0;

struct SampleRow {
  int64_t timestampUs;
  float light;
  float potentiometer;
  float sound;
  float ir;
};

struct OledSnapshot {
  SampleRow readings;
  uint16_t duty;
  bool pwmReady;
  bool valid;
};

bool scan(SampleRow &row);
void sendHello();
void publishOled(const SampleRow *readings);

SampleRow block[ROWS_PER_PACKET];
size_t rowCount = 0;
uint32_t sequenceNumber = 0;
uint32_t lastHelloMs = 0;
int64_t previousScanUs = 0;
bool gapPending = true;
bool wireReady = false;
bool ledPwmReady = false;
uint16_t ledDuty = UINT16_MAX;
float filteredPotVolts = 0.0f;
bool irAdsHigh = false;
const char *previousStatus = nullptr;
Adafruit_SSD1306 oled(OLED_WIDTH, OLED_HEIGHT, &Wire, -1, I2C_HZ, I2C_HZ);
QueueHandle_t oledQueue = nullptr;
SemaphoreHandle_t oledBusMutex = nullptr;
uint32_t lastOledQueuedMs = 0;

bool initI2c() {
  if (!wireReady) {
    wireReady = Wire.begin(SDA_PIN, SCL_PIN, I2C_HZ);
    if (wireReady) Wire.setTimeOut(10);
  }
  return wireReady;
}

void oledTask(void *) {
  OledSnapshot snapshot{};
  for (;;) {
    if (xQueueReceive(oledQueue, &snapshot, portMAX_DELAY) != pdTRUE) continue;
    oled.clearDisplay();
    oled.setCursor(0, 0);
    if (snapshot.valid) {
      const auto &row = snapshot.readings;
      const unsigned dutyPct = (100U * snapshot.duty + LED_MAX_DUTY / 2) / LED_MAX_DUTY;
      const bool irHigh = row.ir >= 0.5f;
      const bool detected = IR_ACTIVE_LOW ? !irHigh : irHigh;
      char line[24];
      snprintf(line, sizeof(line), "CH1 Light %.2fV", row.light);
      oled.println(line);
      if (snapshot.pwmReady) {
        snprintf(line, sizeof(line), "CH2 %.2fV LED %u%%", row.potentiometer, dutyPct);
      } else {
        snprintf(line, sizeof(line), "CH2 %.2fV LED ERR", row.potentiometer);
      }
      oled.println(line);
      snprintf(line, sizeof(line), "CH3 Sound %.2fV", row.sound);
      oled.println(line);
      snprintf(line, sizeof(line), "CH4 IR %s %s", detected ? "DETECT" : "CLEAR",
               irHigh ? "HIGH" : "LOW");
      oled.print(line);
    } else {
      oled.println(F("No sensor data"));
      oled.print(F("Check ADS1115"));
    }
    if (oledBusMutex) xSemaphoreTake(oledBusMutex, portMAX_DELAY);
    oled.display();
    if (oledBusMutex) xSemaphoreGive(oledBusMutex);
  }
}

void startOled() {
  if (!initI2c()) return;
  Wire.beginTransmission(OLED_ADDRESS);
  if (Wire.endTransmission() != 0) return;
  if (!oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS, false, false)) return;
  oled.clearDisplay();
  oled.setTextSize(1);
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextWrap(false);
  oled.setCursor(0, 0);
  oled.print(F("Waiting for samples"));
  oled.display();

  oledQueue = xQueueCreate(1, sizeof(OledSnapshot));
  if (!oledQueue) return;
  // The OLED and ADS1115 share I2C only in external-ADC mode.
  if (USE_ADS1115) oledBusMutex = xSemaphoreCreateMutex();
  if ((!USE_ADS1115 || oledBusMutex) &&
      xTaskCreate(oledTask, "DSO OLED", 4096, nullptr, 1, nullptr) == pdPASS) return;
  if (oledBusMutex) vSemaphoreDelete(oledBusMutex);
  oledBusMutex = nullptr;
  vQueueDelete(oledQueue);
  oledQueue = nullptr;
}

void publishOled(const SampleRow *readings) {
  if (!oledQueue) return;
  const uint32_t now = millis();
  if (now - lastOledQueuedMs < OLED_REFRESH_MS) return;
  lastOledQueuedMs = now;
  OledSnapshot snapshot{};
  if (readings) snapshot.readings = *readings;
  snapshot.duty = ledPwmReady ? ledDuty : 0;
  snapshot.pwmReady = ledPwmReady;
  snapshot.valid = readings != nullptr;
  xQueueOverwrite(oledQueue, &snapshot);
}

bool initLedPwm() {
  pinMode(LED_PWM_PIN, OUTPUT);
  digitalWrite(LED_PWM_PIN, LOW);
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  return ledcAttach(LED_PWM_PIN, LED_PWM_HZ, LED_PWM_BITS);
#else
  if (ledcSetup(LEGACY_LED_CHANNEL, LED_PWM_HZ, LED_PWM_BITS) <= 0) return false;
  ledcAttachPin(LED_PWM_PIN, LEGACY_LED_CHANNEL);
  return true;
#endif
}

void setLedDuty(uint16_t duty) {
  if (!ledPwmReady || duty == ledDuty) return;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(LED_PWM_PIN, duty);
#else
  ledcWrite(LEGACY_LED_CHANNEL, duty);
#endif
  ledDuty = duty;
}

void updateLed(float potVolts) {
  // Constrain before filtering to keep the two endpoints responsive.
  const float bounded = constrain(potVolts, POT_OFF_V, POT_FULL_V);
  filteredPotVolts += LED_FILTER_WEIGHT * (bounded - filteredPotVolts);
  const float fraction = (filteredPotVolts - POT_OFF_V) / (POT_FULL_V - POT_OFF_V);
  setLedDuty(static_cast<uint16_t>(fraction * LED_MAX_DUTY + 0.5f));
}

bool readRegister(uint8_t reg, uint16_t &value) {
  Wire.beginTransmission(ADS_ADDRESS);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(ADS_ADDRESS, static_cast<size_t>(2)) != 2) return false;
  const int highByte = Wire.read();
  const int lowByte = Wire.read();
  if (highByte < 0 || lowByte < 0) return false;
  value = static_cast<uint16_t>((highByte << 8) | lowByte);
  return true;
}

bool readInput(uint8_t channel, int16_t &counts) {
  const uint16_t config = START_OR_READY | SINGLE_ENDED_MUX |
      (static_cast<uint16_t>(channel) << 12) | PGA_4V096 |
      SINGLE_SHOT | RATE_860_SPS | COMPARATOR_OFF;
  Wire.beginTransmission(ADS_ADDRESS);
  Wire.write(CONFIG_REG);
  Wire.write(static_cast<uint8_t>(config >> 8));
  Wire.write(static_cast<uint8_t>(config));
  if (Wire.endTransmission() != 0) return false;

  const int64_t deadlineUs = esp_timer_get_time() + 8000;
  delayMicroseconds(1350);
  uint16_t status;
  do {
    if (!readRegister(CONFIG_REG, status)) return false;
    if ((status & START_OR_READY) != 0) {
      uint16_t raw;
      if (!readRegister(CONVERSION_REG, raw)) return false;
      counts = static_cast<int16_t>(raw);
      return true;
    }
    delayMicroseconds(200);
  } while (esp_timer_get_time() < deadlineUs);
  return false;
}

bool scan(SampleRow &row) {
  const int64_t startUs = esp_timer_get_time();
  if (USE_ADS1115) {
    int16_t light, potentiometer, sound, ir;
    if (!readInput(0, light) || !readInput(1, potentiometer) ||
        !readInput(2, sound) || !readInput(3, ir)) return false;
    row.light = light * VOLTS_PER_COUNT;
    row.potentiometer = potentiometer * VOLTS_PER_COUNT;
    row.sound = sound * VOLTS_PER_COUNT;
    // Retain the comparator state through ambiguous mid-band samples.
    const float irVolts = ir * VOLTS_PER_COUNT;
    irAdsHigh = irVolts >= IR_HIGH_V || (irVolts > IR_LOW_V && irAdsHigh);
    row.ir = irAdsHigh ? 1.0f : 0.0f;
  } else {
    row.light = analogReadMilliVolts(LIGHT_ADC_PIN) / 1000.0f;
    row.potentiometer = analogReadMilliVolts(POT_ADC_PIN) / 1000.0f;
    row.sound = analogReadMilliVolts(SOUND_ADC_PIN) / 1000.0f;
    row.ir = digitalRead(IR_PIN) == HIGH ? 1.0f : 0.0f;
  }
  row.timestampUs = startUs + (esp_timer_get_time() - startUs) / 2;
  return true;
}

void sendHello() {
  Serial.print(F("{\"type\":\"hello\",\"protocol\":1,"
                   "\"device\":\"ESP32 Four Sensors\",\"channels\":["
                   "{\"name\":\"Light\",\"unit\":\"V\"},"
                   "{\"name\":\"Potentiometer\",\"unit\":\"V\"},"
                   "{\"name\":\"Sound\",\"unit\":\"V\"},"
                   "{\"name\":\"IR Output\",\"unit\":\"logic\",\"active_low\":"));
  Serial.println(IR_ACTIVE_LOW ? F("true}]}") : F("false}]}"));
  lastHelloMs = millis();
}

void sendStatus(const char *status, bool repeat) {
  if (!repeat && previousStatus && strcmp(previousStatus, status) == 0) return;
  Serial.printf("{\"type\":\"status\",\"code\":\"%s\"}\n", status);
  previousStatus = status;
}

void sendBlock() {
  Serial.printf("{\"type\":\"samples\",\"seq\":%lu",
                static_cast<unsigned long>(sequenceNumber++));
  if (gapPending) Serial.print(F(",\"discontinuity\":true"));
  Serial.print(F(",\"times\":["));
  for (size_t row = 0; row < rowCount; ++row) {
    const uint64_t timeUs = static_cast<uint64_t>(block[row].timestampUs);
    Serial.printf("%s%llu.%06lu", row == 0 ? "" : ",",
                  static_cast<unsigned long long>(timeUs / 1000000),
                  static_cast<unsigned long>(timeUs % 1000000));
  }
  Serial.print(F("],\"values\":["));
  for (size_t row = 0; row < rowCount; ++row) {
    if (row != 0) Serial.print(',');
    const SampleRow &sample = block[row];
    Serial.printf("[%.6f,%.6f,%.6f,%.0f]",
                  sample.light, sample.potentiometer, sample.sound, sample.ir);
  }
  Serial.println(F("]}"));
  rowCount = 0;
  gapPending = false;
}
}  // namespace

void setup() {
  Serial.begin(BAUD);
  filteredPotVolts = POT_OFF_V;
  ledPwmReady = initLedPwm();
  setLedDuty(0);
  if (POWER_10K_POT_FROM_GPIO26) {
    pinMode(POT_POWER_PIN, OUTPUT);
    digitalWrite(POT_POWER_PIN, HIGH);
  }
  sendHello();
  if (!USE_ADS1115) {
    analogReadResolution(12);
    analogSetPinAttenuation(LIGHT_ADC_PIN, ADC_11db);
    analogSetPinAttenuation(POT_ADC_PIN, ADC_11db);
    analogSetPinAttenuation(SOUND_ADC_PIN, ADC_11db);
    pinMode(IR_PIN, INPUT);
  }
  if (ENABLE_OLED) startOled();
}

void loop() {
  const bool announce = millis() - lastHelloMs >= HELLO_INTERVAL_MS;
  if (announce) sendHello();

  if (USE_ADS1115 && !initI2c()) {
    publishOled(nullptr);
    sendStatus("ads1115_missing", announce);
    delay(250);
    return;
  }

  if (!USE_ADS1115) delay(4);
  SampleRow row{};
  if (oledBusMutex) xSemaphoreTake(oledBusMutex, portMAX_DELAY);
  const bool received = scan(row);
  if (oledBusMutex) xSemaphoreGive(oledBusMutex);
  if (!received) {
    rowCount = 0;
    gapPending = true;
    filteredPotVolts = POT_OFF_V;
    setLedDuty(0);
    publishOled(nullptr);
    sendStatus("ads1115_missing", announce);
    delay(100);
    return;
  }
  updateLed(row.potentiometer);
  publishOled(&row);
  sendStatus(ledPwmReady ? "ready" : "pwm_unavailable", announce);

  if (previousScanUs > 0 && row.timestampUs - previousScanUs > SCAN_GAP_US) {
    rowCount = 0;
    gapPending = true;
  }
  previousScanUs = row.timestampUs;
  block[rowCount++] = row;
  if (rowCount == ROWS_PER_PACKET) {
    sendBlock();
    delay(1);
  }
}
