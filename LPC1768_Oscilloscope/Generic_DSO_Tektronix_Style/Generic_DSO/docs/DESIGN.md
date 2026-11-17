# Generic DSO — Tektronix-style hardware scope

Native Windows/Python application for real, timestamped acquisition hardware. The
instrument face follows the familiar Tektronix DSO workflow: black 10 × 8 graticule,
blue top/bottom status bars, right-side context/soft menus, colour-coded channels,
RUN/STOP, SINGLE, AUTOSET, AUTORANGE, trigger, cursor and measurement controls.
The product remains a Generic DSO and does not claim to be Tektronix software.

Data enters through a user-selected serial port or TCP endpoint, using newline JSON
protocol version 1. Device metadata defines 1–4 channels and their units. Each
capture carries hardware sample timing, sequence information and real values. The
application never substitutes PC arrival time for acquisition time and never
creates a synthetic signal for the display.

## Instrument behaviour

- **ACQUIRE:** RUN/STOP, SINGLE SEQUENCE, AUTOSET and continuous AUTORANGE.
- **HORIZONTAL:** SEC/DIV and horizontal position, with exact selectors and sliders.
- **VERTICAL:** CH1–CH4 visibility, VOLTS/DIV, position, DC/AC/GND display coupling
  and 1X/10X/100X probe scaling.
- **TRIGGER:** Auto/Normal, source, rising/falling edge and level. The trigger line
  can also be dragged on the graticule. Single acquisition waits for one complete
  edge-triggered record and then stops.
- **CURSOR:** time/value cursors with delta time, inverse delta time and delta value.
- **MEASURE:** frequency, period, peak-to-peak, RMS, mean, minimum, maximum and duty.
- **SAVE:** CSV of received/calibrated samples and a PNG of the instrument screen.

Peak-preserving display downsampling is used for dense traces. It is a rendering
optimization, not filtering: received samples are not smoothed or fabricated.
Sparse captures show their sample dots.

DC/AC/GND and probe settings are host-side scope/display operations. They do not
replace a physical analog front end. Input attenuation, buffering, protection,
anti-alias filtering and isolation remain hardware responsibilities.

Board profiles are descriptive. Sensor profiles provide units and user-entered
linear calibration; they do not imply universal drivers or change physical ADC
capability. Firmware or an adapter must read each specific sensor.

Host triggers are software triggers on received sample history; hardware-trigger
performance is not claimed. Uncalibrated/uncertified hardware remains explicitly
unverified. Sequence loss, invalid frames, time discontinuity, input clipping and
no-data states are surfaced in the status line.
