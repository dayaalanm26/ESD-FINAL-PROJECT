# Generic DSO — approved direction

Native Windows Python application. Flat instrument layout with black 10 × 8
graticule, four colour-coded inputs, side controls and measurement strip.
No 3D scene, FPS display, synthetic acquisition or simulation mode.

Data enters through a user-selected serial port or TCP endpoint, using newline
JSON protocol version 1. Device metadata defines 1–4 channels and their units.
Each capture carries hardware sample timing, a sequence number and actual values.
The application never substitutes PC arrival time for sample time.

Board profiles are descriptive. Sensor profiles provide units and user-entered
linear calibration; they do not imply universal drivers or change physical ADC
capability. Firmware or an adapter must read each specific sensor.

Host controls: Run/Stop, Single, Auto/Normal edge trigger, source, slope, level,
horizontal time/div and pan, per-channel value/div and position, Auto Scale,
time/value cursors, CSV and PNG export. Host triggers are software triggers on
received blocks; hardware trigger support is not claimed. Actuator outputs are
not driven by this acquisition application.

Uncalibrated/uncertified hardware remains explicitly unverified. Sequence loss,
invalid frames, time discontinuity, input clipping and no-data states are visible.
No digital filtering or smoothing; plotted line segments join real sample points.
Sampling timestamps describe sequential sensor polling unless sender guarantees
simultaneous acquisition. Calibration cannot correct bandwidth or aliasing.

Windows launcher creates a local virtual environment on first run. Internet and
Python 3.10+ are required for first setup. No prebuilt Windows EXE is claimed.
