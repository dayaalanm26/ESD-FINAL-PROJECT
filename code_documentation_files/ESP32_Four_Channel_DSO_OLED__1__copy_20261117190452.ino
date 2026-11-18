/**
 * @file ESP32_Four_Channel_DSO_OLED.ino
 * @brief Four-channel serial Digital Storage Oscilloscope (DSO) and 128x32 I2C OLED firmware for ESP32.
 * @details Acquires four channels (Light, Potentiometer, Sound, and IR digital output) via either
 *          the internal ESP32 12-bit ADC or an external ADS1115 16-bit I2C ADC, streams JSON packets
 *          over UART at 921600 baud, drives a status LED via PWM, and renders live telemetry on an SSD1306 OLED.
 *
 * @note Hardware connections:
 *       - OLED: VCC -> 3.3 V, GND -> GND, SDA -> GPIO21, SCL -> GPIO22
 *       - Internal ADC mode: Light -> GPIO32, Potentiometer -> GPIO35, Sound -> GPIO33, IR -> GPIO34
 *       - LED PWM output -> GPIO25
 * @see Adafruit_SSD1306, Adafruit GFX Library
 */

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

/** @name Hardware & ADC Configuration Constants */
///@{
constexpr uint8_t ADS_ADDRESS = 0x48;                  /**< I2C address of ADS1115 with ADDR pin tied to GND. */
constexpr bool USE_ADS1115 = false;                    /**< Set true to use external ADS1115; false uses internal ESP32 ADC1 pins. */
constexpr uint8_t POT_ADC_PIN = 35;                    /**< ESP32 ADC1 pin connected to potentiometer wiper (CH2). */
constexpr bool POWER_10K_POT_FROM_GPIO26 = false;      /**< Enable only when powering a 10 kOhm pot high-side leg from GPIO26. */
constexpr uint8_t POT_POWER_PIN = 26;                  /**< Optional GPIO supply pin for 10 kOhm potentiometer. */
constexpr uint8_t LED_PWM_PIN = 25;                    /**< GPIO pin driving indicator LED via LEDC PWM. */
constexpr uint32_t LED_PWM_HZ = 1000;                  /**< LED PWM carrier frequency in Hz. */
constexpr uint8_t LED_PWM_BITS = 10;                   /**< LED PWM duty resolution in bits (0..1023). */
constexpr uint16_t LED_MAX_DUTY = (1U << LED_PWM_BITS) - 1U; /**< Maximum raw LEDC duty cycle count. */
constexpr uint8_t LEGACY_LED_CHANNEL = 0;              /**< LEDC channel index for ESP32 Arduino Core < 3.0. */
constexpr float POT_OFF_V = USE_ADS1115 ? 0.03f : 0.15f;     /**< Potentiometer voltage threshold corresponding to 0% LED duty. */
constexpr float POT_FULL_V = USE_ADS1115 ? 3.24f : 3.00f;    /**< Potentiometer voltage threshold corresponding to 100% LED duty. */
constexpr float LED_FILTER_WEIGHT = 0.20f;             /**< Exponential moving average smoothing factor for LED dimming. */
static_assert(POT_OFF_V < POT_FULL_V, "Set POT_FULL_V above POT_OFF_V");

constexpr uint8_t LIGHT_ADC_PIN = 32;                  /**< ESP32 ADC1 pin connected to analog light sensor (CH1). */
constexpr uint8_t SOUND_ADC_PIN = 33;                  /**< ESP32 ADC1 pin connected to analog sound sensor (CH3). */
constexpr uint8_t IR_PIN = 34;                         /**< ESP32 digital input pin connected to IR module DO/OUT (CH4). */
constexpr bool IR_ACTIVE_LOW = true;                   /**< True if IR sensor pulls output LOW upon obstacle detection. */
constexpr float IR_LOW_V = 1.0f;                       /**< Hysteresis lower threshold (V) when reading IR via ADS1115. */
constexpr float IR_HIGH_V = 2.0f;                      /**< Hysteresis upper threshold (V) when reading IR via ADS1115. */
///@}

