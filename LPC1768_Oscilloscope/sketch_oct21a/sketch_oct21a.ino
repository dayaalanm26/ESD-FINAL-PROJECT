#include <Arduino.h>
#include "esp_timer.h"

const int OUTPUT_PIN = 25;
const int INPUT_PIN  = 34;

uint32_t sequenceNumber = 0;
uint32_t lastHello = 0;
uint32_t lastToggle = 0;
bool outputHigh = false;

void sendHello() {
  Serial.println(
    "{\"type\":\"hello\",\"protocol\":1,"
    "\"device\":\"ESP32 Square Wave\","
    "\"channels\":[{\"name\":\"GPIO34\",\"unit\":\"V\"}]}"
  );
}

void setup() {
  Serial.begin(115200);

  pinMode(OUTPUT_PIN, OUTPUT);
  digitalWrite(OUTPUT_PIN, LOW);

  pinMode(INPUT_PIN, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(INPUT_PIN, ADC_11db);

  delay(1000);

  sendHello();
  lastHello = millis();
  lastToggle = millis();
}

void loop() {
  uint32_t now = millis();

  // Toggle every 500 ms: approximately 1 Hz square wave.
  if (now - lastToggle >= 500) {
    lastToggle = now;
    outputHigh = !outputHigh;
    digitalWrite(OUTPUT_PIN, outputHigh);
  }

  // Repeat identification so Python can connect at any time.
  if (now - lastHello >= 2000) {
    sendHello();
    lastHello = now;
  }

  // Measure the physical signal at the potentiometer wiper.
  int64_t sampleTimeUs = esp_timer_get_time();
  float voltage = analogReadMilliVolts(INPUT_PIN) / 1000.0f;

  Serial.printf(
    "{\"type\":\"samples\",\"seq\":%lu,"
    "\"times\":[%.6f],\"values\":[[%.3f]]}\n",
    (unsigned long)sequenceNumber++,
    sampleTimeUs / 1000000.0,
    voltage
  );

  delay(20);  // Approximately 50 measurements per second.
}