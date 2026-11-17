# Final merged release notes — 14 November 2026

**Base:** exact user-uploaded `Generic_DSO_FINAL_Light_Calibrated_Fixed(1).zip`.
UI layout, colour scheme, old Tektronix reference, saved profiles, trigger,
measurement and waveform acquisition preserved.

**Integrated fixes**
- Serial connection defaults to 921600 baud for the ESP32 PWM firmware.
- COM Connect automatically arms RUN when device metadata arrives.
- RUN can be armed/cancelled once COM opens, even before metadata.
- Clear diagnostics when the ESP32 does not send valid metadata.
- New `PWM 100 Hz` button: 1 V/div, 5 ms/div, center 1.65V, rising trigger 1.65V.
- Keeps PWM preset from being overwritten by automatic first-capture fit.
- Automatically resets >5x stale CH1 calibration on recognized shipped
  ESP32 PWM firmware only; warns visibly. User still has RESET CH1 GAIN.
- Two-point calibration instructions now refer to two 1k divider resistors.
- Includes latest ESP32 Arduino sketch: GPIO26 pot power, GPIO35 ADC,
  GPIO25 PWM LED + Tektronix, GPIO34 ADC through 1k/1k divider.
- Kept original `TEK PRESET` for the 1 Hz test / 115200 baud.

**Calibration statement:** Software calibration has not been checked against a
traceable voltage standard. The shipped firmware reconstructs GPIO25 volts
using equal resistors (scale x2). No artificial waveforms are generated.