/** @name I2C, OLED, and Serial Protocol Constants */
///@{
constexpr int SDA_PIN = 21;                            /**< I2C data pin (SDA). */
constexpr int SCL_PIN = 22;                            /**< I2C clock pin (SCL). */
constexpr uint32_t I2C_HZ = 400000;                    /**< I2C fast-mode bus clock frequency in Hz. */
constexpr bool ENABLE_OLED = true;                     /**< Enable or disable local SSD1306 OLED telemetry display. */
constexpr uint8_t OLED_ADDRESS = 0x3C;                 /**< 7-bit I2C slave address of the 128x32 SSD1306 OLED. */
constexpr uint8_t OLED_WIDTH = 128;                    /**< OLED horizontal pixel resolution. */
constexpr uint8_t OLED_HEIGHT = 32;                    /**< OLED vertical pixel resolution. */
constexpr uint32_t OLED_REFRESH_MS = 250;              /**< Minimum interval between OLED frame updates in milliseconds. */
constexpr uint32_t BAUD = 921600;                      /**< Serial UART baud rate for host DSO communication. */
constexpr uint32_t HELLO_INTERVAL_MS = 2000;           /**< Keepalive interval for transmitting 'hello' metadata packets (ms). */
constexpr uint32_t SCAN_GAP_US = 25000;                /**< Sample interval threshold (us) above which a discontinuity is flagged. */
constexpr uint8_t ROWS_PER_PACKET = 4;                 /**< Number of sample rows batched into each JSON 'samples' frame. */
///@}

/** @name ADS1115 Register & Configuration Bitmasks */
///@{
constexpr uint8_t CONVERSION_REG = 0x00;               /**< ADS1115 16-bit conversion result register address. */
constexpr uint8_t CONFIG_REG = 0x01;                   /**< ADS1115 16-bit configuration register address. */
constexpr uint16_t START_OR_READY = 0x8000;            /**< OS bit: write 1 to start single-shot; reads 1 when ready. */
constexpr uint16_t SINGLE_ENDED_MUX = 0x4000;          /**< Base MUX setting for single-ended AINx vs GND measurements. */
constexpr uint16_t PGA_4V096 = 0x0200;                 /**< Programmable gain amplifier setting for +/-4.096 V full-scale range. */
constexpr uint16_t SINGLE_SHOT = 0x0100;               /**< Operating mode bit for single-shot power-down conversion. */
constexpr uint16_t RATE_860_SPS = 0x00E0;              /**< Data rate bits selecting 860 samples per second. */
constexpr uint16_t COMPARATOR_OFF = 0x0003;            /**< Disable ADS1115 alert/ready comparator output. */
constexpr double VOLTS_PER_COUNT = 4.096 / 32768.0;    /**< LSB voltage resolution in +/-4.096 V PGA mode. */
///@}

/**
 * @struct SampleRow
 * @brief Represents a single timestamped 4-channel acquisition measurement.
 */
struct SampleRow {
  int64_t timestampUs;  /**< Sample midpoint timestamp since boot in microseconds. */
  float light;          /**< CH1: Light sensor voltage in Volts. */
  float potentiometer;  /**< CH2: Potentiometer voltage in Volts. */
  float sound;          /**< CH3: Sound sensor voltage in Volts. */
  float ir;             /**< CH4: IR sensor logic level (1.0f = HIGH, 0.0f = LOW). */
};

/**
 * @struct OledSnapshot
 * @brief Thread-safe payload passed over a FreeRTOS queue to the OLED rendering task.
 */
struct OledSnapshot {
  SampleRow readings;   /**< Latest acquired 4-channel sensor values. */
  uint32_t intervalUs;  /**< Time elapsed since the previous scan in microseconds (0 if invalid/gap). */
  uint16_t duty;        /**< Current LED PWM duty cycle count. */
  bool pwmReady;        /**< True if LEDC PWM hardware initialized properly. */
  bool valid;           /**< True if readings contain valid acquisition data; false shows wiring error. */
};

bool scan(SampleRow &row);
void sendHello();
void publishOled(const SampleRow *readings, uint32_t intervalUs);

