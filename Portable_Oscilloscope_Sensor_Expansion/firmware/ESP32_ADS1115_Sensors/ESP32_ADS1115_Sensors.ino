#include <Arduino.h>
#include <Wire.h>
#include <esp_timer.h>
#include <stdio.h>

namespace {
constexpr uint8_t ADS_ADDRESS = 0x48;  // ADDR tied to GND
constexpr int SDA_PIN = 21;
constexpr int SCL_PIN = 22;
constexpr uint32_t BAUD = 921600;
constexpr uint32_t HELLO_INTERVAL_MS = 2000;
constexpr uint32_t SCAN_GAP_US = 25000;
constexpr uint8_t ROWS_PER_PACKET = 6;

struct SensorInput {
  uint8_t adcChannel;
  const char *label;
};

// Remove entries for unused inputs. List order determines the DSO channel order.
constexpr SensorInput INPUTS[] = {
    {0, "AIN0 Light"},
    {1, "AIN1 Thermistor"},
    {2, "AIN2 Sound envelope"},
    {3, "AIN3 IR level"},
};
constexpr size_t CHANNEL_COUNT = sizeof(INPUTS) / sizeof(INPUTS[0]);
static_assert(CHANNEL_COUNT >= 1 && CHANNEL_COUNT <= 4, "Enable one to four inputs");

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
  int16_t counts[CHANNEL_COUNT];
};

SampleRow block[ROWS_PER_PACKET];
size_t rowCount = 0;
uint32_t sequenceNumber = 0;
uint32_t lastHelloMs = 0;
int64_t previousScanUs = 0;
bool gapPending = false;
bool wireReady = false;
bool announced = false;

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
  for (size_t i = 0; i < CHANNEL_COUNT; ++i) {
    if (!readInput(INPUTS[i].adcChannel, row.counts[i])) return false;
  }
  row.timestampUs = startUs + (esp_timer_get_time() - startUs) / 2;
  return true;
}

void sendHello() {
  Serial.print(F("{\"type\":\"hello\",\"protocol\":1,"
                 "\"device\":\"ESP32 ADS1115 Sensors\",\"channels\":["));
  for (size_t i = 0; i < CHANNEL_COUNT; ++i) {
    if (i != 0) Serial.print(',');
    Serial.printf("{\"name\":\"%s\",\"unit\":\"V\"}", INPUTS[i].label);
  }
  Serial.println(F("]}"));
  lastHelloMs = millis();
  announced = true;
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
    Serial.print('[');
    for (size_t channel = 0; channel < CHANNEL_COUNT; ++channel) {
      Serial.printf("%s%.6f", channel == 0 ? "" : ",",
                    block[row].counts[channel] * VOLTS_PER_COUNT);
    }
    Serial.print(']');
  }
  Serial.println(F("]}"));
  rowCount = 0;
  gapPending = false;
}
}  // namespace

void setup() {
  Serial.begin(BAUD);
}

void loop() {
  if (!wireReady) {
    wireReady = Wire.begin(SDA_PIN, SCL_PIN, 400000);
    if (!wireReady) {
      delay(250);
      return;
    }
    Wire.setTimeOut(10);
  }

  SampleRow row;
  if (!scan(row)) {
    // Discard incomplete scans and queued rows from before the fault.
    rowCount = 0;
    gapPending = announced;
    delay(100);
    return;
  }

  if (previousScanUs > 0 && row.timestampUs - previousScanUs > SCAN_GAP_US) {
    rowCount = 0;
    gapPending = true;
  }
  previousScanUs = row.timestampUs;
  if (!announced || millis() - lastHelloMs >= HELLO_INTERVAL_MS) sendHello();

  block[rowCount++] = row;
  if (rowCount == ROWS_PER_PACKET) {
    sendBlock();
    delay(1);  // Yield to other ESP32 tasks between packets.
  }
}
