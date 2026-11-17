# Generic DSO

A native PySide6 oscilloscope with a lavender theme and a classic DSO layout.
**Real hardware only:** the app does not generate signals. Without a connected
compatible device, the screen displays **No signal**.

## Start on Windows

1. Extract the complete ZIP to a folder.
2. Open `Start_DSO.bat`. It uses an existing `generic_dso` Conda environment;
   otherwise it creates or reuses `.venv`. Missing packages install automatically.
3. Choose Serial with the COM port and baud rate, or TCP with the host and port.
4. Connect, wait for READY, then press RUN. Use the gear to set sensor units,
   channel names and calibration. The device must send compatible protocol data.

Python 3.10+ is required; the first dependency installation needs internet.
Linux/macOS: run `sh Start_DSO.sh`, or install `requirements.txt` and run
`python scope_app.py`. Build a Windows application with `Build_Windows_EXE.bat`.
The Windows launcher has been inspected but cannot be executed in the Linux test environment.

## Controls

- Single-row connection header; 10 × 8 graticule with ground and trigger markers.
- RUN/STOP, SINGLE and AUTOSET remain visible beside all six control pages.
- Four channel cards: toggle, scale, position, DC/AC/GND and 1X/10X/100X probe.
- Eight measurement chips; use Measure to choose source and visible readings.
- Time and value cursors; offscreen time cursors recenter when the timebase changes.
- Save exports CSV plus metadata, or a PNG screenshot. Profiles persist locally.

## Acquisition and display

**Sample** keeps each acquired record. **Average** combines 2/4/8/16/32/64
successive compatible triggered records, resetting on timebase, vertical-scale,
trigger, coupling, probe or data-gap changes. Non-triggered or irregularly timed
records are shown without averaging. **Hi-Res** applies a 2/4/8/16-sample boxcar.
Measurements use the acquisition-processed data and selected probe/coupling.

**Sin(x)/x** is the default display-only interpolation for sparse, uniformly
sampled traces; **Linear** joins samples directly. Irregular data stays linear.
Sinc can visibly ring around a square-wave edge; select Linear to inspect it.
The original samples remain in the display path, and peak downsampling preserves
narrow extrema. Sample dots appear below 80 visible points. Refresh is capped at 30 fps.

**CSV always contains the raw captured record** before Average, Hi-Res,
probe/coupling and display interpolation. As in the original app, configured
`raw × gain + offset` calibration has already been applied at acquisition.
Device and relative timestamps are preserved. Cursors do not use interpolated
values. Probe factors are dimensionless; position and trigger fields use the
channel's physical unit.

## Hardware and validation

Protocol: `docs/PROTOCOL.md`. Examples for Arduino ADC, C firmware and Linux
sensor sources remain in `examples/`. Firmware must provide acquisition
timestamps; serial arrival times are not a sampling clock. This is not a
calibrated laboratory instrument, and no physical board was connected for this update.

Run tests: `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v`.
On Windows use `set QT_QPA_PLATFORM=offscreen` first.

`previews/` contains labeled **synthetic test fixtures**, not measurements from
hardware. Recreate them with `python tests/render_preview.py`. This script is
not imported or exposed by the application. See `CHANGELOG.md` and
`docs/VALIDATION.md` for changes and verification.
