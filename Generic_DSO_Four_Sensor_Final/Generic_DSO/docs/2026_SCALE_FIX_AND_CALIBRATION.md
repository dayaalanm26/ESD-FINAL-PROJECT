# October 2026 — Final light interface, range correction & scale audit

## What the screenshots prove

The newer GUI reported **3.3 Vpp**, **1 Hz**, **50% duty**, but still used **1 mV/div** and **0.00263 V** vertical position. Its waveform therefore left the 8-division plot; the top plateau was invisible and edges appeared as large vertical lines. That is a **display range error**.

The earlier calibration dialog reported a proposed **×686.62** software multiplier. The original input spread was only a few **mV**, not a confirmed 3.3 Vpp. A software gain can create the *number* 3.3 without proving the hardware ADC measured 3.3. That is an **acquisition/unit/calibration problem** distinct from the GUI range error.

The reference Tektronix screenshot shows approximately five periods across the 10 horizontal divisions at **500 ms/div**, consistent with a **1 Hz** square wave. The **CH1 1 V/div** label is clear; an approximately 3.3 V amplitude is *nominal expectation* based on an ESP32 GPIO output, **not an independently verified amplitude derived from the photograph**.

## Changes in this revision

1. **First-capture auto-fit** automatically chooses an appropriate volts/division and vertical position for real captured values. A 0–3.3 V waveform now uses **1 V/div**, centered at **1.65 V** instead of the prior 1 mV/div settings. No values are replaced or synthesized.
2. **TEK PRESET** remains available to match **500 ms/div, 1 V/div, rising CH1 edge, 1.65 V trigger** with DC coupling and 1X probe.
3. New **Scale check** diagnostic bar shows the span transmitted by hardware versus the software-corrected display span and current gain/offset.
4. Added **RESET CH1 GAIN** to safely restore unity gain / zero offset in the existing persisted settings (`%USERPROFILE%\.generic_dso_profiles.json`). This is critical after the old ×686 two-point calibration.
5. The calibration dialog refuses suspicious corrections (>5× or <0.2× of current gain). This prevents normal users from accidentally hiding firmware errors by forcing an arbitrary reference amplitude.
6. Detects **display clipping** separately from ADC saturation and flags off-screen traces; recommends **AUTOSET** rather than implying the source is damaged.
7. Kept the light, high-contrast interface, measurement cards, cursors, acquisition filters, hardware timestamp handling, CSV export, serial/TCP connections and CH1–CH4 channel controls.

## Where 3.3 V is and is not expected

Recommended two-10k-resistor divider:

- **GPIO25 (real Tektronix probe tip)**: approximately 0 to ESP32 logic-high rail (~3.3 V).
- **GPIO34 ADC**: approximately 0 to 1.65 V physically.
- **Firmware JSON channel in volts**: approximately 0 to 3.3 V, because it sends `analogReadMilliVolts(GPIO34) * 0.001 * 2.0`.
- **Python DSO with CH1 gain 1**: approximately 0 to 3.3 V; physical errors or ADC nonlinearity may cause deviation.

Do **not** apply two-point calibration until the hardware levels are checked independently. If the channel reports only 4–6 mVpp after reset, inspect the ADC pin, resistor divider, USB upload, pin declarations and grounds, not just the plot settings.

## Test results and limitations

The source files pass `compileall` and the revised unit/protocol/processing tests pass **33/33**. A GUI execution test and electrical reference verification are pending on a machine with PySide6, pyqtgraph and the actual ESP32/Tektronix hardware. The original serial integration test was not executable in this isolated environment because `pyserial` was unavailable. This is **not** a claim of end-to-end hardware calibration.
