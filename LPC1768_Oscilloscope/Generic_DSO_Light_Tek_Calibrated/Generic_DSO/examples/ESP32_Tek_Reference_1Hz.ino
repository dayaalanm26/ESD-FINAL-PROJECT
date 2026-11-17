/*
 * ESP32 -> Generic DSO — measured 1 Hz square wave (ADC1 / GPIO34)
 *
 * GPIO25 ---+--- oscilloscope CH1 tip (scope GND -> ESP32 GND)
 *           |
 *          10k (R1)
 *           |
 * GPIO34 ---+--- physical ADC input, measures ~0 to 1.65 V
 *           |
 *          10k (R2)
 *           |
 *          GND
 *
 * By design, transmitted ADC channel values are scaled 2x to undo divider.
 * GPIO25 square wave is 0/3.3 V nominal (high depends on actual 3.3V rail).
 * Scope analog values depend on board ADC calibration, noise and wiring.
 * Use only ESP32 3.3V signals; NEVER connect outside voltage to an ESP32 pin.
 * USB: 115200 baud, Serial 8N1. Requires Arduino-ESP32 board support.
 * This sender uses actual timestamps, not fictional uniform sampling.
 */
#include <Arduino.h>
#include "esp_timer.h"

constexpr int OUTPUT_PIN = 25;
constexpr int INPUT_PIN = 34;
constexpr uint32_t BAUD = 115200;
constexpr int64_t HALF_PERIOD_US = 500000;  // nominal 1 Hz, 50% duty
constexpr int64_t SAMPLE_US = 20000;        // nominal 50 samples/sec
constexpr size_t BLOCK = 10;
constexpr float DIVIDER_CORRECTION = 2.0f; // two equal 10k resistors

uint32_t sequenceNumber = 0;
int64_t nextSampleUs = 0;
int64_t lastHelloUs = 0;
int lastOutput = -1;
bool lostTime = false;
double timesBuffer[BLOCK];
float valuesBuffer[BLOCK];
size_t countBuffer = 0;

void sendHello() {
  Serial.println(F("{\"type\":\"hello\",\"protocol\":1,"
                   "\"device\":\"ESP32 GPIO25 - GPIO34 ADC (10k divider)\","
                   "\"channels\":[{\"name\":\"GPIO34 voltage\",\"unit\":\"V\"}]}"));
}

void sendSamples() {
  if (!countBuffer) return;
  Serial.print(F("{\"type\":\"samples\",\"seq\":"));
  Serial.print(sequenceNumber++);
  if (lostTime) Serial.print(F(",\"discontinuity\":true"));
  Serial.print(F(",\"times\":["));
  for (size_t i = 0; i < countBuffer; ++i) {
    if (i) Serial.print(',');
    Serial.print(timesBuffer[i], 6);
  }
  Serial.print(F("],\"values\":["));
  for (size_t i = 0; i < countBuffer; ++i) {
    if (i) Serial.print(',');
    Serial.print('[');
    Serial.print(valuesBuffer[i], 5);
    Serial.print(']');
  }
  Serial.println(F("]}"));
  countBuffer = 0;
  lostTime = false;
}

void setup() {
  Serial.begin(BAUD);
  pinMode(OUTPUT_PIN, OUTPUT);
  digitalWrite(OUTPUT_PIN, LOW);
  pinMode(INPUT_PIN, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(INPUT_PIN, ADC_11db);
  delay(250);
  sendHello();
  int64_t now = esp_timer_get_time();
  lastHelloUs = now;
  nextSampleUs = now;
}

void loop() {
  const int64_t now = esp_timer_get_time();
  // State is derived from the monotonic clock, avoiding cumulative delay drift.
  const int level = ((now / HALF_PERIOD_US) & 1LL) ? HIGH : LOW;
  if (level != lastOutput) {
    digitalWrite(OUTPUT_PIN, level);
    lastOutput = level;
  }
  if (now - lastHelloUs >= 10000000LL) {
    sendHello();
    lastHelloUs = now;
  }
  if (now < nextSampleUs) return;
  if (now - nextSampleUs > SAMPLE_US * 2) {
    // Do not invent missed ADC samples or a uniform 50 Hz sample clock.
    lostTime = true;
    countBuffer = 0;
  }
  nextSampleUs += SAMPLE_US;
  if (nextSampleUs < now) nextSampleUs = now + SAMPLE_US;
  const int64_t timestamp = esp_timer_get_time();
  const uint32_t millivolts = analogReadMilliVolts(INPUT_PIN);
  // Recovered GPIO25 voltage, assuming the equal-resistor divider described.
  timesBuffer[countBuffer] = (double)timestamp / 1000000.0;
  valuesBuffer[countBuffer] = (float)millivolts * 0.001f * DIVIDER_CORRECTION;
  ++countBuffer;
  if (countBuffer >= BLOCK) sendSamples();
}
