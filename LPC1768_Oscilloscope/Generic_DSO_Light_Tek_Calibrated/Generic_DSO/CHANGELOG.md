# Change log — light Tek reference edition (2026-10-25)

## Visual update
- Replaced dark lavender theme with a high-contrast white/navy/blue control layout.
- Recolored graticule, trace, trigger and cursors for legibility on a bright background.
- Added instrumentation header, labeled Tektronix reference callout, quick TEK PRESET and CALIBRATE buttons.
- Added scrollable main container for laptop-height screens; lower channel controls are accessible.
- Changed square-wave interpolation default to **Linear**, avoiding sinc edge ringing.

## Reference and measurement calibration
- Added 500 ms/div startup timebase and one-click CH1 1 V/div, 500 ms/div, 1.65 V vertical reference, DC/1X and CH1 rising trigger preset.
- Added diagnostic of CH1 amplitude and frequency **when the GPIO25 0–3.3 V / 1 Hz test assumption is enabled**. Explicitly labels suspected low-amplitude signals and does not synthesize missing readings.
- Added two-point conversion helper (`calibration.py`) and modal, verification-gated calibration, including warning about suspicious >5x scaling and clearing stale acquisitions after modifying the gain/offset.
- Added calibrated ESP32 example with resistor divider and precise ADC acquisition timestamps instead of assuming that a UART message arrival is an acquisition timestamp.
- Compared four supplied Tek BMPs with prior PC screenshot in `docs/TEKTRONIX_CALIBRATION_GUIDE.md`.

## Verification
- Python `compileall` of all application files passes.
- 18 existing numerical protocol/capture tests, 6 processing tests, and 4 new calibration tests pass.
- GUI integration tests cannot run here because PySide6/pyqtgraph cannot be installed in the offline container. GUI and hardware operations require validation with the actual Windows + ESP32 equipment; avoid claims of measured accuracy until then.