/** @name Global Firmware State */
///@{
SampleRow block[ROWS_PER_PACKET];                      /**< Ring/batch buffer holding sample rows prior to serial transmission. */
size_t rowCount = 0;                                   /**< Current number of buffered rows in @ref block. */
uint32_t sequenceNumber = 0;                           /**< Monotonically increasing packet sequence counter. */
uint32_t lastHelloMs = 0;                              /**< Timestamp (ms) of the last transmitted 'hello' packet. */
int64_t previousScanUs = 0;                            /**< Timestamp (us) of the previous completed scan. */
bool gapPending = true;                                /**< Flags a timing discontinuity on the next transmitted packet. */
bool wireReady = false;                                /**< True once Wire I2C peripheral has been initialized. */
bool ledPwmReady = false;                              /**< True if LED PWM peripheral initialized without error. */
uint16_t ledDuty = UINT16_MAX;                         /**< Cached PWM duty cycle to avoid redundant register writes. */
float filteredPotVolts = 0.0f;                         /**< Low-pass filtered potentiometer voltage for LED control. */
bool irAdsHigh = false;                                /**< Schmitt-trigger state for ADS1115 IR channel hysteresis. */
const char *previousStatus = nullptr;                  /**< Pointer to last emitted status string for deduplication. */
Adafruit_SSD1306 oled(OLED_WIDTH, OLED_HEIGHT, &Wire, -1, I2C_HZ, I2C_HZ); /**< SSD1306 display driver instance. */
QueueHandle_t oledQueue = nullptr;                     /**< Single-item overwrite queue feeding @ref oledTask. */
SemaphoreHandle_t oledBusMutex = nullptr;              /**< Mutex protecting shared I2C bus when ADS1115 is active. */
uint32_t lastOledQueuedMs = 0;                         /**< Timestamp (ms) of last OLED queue update for rate limiting. */
///@}

/**
 * @brief Initializes the ESP32 I2C master bus if not already started.
 * @return true if I2C bus initialization succeeded, false otherwise.
 */
bool initI2c() {
  if (!wireReady) {
    wireReady = Wire.begin(SDA_PIN, SCL_PIN, I2C_HZ);
    if (wireReady) Wire.setTimeOut(10);
  }
  return wireReady;
}

/**
 * @brief FreeRTOS background task that renders telemetry snapshots onto the SSD1306 OLED.
 * @details Blocks indefinitely on @ref oledQueue and updates the 128x32 display without
 *          stalling the main real-time ADC sampling loop.
 * @param[in] param Unused FreeRTOS task parameter.
 */
void oledTask(void *) {
  OledSnapshot snapshot{};
  for (;;) {
    if (xQueueReceive(oledQueue, &snapshot, portMAX_DELAY) != pdTRUE) continue;
    oled.clearDisplay();
    oled.setCursor(0, 0);
    if (snapshot.valid) {
      const auto &row = snapshot.readings;
      const unsigned dutyPct = (100U * snapshot.duty + LED_MAX_DUTY / 2) / LED_MAX_DUTY;
      // The 128-pixel display fits 21 characters at text size 1.
      char line[22];
      snprintf(line, sizeof(line), "CH1 %.2fV CH2 %.2fV", row.light, row.potentiometer);
      oled.println(line);
      snprintf(line, sizeof(line), "CH3 %.2fV CH4 %s", row.sound,
               row.ir >= 0.5f ? "HIGH" : "LOW");
      oled.println(line);
      snprintf(line, sizeof(line), "t %.2fs", row.timestampUs / 1000000.0);
      oled.println(line);
      char interval[8] = "--";
      if (snapshot.intervalUs > 0) {
        snprintf(interval, sizeof(interval), "%.1fms", snapshot.intervalUs / 1000.0);
      }
      if (snapshot.pwmReady) {
        snprintf(line, sizeof(line), "dt %s PWM %u%%", interval, dutyPct);
      } else {
        snprintf(line, sizeof(line), "dt %s PWM ERR", interval);
      }
      oled.print(line);
    } else {
      oled.println(F("No sample data"));
      oled.print(F("Check input wiring"));
    }
    if (oledBusMutex) xSemaphoreTake(oledBusMutex, portMAX_DELAY);
    oled.display();
    if (oledBusMutex) xSemaphoreGive(oledBusMutex);
  }
}

/**
 * @brief Probes for the SSD1306 OLED on I2C, creates synchronization primitives, and spawns @ref oledTask.
 */
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

/**
 * @brief Throttles and posts the latest acquisition data to the OLED background task.
 * @param[in] readings Pointer to latest @ref SampleRow, or nullptr if acquisition failed.
 * @param[in] intervalUs Sample-to-sample interval in microseconds (0 if discontinuity occurred).
 */
void publishOled(const SampleRow *readings, uint32_t intervalUs) {
  if (!oledQueue) return;
  const uint32_t now = millis();
  if (now - lastOledQueuedMs < OLED_REFRESH_MS) return;
  lastOledQueuedMs = now;
  OledSnapshot snapshot{};
  if (readings) snapshot.readings = *readings;
  snapshot.intervalUs = intervalUs;
  snapshot.duty = ledPwmReady ? ledDuty : 0;
  snapshot.pwmReady = ledPwmReady;
  snapshot.valid = readings != nullptr;
  xQueueOverwrite(oledQueue, &snapshot);
}

