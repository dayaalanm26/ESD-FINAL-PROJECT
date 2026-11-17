> **CH2 potentiometer and LED:** Begin with [START_HERE_4_SENSORS.txt](START_HERE_4_SENSORS.txt) for the main four-channel sketch, LED wiring, and continuous rightward Roll. The reference experiments below use their own sketches and wiring.

## IMPORTANT — 2026 light-interface & scale fix

See **QUICK_START_FINAL.txt** and **docs/2026_SCALE_FIX_AND_CALIBRATION.md** first. After a previous ~686× two-point calibration, press **RESET CH1 GAIN** to reveal actual firmware values before comparing against the Tektronix. The latest UI now fits the **first real capture** instead of plotting a nominal 3.3 V waveform at 1 mV/div. Hardware/wiring must still be correct: software cannot certify voltage at the ADC or scope probe.

# Generic DSO — calibrated light edition

A real-hardware, 4-channel-capable Python DSO redesigned in a **light, high-contrast layout**. The reference setup is based on the supplied **Tektronix TBS1062** display photos and original PC screenshot. This package includes the full original acquisition/application modules, new calibration routines and matching ESP32 test firmware.

## Tektronix reference experiment on Windows

1. Extract the entire ZIP to a normal folder.
2. Double-click **`Start_DSO.bat`**. First launch creates a Python virtual environment if needed and installs requirements (internet required first time). Requires **Python 3.10+** and USB driver for the ESP32 board.
3. In Arduino IDE upload `examples/ESP32_Tek_Reference_1Hz.ino`. Wire GPIO25 → 10kΩ → GPIO34 → 10kΩ → GND. The real Tek CH1 probe tip connects to GPIO25 and Tek ground connects to ESP32 GND. Use common GND, ESP32 USB power.
4. Close Arduino Serial Monitor (COM can be opened by one application at a time). In the app choose **Serial**, your ESP32 **COM port**, **115200** baud; click **Connect** and then **RUN**.
5. Click **TEK PRESET**. It selects CH1 **1 V/div**, **500 ms/div**, DC, 1X, trigger CH1/Rising/1.65 V, normal Linear interpolation, and shows a diagnostic CH1 vs nominal 3.3 V / 1 Hz test. Allow 5+ seconds to fill a complete acquisition frame.
6. If your waveform is too small, **check firmware, divider, GND and input units before calibrating**. The old screenshot's ~2.403 mVpp is inconsistent with the scope screenshot's approximately 3.3 Vpp. **Do not simply set V/div to 1 V and claim accuracy.**
7. When a verified two-level test source is attached, **CALIBRATE** can compute `Voltage = source_data × gain + offset`; it requires verified reference values and warns on extremely large corrections. Change/restart RUN after applying calibration. Calibration profiles persist at `~/.generic_dso_profiles.json`.

The CH2 PWM duty and serial capture were verified with a host-side firmware mock and a Linux serial loopback into the Qt app. Physical ESP32 and Windows verification remain open. The existing test suite has four failures in the legacy reference preset, theme, AC trigger, and redraw timing tests.

## Controls and scope interpretation

- RUN/STOP, SINGLE and AUTOSET, CH1–CH4 vertical scales/position sliders, DC/AC/GND, 1X/10X/100X probe factors, X time/div and horizontal-position sliders.
- Trigger level/mode/slope/channel; scan-style acquisition with host-side trigger matching and 20% pre-trigger.
- Sample/Hi-Res/Average modes, optional sinc display interpolation for band-limited waveforms, **Linear** by default to preserve the appearance of square wave transitions.
- High-contrast white plot with 10×8 graticule, ground level indicators, triggered timestamp marker, measurement cards (frequency, period, Vpp, RMS, average, min/max, duty), cursors and exports.
- No invented capture: unless compatible **real** source data arrives, the app displays **No signal**. `TEK PRESET` never synthesizes measurements.
- The main pane is scrollable on short laptop screens so lower channel probe/position options remain reachable rather than being clipped.
- TCP acquisition path remains available; for ESP32 USB use Serial.

## Important numerical and hardware details

- With a true 1 Hz square wave, a **500 ms/div** 10-division view shows about **five periods**. With a 0–3.3 V pulse, **1 V/div** produces an approximately **3.3-division** height. The GUI estimates frequency/period/duty using device timestamps, not Windows UART receive times.
- The new firmware samples GPIO34 ADC with `analogReadMilliVolts()` at approximately **50 Sa/s**, provides **real timestamps**, and applies ×2 compensation for the specified two-resistor voltage divider. GPIO25 is the actual 1 Hz source; no software plotting generator is used. This approximate sampling speed is intentional: USB JSON transfer is not a full-bandwidth DSO.
- The Tek scope soft menu in the photos shows `?` for Mean and Pk–Pk: **3.3 Vpp is estimated from the graticule and nominal ESP32 GPIO voltage**, not read from a Tek measurement field. ESP32 high level and ADC transfer are not perfect references; verify with a suitable meter/properly calibrated scope if accuracy matters.
- If you previously changed calibration in the gear profile dialog, restore **gain 1, offset 0** to evaluate the new firmware before fitting any further corrections.
- **Do not** connect mains, negative voltages or 5 V to the ESP32 ADC input. Do not use a scope ground clip anywhere except circuit GND.

## Package contents

`scope_app.py` GUI; `scope_core.py` decoder/trigger/measurements; `transport.py` serial/TCP worker; `display_processing.py` interpolation and acquisition modes; `theme.py` new high-contrast light palette; `calibration.py` two-point fitting; `examples/ESP32_Tek_Reference_1Hz.ino` reference acquisition; `docs/TEKTRONIX_CALIBRATION_GUIDE.md` wiring and reference comparison; `tests/test_calibration.py` new numerical checks; existing development tests and protocol documentation. Reference BMPs supplied by the user are under `reference/` (original images, not simulated captures).

### Test and build

Once requirements are installed:

```powershell
python -m unittest discover -s tests -v
python scope_app.py
```

Windows launcher: `Start_DSO.bat`. Optional desktop EXE build: `Build_Windows_EXE.bat` (requires PyInstaller, which the launcher installs during building). Linux/macOS: `sh Start_DSO.sh`.

**Scope:** an extensible serial data-acquisition viewer with software triggering, **not** a 60 MHz/1 GS/s substitute for the Tektronix TBS1062.

## References
Four original Tektronix `.BMP` images and the original Python DSO screenshot are included unchanged in `reference/`. The `legacy` preview PNGs show the historical lavender app, not the new theme.
