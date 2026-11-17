FIXED — ESP32 PWM RUN / WAITING (14 November 2026)

ROOT CAUSE SHOWN IN SCREENSHOT
- Python DSO showed Serial COM5 at 115200 baud and WAITING / disabled RUN.
- ESP32 potentiometer PWM sketch transmits at 921600 baud.
- The GUI intentionally disabled RUN until a valid HELLO metadata frame arrived.
- Incorrect baud means no valid HELLO, hence grey RUN and no signal.

CHANGES TO THIS APPLICATION
- Serial baud defaults to 921600 for current PWM sketch.
- RUN can be armed/cancelled once the COM port is open, even while waiting for HELLO.
- Connect pre-arms capture: acquisition starts automatically once valid HELLO is received.
- Clear diagnostics on wrong-baud/invalid-data or missing HELLO.
- Added PWM 100 Hz preset (1 V/div, 5 ms/div, CH1 trigger rising 1.65 V).
- Original TEK PRESET (1 Hz, 500 ms/div) retained for the old reference firmware.
- Included GPIO34 PWM firmware repeats HELLO every 2 seconds instead of 10 seconds.
- No simulated measurement samples. Real hardware samples only.

START / TEST
1. Flash examples/ESP32_POT_PWM_LED_DSO_GPIO34_FINAL.ino (requires classic ESP32).
2. Close Arduino IDE Serial Monitor and other COM users.
3. Double-click Generic_DSO/Start_DSO.bat.
4. Select COM5 (or your ESP32 port), Serial, BAUD = 921600.
5. Connect. It will auto-run after receiving ESP32 HELLO (~2 seconds).
6. Click PWM 100 Hz preset. GPIO34 must be connected to midpoint of two 1k resistors from GPIO25 to GND.
7. If previous 686x gain still exists, click RESET CH1 GAIN.
8. LED brightness follows the 10k potentiometer on GPIO35; CH1 frequency ~100Hz at intermediate duty.

If you prefer the old 1 Hz test firmware, set 115200 baud and use TEK PRESET.
At 0% or 100% duty the output is flat: frequency/duty edge measurement may be unavailable.

LIMITATIONS
Python GUI was syntax-checked but not operated on the user's Windows PC or physical ESP32.
