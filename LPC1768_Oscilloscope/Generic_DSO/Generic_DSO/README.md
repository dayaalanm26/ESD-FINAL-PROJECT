# Generic DSO

Native DSO interface for actual hardware measurements. No 3D view, FPS counter,
waveform generator, simulation mode or dataset requirement.

## Start on Windows

1. Extract the **whole ZIP** to a normal writable folder, such as Documents.
2. Install Python **3.10 or newer** for Windows, including pip and the Python launcher.
   Python 3.12 is a suitable choice for this package.
3. Double-click **Start_DSO.bat**. On the first launch it creates `.venv` in this
   folder and downloads dependencies; internet is required for that setup.
4. Double-click the same launcher for later runs. Keep all package files together.

This download contains Python application source and a launcher, not a prebuilt
Windows EXE. `Build_Windows_EXE.bat` optionally builds an EXE on your Windows PC.
The generated EXE must stay with its complete `dist/Generic_DSO` folder.
If the app cannot start, read `startup_error.log` in the package folder.

macOS/Linux: install requirements in a virtual environment, then run
`python Generic_DSO.pyw`. GUI platform dependencies are also needed on Linux.

## First connection

1. Implement the device format in **docs/PROTOCOL.md** or use an included example.
2. Connect the board using its serial/USB interface. Close Arduino Serial Monitor,
   other terminal programs, or anything else that owns the same port.
3. In the app select Serial, the COM port, and the firmware's baud rate. Click Connect.
   For a microprocessor/network bridge, select TCP and enter its address and port.
4. After metadata arrives, the app shows READY. Press **Run**.
5. Set channel value/div and centre, time/div, trigger source/edge/level.
6. Click **Auto Scale** after data arrives if the received signal is outside the view.
7. Select a measurement channel and enable Time, Value, or Time + Value cursors.
   Drag lines on the plot; Centre cursors places them back in view.

The app starts blank and never makes its own signal. Connecting a bare board with
unrelated firmware will not produce a valid trace. GPIO voltage cannot be obtained
by selecting a board name. Use a suitable measurement input and firmware/adapter.

## Controls

| Control | Behaviour |
|---|---|
| Run/Stop | Captures new received samples / freezes the last capture |
| Single | Arms one complete edge-triggered record; then stops |
| Auto trigger | Uses matching edges when available; otherwise shows newest received samples |
| Normal trigger | Updates only after a matching edge and enough pre/post samples |
| Trigger level | Enter a value or drag the orange horizontal trigger line |
| Time/div | Ten horizontal divisions; trigger appears at 20% of the window |
| Position (s) | Pans the displayed captured time range; does not alter sample times |
| Value/div and Centre | Per-channel vertical scaling, in that channel's configured units |
| Auto Scale | Fits existing data; does not modify the acquisition hardware |
| Cursors | T1/T2, delta time, inverse delta time; Y1/Y2 and delta value |
| Export samples | CSV of actual captured samples, plus calibration metadata JSON |
| Save screen | PNG screenshot of the current instrument window |

The first acquisition widens an overly short default time base for slow sensors.
A manually selected time base is otherwise respected. Time/div only selects the
host record/display span; it cannot change the hardware sampling clock.

Channel toggles affect visibility. The measurement selector chooses which channel
the bottom readouts and value cursors use. Trigger source is independently selected.
Channel centre means the physical value at the graticule's zero division.
Time cursor coordinates are relative to the trigger (or the Auto capture origin).
The inverse cursor separation is a frequency equivalent, not automatic proof of
the waveform's actual frequency.

Single/Normal may wait indefinitely if there is no matching edge, insufficient
pre-trigger history, insufficient record length, or requested duration exceeds
the 65,536-point buffer. Reduce time/div or sample rate. Stop preserves the capture;
disconnect preserves it with a held/disconnected label. A new connection clears it.

## Boards, sensors and actuators

The **Device & sensors** dialog includes board families and sensor quantities:
voltage, temperature, current, pressure, acceleration, light, humidity, speed,
position, digital level, and custom channels. These are configuration profiles,
not universal sensor drivers. Use a driver for your particular I²C/SPI/UART sensor.
Actuator feedback such as current, speed, position or temperature is acquired in
the same way. The app does not send motor/actuator commands.

Conversion is: **displayed value = received value × gain + offset**.
Select Custom or the actual sensor quantity, set a unit, and enter a calibration
derived from the sensor datasheet and your reference measurements. Changing the
unit name alone does not convert values. Profile changes clear old samples to
avoid mixing old and new calibrations. Profiles save locally for subsequent runs.
The Device units setting adopts the firmware name/unit while preserving entered
gain and offset. Set gain 1 and offset 0 for unmodified device values. Review
calibration when switching hardware; saved profiles are not tied to a serial number.

Examples:

- **Arduino_Real_ADC.ino**: complete classic Uno/Nano ATmega328P A0 example at 115200
  baud. Transmits ADC counts and measured timestamps. Its polling/transmission
  gaps make it a connection example, not precision high-speed DSO firmware.
- **generic_sender.c/.h**: portable C packet encoder for one real ADC channel.
  Integrate with the board's timer/ADC/DMA and UART/USB routines. Not a complete
  LPC1768, ESP32 or other board-specific firmware. C floating-point printf support
  may need enabling in your embedded toolchain.
- **linux_sensor_bridge.py**: reads an existing Linux IIO sysfs raw sensor attribute
  and serves it over TCP. No data is generated if a sensor is absent. Read timing
  represents software polling; use a buffered hardware driver for waveform accuracy.

## Accuracy and limits

No physical accuracy percentage is claimed. Actual ADC samples are plotted, with
straight segments connecting samples and no digital filtering/smoothing. Sparse
captures show sample dots. Curves between dots are not additional measurements.

Frequency uses interpolated midpoint crossings with hysteresis and requires at
least three crossings, reasonably consistent periods and at least ten samples
per estimated period. It can still be wrong for aliased/noisy/nonperiodic signals;
verify against a reference. Peak-to-peak, mean and RMS describe the captured sample
values. Mean/RMS are sample-weighted, not time-weighted for irregular sensor reads.

The timing readout reports median samples/s and flags irregular spacing. Sequential
sensor reads are not automatically simultaneous channels. Host software triggering
cannot guarantee capture of events that the acquisition device missed.

Use appropriate attenuation, buffering, anti-alias filtering and input protection.
Do not apply motor supply voltage, negative voltage or mains directly to an MCU ADC.
High-side/switching measurements need the appropriate differential/isolated input.

For validation, use **docs/VALIDATION.md**. A dataset is not required.

## Tests

Run `python -m unittest discover -s tests -v` from this directory. Tests contain
fixed/numerical fixtures solely to verify the software; there is no test source
selectable in the delivered application. The POSIX serial integration test uses
an OS pseudo-terminal and is skipped on Windows.
