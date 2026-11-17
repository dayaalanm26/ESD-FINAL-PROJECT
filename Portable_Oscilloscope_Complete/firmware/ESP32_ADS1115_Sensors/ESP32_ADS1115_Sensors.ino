#include <Arduino.h>
#include <DHT.h>
#include <Wire.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

namespace {
constexpr uint8_t ADS_ADDRESS = 0x48;  // ADDR tied to GND
constexpr bool USE_ADS1115 = false;    // Direct ESP32 wiring
constexpr int DHT_PIN = 27;
constexpr uint8_t LIGHT_ADC_PIN = 32;
constexpr uint8_t SOUND_ADC_PIN = 33;
constexpr uint8_t IR_ADC_PIN = 34;
constexpr int SDA_PIN = 21;
constexpr int SCL_PIN = 22;

constexpr uint32_t BAUD = 921600;
constexpr uint32_t HELLO_INTERVAL_MS = 2000;
constexpr uint32_t SCAN_GAP_US = 25000;
constexpr uint32_t TEMP_INTERVAL_MS = 2500;
constexpr uint32_t TEMP_VALID_MS = 7000;
constexpr uint8_t ROWS_PER_PACKET = 6;

constexpr uint8_t CONVERSION_REG = 0x00;
constexpr uint8_t CONFIG_REG = 0x01;
constexpr uint16_t START_OR_READY = 0x8000;
constexpr uint16_t SINGLE_ENDED_MUX = 0x4000;
constexpr uint16_t PGA_4V096 = 0x0200;
constexpr uint16_t SINGLE_SHOT = 0x0100;
constexpr uint16_t RATE_860_SPS = 0x00E0;
constexpr uint16_t COMPARATOR_OFF = 0x0003;
constexpr double VOLTS_PER_COUNT = 4.096 / 32768.0;

struct Temperature {
  float celsius;
  uint32_t sampledAt;
};

struct SampleRow {
  int64_t timestampUs;
  float light;
  float celsius;
  float sound;
  float ir;
};

bool scan(SampleRow &row);
void sendHello();

DHT dht(DHT_PIN, DHT11);
QueueHandle_t temperatureUpdates = nullptr;

SampleRow block[ROWS_PER_PACKET];
size_t rowCount = 0;
uint32_t sequenceNumber = 0;
uint32_t lastHelloMs = 0;
int64_t previousScanUs = 0;
bool gapPending = true;
bool wireReady = false;
bool includeTemperature = false;
const char *previousStatus = nullptr;

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

void measureTemperature(void *) {
  dht.begin();
  vTaskDelay(pdMS_TO_TICKS(TEMP_INTERVAL_MS));

  for (;;) {
    const float celsius = dht.readTemperature();
    if (isfinite(celsius)) {
      const Temperature fresh{celsius, millis()};
      xQueueOverwrite(temperatureUpdates, &fresh);
    }
    vTaskDelay(pdMS_TO_TICKS(TEMP_INTERVAL_MS));
  }
}

bool scan(SampleRow &row) {
  const int64_t startUs = esp_timer_get_time();

  if (USE_ADS1115) {
    int16_t light, sound, ir;
    if (!readInput(0, light) || !readInput(2, sound) ||
        !readInput(3, ir)) return false;

    row.light = light * VOLTS_PER_COUNT;
    row.sound = sound * VOLTS_PER_COUNT;
    row.ir = ir * VOLTS_PER_COUNT;
  } else {
    row.light = analogReadMilliVolts(LIGHT_ADC_PIN) / 1000.0f;
    row.sound = analogReadMilliVolts(SOUND_ADC_PIN) / 1000.0f;
    row.ir = analogReadMilliVolts(IR_ADC_PIN) / 1000.0f;
  }

  row.timestampUs = startUs + (esp_timer_get_time() - startUs) / 2;
  return true;
}

void sendHello() {
  Serial.print(F("{\"type\":\"hello\",\"protocol\":1,"
                 "\"device\":\"ESP32 Four Sensors\",\"channels\":["
                 "{\"name\":\"Light\",\"unit\":\"V\"}"));

  if (includeTemperature) {
    Serial.print(F(",{\"name\":\"DHT11 Temperature\",\"unit\":\"\\u00b0C\"}"));
  }

  Serial.println(F(",{\"name\":\"Sound\",\"unit\":\"V\"},"
                   "{\"name\":\"IR level\",\"unit\":\"V\"}]}"));

  lastHelloMs = millis();
}

void sendStatus(const char *status, bool repeat) {
  if (!repeat && previousStatus &&
      strcmp(previousStatus, status) == 0) return;

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
    Serial.printf("[%.6f", sample.light);
    if (includeTemperature) Serial.printf(",%.2f", sample.celsius);
    Serial.printf(",%.6f,%.6f]", sample.sound, sample.ir);
  }

  Serial.println(F("]}"));
  rowCount = 0;
  gapPending = false;
}
}  // namespace

void setup() {
  Serial.begin(BAUD);
  sendHello();

  if (!USE_ADS1115) {
    analogReadResolution(12);
    analogSetPinAttenuation(LIGHT_ADC_PIN, ADC_11db);
    analogSetPinAttenuation(SOUND_ADC_PIN, ADC_11db);
    analogSetPinAttenuation(IR_ADC_PIN, ADC_11db);
  }

  temperatureUpdates = xQueueCreate(1, sizeof(Temperature));
  if (!temperatureUpdates ||
      xTaskCreate(measureTemperature, "dht11", 3072, nullptr, 1, nullptr) != pdPASS) {
    Serial.println(F("Cannot start the DHT11 temperature task."));
    for (;;) delay(1000);
  }
}

void loop() {
  Temperature reading{};
  const bool fresh = xQueuePeek(temperatureUpdates, &reading, 0) == pdTRUE &&
      millis() - reading.sampledAt <= TEMP_VALID_MS;

  const bool schemaChanged = fresh != includeTemperature;
  const bool announce = schemaChanged || millis() - lastHelloMs >= HELLO_INTERVAL_MS;

  if (schemaChanged) {
    includeTemperature = fresh;
    rowCount = 0;
    gapPending = true;
  }
  if (announce) sendHello();

  if (USE_ADS1115 && !wireReady) {
    wireReady = Wire.begin(SDA_PIN, SCL_PIN, 400000);
    if (!wireReady) {
      sendStatus("ads1115_missing", announce);
      delay(250);
      return;
    }
    Wire.setTimeOut(10);
  }

  if (!USE_ADS1115) delay(4);

  SampleRow row{};
  if (!scan(row)) {
    rowCount = 0;
    gapPending = true;
    sendStatus("ads1115_missing", announce);
    delay(100);
    return;
  }

  row.celsius = reading.celsius;
  sendStatus(fresh ? "ready" : "dht11_waiting", announce);

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