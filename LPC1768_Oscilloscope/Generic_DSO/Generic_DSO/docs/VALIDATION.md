# Hardware validation worksheet

Software checks are not proof of physical measurement accuracy. Before claiming
performance, record the acquisition board, input circuit, reference instrument,
firmware, sample rate, channel count, input range and calibration date.

## Suggested bench checks

1. Ground/zero input: measure offset and noise; confirm the input is not floating.
2. Several known DC levels within the designed input range: compare against a
   calibrated multimeter; obtain gain/offset calibration and residual error.
3. Known sine waves across the intended frequency range: compare peak-to-peak,
   RMS and frequency with a reference DSO; account for probe attenuation/loading.
4. Square/PWM signals: compare period, edge timing and visible pulse shape. Sharp
   edges require bandwidth beyond the fundamental frequency.
5. Trigger: verify rising/falling edges, threshold, Auto, Normal and Single.
6. Step/pulse: confirm that the acquisition firmware captures the entire event,
   not only the block sent after it.
7. Two channels, where available: check relative timing. A multiplexed ADC or
   sequential sensor reads have inter-channel skew; do not claim simultaneity.
8. Unplug/reconnect, reset the board, slow down transmission, and force a dropped
   frame. Check error/gap indicators and that old data is not represented as new.

| Signal | Reference value | DSO value | Absolute error | Relative error | Conditions |
|---|---|---|---|---|---|
| DC low | | | | | |
| DC mid | | | | | |
| DC high | | | | | |
| Sine frequency | | | | | |
| Sine peak-to-peak | | | | | |
| Pulse period | | | | | |

Relative error (%) = 100 × |measured − reference| / |reference|, for nonzero
reference values. Use absolute error at zero. Keep reference-instrument uncertainty
separate from the error observed. Establish pass limits from the project needs and
component specifications rather than assigning an arbitrary accuracy percentage.

## Delivery verification scope

The application is verified with automated parser/capture/measurement checks,
offscreen GUI interaction and serial transport through an OS pseudo-terminal.
The portable C encoder is compiled with C99 warnings enabled. This delivery has
not been tested on a physical MCU, a reference oscilloscope or a Windows desktop.
The Arduino firmware example must be compiled/uploaded using the matching board
core on the user's machine. The Windows EXE build script is supplied, not executed
on this Linux development environment.
