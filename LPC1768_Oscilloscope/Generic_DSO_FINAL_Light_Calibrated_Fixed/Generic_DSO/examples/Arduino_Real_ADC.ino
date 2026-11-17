/* Actual A0 measurements for classic Arduino Uno/Nano ATmega328P.
   Output units are raw ADC counts (0..1023), not calibrated volts.
   No test waveform is generated. Use only inputs within the board ADC limits.
   Other boards: adjust ADC resolution/range and input pin explicitly.
   This polling example demonstrates connectivity, NOT precision timed DSO firmware.
*/
const unsigned N = 32;
unsigned int values[N];
unsigned long timesUs[N];
unsigned long sequence = 0, previousMicros = 0;
unsigned long long elapsedUs = 0;

void hello() {
  Serial.println(F("{\"type\":\"hello\",\"protocol\":1,\"device\":\"Arduino ATmega328P ADC\",\"channels\":[{\"name\":\"A0\",\"unit\":\"count\",\"min\":0,\"max\":1023}]}"));
}
void setup() {
  Serial.begin(115200);
  previousMicros = micros();
  delay(1500);
  hello();
}
void loop() {
  // Repeat metadata so opening the port after boot is supported.
  if (sequence % 16 == 0) hello();
  unsigned long blockStart = micros();
  elapsedUs += (unsigned long)(blockStart - previousMicros);
  previousMicros = blockStart;
  double blockSeconds = (double)elapsedUs / 1000000.0;
  for (unsigned i=0; i<N; ++i) {
    unsigned long scheduled = blockStart + i * 2000UL; // nominal 500 samples/s
    while ((long)(micros() - scheduled) < 0) {}
    timesUs[i] = (unsigned long)(micros() - blockStart);
    values[i] = analogRead(A0);
  }
  Serial.print(F("{\"type\":\"samples\",\"seq\":")); Serial.print(sequence++);
  Serial.print(F(",\"times\":["));
  for (unsigned i=0; i<N; ++i) {
    if (i) Serial.print(',');
    Serial.print(blockSeconds + timesUs[i]/1000000.0, 6);
  }
  Serial.print(F("],\"values\":["));
  for (unsigned i=0; i<N; ++i) {
    if (i) Serial.print(',');
    Serial.print('['); Serial.print(values[i]); Serial.print(']');
  }
  Serial.println(F("]}"));
}
