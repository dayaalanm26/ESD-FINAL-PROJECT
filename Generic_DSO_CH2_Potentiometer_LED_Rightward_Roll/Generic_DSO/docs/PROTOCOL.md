# Generic DSO protocol v1

The GUI reads UTF-8 JSON objects, one per line, over serial (8-N-1, no flow control)
or a TCP stream. The GUI is the TCP client. It sends no device commands.
The device or adapter continuously sends actual measurements. Run/Stop and Single
operate on host capture; Stop does not stop the sensor or command an actuator.

## 1. Metadata before samples

```json
{"type":"hello","protocol":1,"device":"My acquisition board","channels":[{"name":"A0","unit":"V","min":0,"max":3.3},{"name":"Temperature","unit":"°C"}]}
```

Define 1–4 channels. Name: 1–40 characters. Unit: up to 16 characters, required.
Optional min/max must both exist and min < max. They describe the physical valid
input range in the transmitted units. Values at or outside these boundaries raise
a RANGE LIMIT indicator; reaching a boundary is not proof of analog clipping.
Use `clipped:true` on a frame when the hardware actually detects saturation.

Metadata may be repeated unchanged. Changed metadata resets capture and requires
Run again. Restarting timestamps/sequence with identical metadata requires a fresh
connection, so old and new time epochs cannot be joined silently.

## 2. Hardware-timed uniform samples

```json
{"type":"samples","seq":0,"t0":0.0,"fs":10000,"values":[[0.12,25.1],[0.14,25.1],[0.18,25.1]],"clipped":false}
```

- `seq`: nonnegative integer, increases by 1 for every transmitted sample frame.
- `t0`: actual first-sample acquisition time in seconds from a monotonic epoch.
- `fs`: actual sample frequency, Hz, > 0. Send the calibrated rate if known.
- Row k is captured at `t0 + k/fs`. Each column follows metadata channel order.
- Only use uniform timing for genuinely uniform hardware sampling. UART arrival
  time and a delay loop's requested rate are not substitutes for this timing.

## 3. Timestamped sensor reads / irregular acquisition

```json
{"type":"samples","seq":1,"times":[0.101,0.1032,0.1051],"values":[[0.21,25.2],[0.22,25.2],[0.24,25.2]]}
```

Use `times` instead of `t0`/`fs`. It must contain one timestamp per row, in seconds,
strictly increasing within and between frames. Values must be finite JSON numbers.
Do not send NaN, Infinity, numeric strings, null, or booleans.

Rows imply shared sample times. If sensors were polled sequentially, this does
not prove simultaneous sampling: describe that limitation in the hardware adapter.
Do not repeat stale sensor values and claim each row is a fresh simultaneous sample.
Use separate sessions or an explicit hardware synchronisation strategy if necessary.

## 4. Limits and errors

- 1–4096 rows/frame, 1–4 channels, maximum line size 2 MB.
- Host acquisition ring: 65,536 points. Choose record duration/sample rate that fits.
- Duplicate sequence, overlapping/reversed time, missing metadata and malformed
  rows are rejected. The next accepted frame starts a new segment after errors.
- Missing sequences and uniform-time discontinuities reset the capture buffer;
  the GUI counts gaps. Device-side losses must also be reflected in seq/timing.
- For explicit timestamps, inter-frame gaps over 10 times the preceding median
  cadence break continuity. Within a frame, gaps over 10 times its median cadence
  discard the prefix through the last such gap, preserving the latest segment.
  This is a conservative heuristic, not guaranteed detection of every missing sample.
  Set `discontinuity:true` on a frame whenever the sender knows continuity was lost.
  Start a new frame at each known acquisition interruption. Genuinely irregular
  polling may therefore start new segments; no uninterrupted capture is implied.
- Queue overflow is counted and breaks continuity rather than hiding dropped data.
- After three seconds without data (or longer for the selected slow time base),
  the status reports no recent samples. A previous capture remains labelled/held.

## 5. Throughput and accuracy

This text protocol prioritises integration and inspectability. It does not provide
full laboratory oscilloscope throughput. Serial overhead can dominate the rate.
For example, 115200 baud with 8-N-1 carries at most about 11,520 payload bytes/s
before further overhead; a JSON sample often requires many bytes.

Use timer-triggered ADC acquisition, DMA/buffered capture where supported, proper
input conditioning, a known voltage reference and a calibrated clock. Send measured
samples in blocks after capture if streaming cannot keep up. Triggering in this
version is performed on the PC after receiving blocks and is not a hardware trigger.

The ADC resolution, input range, bandwidth, timing and driver are properties of
the acquisition hardware. Selecting a board in the UI does not change them.
For faster instruments, implement a manufacturer SDK adapter or a future binary
protocol. Merely selecting that instrument's name is not an implemented driver.

## Four-channel serial stream

The bundled potentiometer firmware declares CH1 Light (V), CH2
Potentiometer (V), CH3 Sound (V) and CH4 IR Detection (logic).
The same CH2 measurement controls the duty of the 1 kHz LED output
on GPIO25. The CH2 stream contains the measured potentiometer voltage.
The host also understands older firmware that declares a channel
'nullable': a JSON null in that column is an unavailable reading.
All other samples are finite numeric values. Device messages use
'"type": "status"' and a short '"code"'. The host recognizes 'ready',
'dht11_waiting' from older firmware, 'ads1115_missing' and 'pwm_unavailable'.

An initial packet with seq 0 and discontinuity true permits restarting
the firmware while leaving the serial port open. Roll reverses the display
axis so the newest sample is on the left. Samples inside the capture are
still in ascending timestamp order, as in the CSV export.