/**
 * @brief Configures the ESP32 LEDC peripheral for PWM output on @ref LED_PWM_PIN.
 * @note Supports both ESP32 Arduino Core v3.x (`ledcAttach`) and v2.x (`ledcSetup`).
 * @return true if PWM setup succeeded, false otherwise.
 */
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

/**
 * @brief Updates the hardware PWM duty cycle if the value has changed.
 * @param[in] duty Target duty cycle count in range [0, @ref LED_MAX_DUTY].
 */
void setLedDuty(uint16_t duty) {
  if (!ledPwmReady || duty == ledDuty) return;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(LED_PWM_PIN, duty);
#else
  ledcWrite(LEGACY_LED_CHANNEL, duty);
#endif
  ledDuty = duty;
}

/**
 * @brief Maps the potentiometer voltage to a smoothed PWM duty cycle for the indicator LED.
 * @param[in] potVolts Measured potentiometer voltage in Volts.
 */
void updateLed(float potVolts) {
  // Constrain before filtering to keep the two endpoints responsive.
  const float bounded = constrain(potVolts, POT_OFF_V, POT_FULL_V);
  filteredPotVolts += LED_FILTER_WEIGHT * (bounded - filteredPotVolts);
  const float fraction = (filteredPotVolts - POT_OFF_V) / (POT_FULL_V - POT_OFF_V);
  setLedDuty(static_cast<uint16_t>(fraction * LED_MAX_DUTY + 0.5f));
}

/**
 * @brief Reads a 16-bit big-endian register from the ADS1115 over I2C.
 * @param[in] reg Register address (@ref CONVERSION_REG or @ref CONFIG_REG).
 * @param[out] value Retrieved 16-bit unsigned register contents.
 * @return true on successful I2C transaction, false on NACK or short read.
 */
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

/**
 * @brief Triggers a single-shot conversion on an ADS1115 single-ended channel and polls until ready.
 * @param[in] channel Single-ended input multiplexer index (0..3).
 * @param[out] counts Signed 16-bit raw ADC conversion result.
 * @return true if conversion completed within 8 ms deadline, false on timeout or I2C error.
 */
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

/**
 * @brief Acquires one complete 4-channel sample row and computes its midpoint timestamp.
 * @param[out] row Populated @ref SampleRow structure.
 * @return true if all four channels were sampled cleanly, false if ADS1115 communication failed.
 */
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

/**
 * @brief Transmits the JSON protocol v1 'hello' device identification and channel specification packet.
 */
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

/**
 * @brief Sends a JSON device status notification when the status changes or on periodic announcement.
 * @param[in] status Null-terminated status code string (e.g., `"ready"`, `"ads1115_missing"`).
 * @param[in] repeat If true, forces transmission even if @p status matches @ref previousStatus.
 */
void sendStatus(const char *status, bool repeat) {
  if (!repeat && previousStatus && strcmp(previousStatus, status) == 0) return;
  Serial.printf("{\"type\":\"status\",\"code\":\"%s\"}\n", status);
  previousStatus = status;
}

/**
 * @brief Serializes and transmits the buffered @ref ROWS_PER_PACKET samples as a JSON 'samples' frame.
 */
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

/**
 * @brief Arduino entry point: initializes Serial, PWM, ADC attenuation, OLED task, and sends initial metadata.
 */
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

/**
 * @brief Arduino main loop: periodically announces metadata, acquires sensor rows, updates LED/OLED,
 *        and streams batched JSON sample packets to the host PC.
 */
void loop() {
  const bool announce = millis() - lastHelloMs >= HELLO_INTERVAL_MS;
  if (announce) sendHello();

  if (USE_ADS1115 && !initI2c()) {
    publishOled(nullptr, 0);
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
    publishOled(nullptr, 0);
    sendStatus("ads1115_missing", announce);
    delay(100);
    return;
  }
  updateLed(row.potentiometer);
  sendStatus(ledPwmReady ? "ready" : "pwm_unavailable", announce);

  const int64_t intervalUs = previousScanUs > 0 ? row.timestampUs - previousScanUs : 0;
  if (intervalUs > SCAN_GAP_US) {
    rowCount = 0;
    gapPending = true;
  }
  // A discontinuity is not a representative sample interval.
  publishOled(&row, intervalUs <= SCAN_GAP_US ? static_cast<uint32_t>(intervalUs) : 0);
  previousScanUs = row.timestampUs;
  block[rowCount++] = row;
  if (rowCount == ROWS_PER_PACKET) {
    sendBlock();
    delay(1);
  }
}