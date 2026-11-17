# ESP32–Tektronix TBS1062: reference setup and calibration

## Evidence taken from the provided images (25 Oct 2026)

- `TEK0000.BMP`: channel 1 **1.00 V/div**, horizontal **500 ms/div**. About five 1 Hz cycles appear across the 10 horizontal divisions (five seconds total). High-to-low vertical separation is about **3.3 divisions**, i.e. approximately **3.3 V peak-to-peak**, an image-based estimate, **not a calibrated reading**. The Tek AUTOSET Mean and Pk–Pk soft-menu entries contain `?`, so they are not direct voltage measurements.
- `TEK0001.BMP`: **2.00 V/div**, **500 ms/div**. The same signal has roughly half as many vertical divisions.
- `TEK0002.BMP` and `TEK0003.BMP`: **2.00 V/div**, **250 ms/div**. Roughly half as many cycles fit on the display compared with 500 ms/div.
- Original Python screenshot: **1 Hz**, **50% duty**, **500 ms/div**, but CH1 **2.403 mV peak-to-peak** and **~1.316 mV mean**. This is >1000 times below the image-estimated GPIO25 voltage. **A display color change or vertical scale setting cannot fix such an input-value mismatch.** The original firmware or wiring may have transmitted an incorrect scale, a floating ADC reading, or an improperly attenuated signal. These are hypotheses; a wiring and firmware inspection is required.

## Wiring that matches the bench reference

1. Connect ESP32 to Windows via USB (USB provides power and serial data).
2. GPIO25 is the 1 Hz square-wave OUTPUT. Connect the Tektronix CH1 probe tip to **GPIO25** and probe ground clip to **ESP32 GND**.
3. To measure with ESP32 ADC, wire **GPIO25 -> R1 10 kΩ -> GPIO34 -> R2 10 kΩ -> GND** (two 10 kΩ resistors). GPIO34 thus receives 0 to about 1.65 V and the firmware multiplies the ADC measurement by 2. Both grounds must be common.
4. Do not apply higher voltages, negative signals, AC mains, or 5 V to GPIO34. GPIO34 is input-only; it cannot generate a square wave. Never attach the Tek scope ground to a non-ground voltage.
5. Upload `examples/ESP32_Tek_Reference_1Hz.ino` using Arduino IDE for the selected ESP32 board. Close Arduino Serial Monitor before opening the Python DSO.

## Tektronix and PC controls

| Setting | Tektronix TBS1062 | Generic DSO light edition |
|:--|:--|:--|
| Signal / source | CH1 probe tip GPIO25 | Serial COM port for ESP32 GPIO34 ADC |
| Vertical | **CH1 1.00 V/div** | CH1 **1 V/div** |
| Horizontal | **M 500 ms/div** | **500 ms/div** |
| Coupling | DC | DC |
| Probe | Set to actual physical probe attenuation | **1X** (ADC units already recovered to GPIO25 voltage) |
| Position / zero | position for full high/low signal | display position ~**1.65 V** |
| Trigger | CH1, rising, near midlevel | CH1, Rising, **1.65 V**, Auto |
| Acquisition | scan/sampling | Sample, Linear interpolation |
| Frequency | expected ~1 Hz | expected ~1 Hz |
| Peak-to-peak | screenshot suggests ~3.3 V | expected about ~3.3 V (actual ADC calibration may differ) |
| Baud | not applicable | Serial **115200** |

Press **TEK PRESET** before connecting or while running; this changes the DISPLAY and trigger controls only, and enables a diagnostic comparison with the expected 0–3.3 V GPIO test. It never invents samples and does not modify calibration coefficients.

## If the Python screen reads 2.403 mV peak-to-peak

- Confirm GPIO25 physically toggles with a multimeter or Tek CH1.
- Confirm the R1/R2 divider **center goes to GPIO34**; both ends of the divider are GPIO25 and GND.
- Flash the bundled sketch; do not mix the old sketch protocol or an ADC value divided by 4095 **twice**.
- Use `analogReadMilliVolts` and convert mV to V **once** (`* 0.001`), then compensate the physical divider **once** (`* 2`), as in the firmware.
- On the PC, use **1X** unless you are feeding voltage measurements with a documented external 10X probe, and ensure sensor profile `gain=1`, `offset=0`, channel unit `V` at the beginning of this test. The actual voltage may be less than nominal due to ESP32 ADC inaccuracies; you can compare against a trusted meter.
- `CALIBRATE` uses the currently held capture to fit two known plateaus with `V = raw × gain + offset`. **Only calibrate if both reference voltages are independently verified.** A thousandfold correction is flagged and requires extra confirmation: numerical scaling cannot rescue floating or saturated ADC samples.
- The display has a `No signal` screen until it receives compatible timestamped sample data. Running app without board does not create fabricated waveforms.

## Accuracy boundary

This USB serial application is a lower-rate **data-acquisition viewer**, not a replacement for the **60 MHz, 1 GS/s** Tektronix oscilloscope. The nominal 50 samples/s produced by the reference sketch are suitable for a ~1 Hz square wave, but cannot resolve nanosecond rise time or high-frequency glitches. The timestamp list reflects actual ADC acquisition times, not UART arrival times. Triggering is performed on the PC; software GUI and firmware were reviewed without a bench-connected ESP32 in the development environment. Actual electrical calibration must be validated using your hardware.
