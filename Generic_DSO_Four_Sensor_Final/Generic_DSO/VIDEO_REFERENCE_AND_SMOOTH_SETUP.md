# Generic DSO — Video-matched PWM edition (14 Nov 2026)

## What was observed in the attached 19.85-second Tektronix video

The real Tektronix TBS1062 displays the PWM output produced by the ESP32.
The reference trace is a **100 Hz** signal (period approximately **10.00 ms**) whose **HIGH duration changes when the potentiometer turns**. At the ends of the knob's range the signal becomes almost a constant LOW/HIGH line (the real scope occasionally shows narrow spikes as the duty approaches its limits). The Tektronix display in this video shows **CH1 2.00 V/div**, **M 5.00 ms/div**, and around **five cycles** across its ten horizontal divisions. The hardware menu shows edge-triggered/autoset operation. Your original earlier 1 Hz Tektronix screenshot used a different horizontal scale; both presets remain available.

This version targets **visual consistency** with the video at 100 Hz, not claimed 1:1 hardware performance. The real TBS1062 has much faster ADC and precise triggering than ESP32 ADC + USB serial.

## Changes in the supplied app

- **60 Hz target graph refresh** (16 ms reader/UI polling, instead of ~29-30 Hz), with timer-driven redraw coalescing. Actual FPS depends on Windows rendering and computer load.
- **True live PWM sample-step display** (`PWM steps (measured)`) renders hard edges without false Sin(x)/x ringing. No synthetic voltages are emitted. Each displayed transition is between two consecutive measured samples; its precise edge time is only bounded by the ADC sample interval.
- **Dark Tek-style waveform screen** inside the preserved white/light-blue interface, with bright CH1 yellow and darker grid; optional from Acquire > Tektronix dark signal screen.
- **PWM preset adapted to the provided video**: CH1 **2 V/div**, Time **5 ms/div**, trigger rising CH1 **1.65 V**, DC coupling, probe 1X, 100 Hz PWM. The original **TEK PRESET (1 Hz)** remains available.
- **Low-latency, bounded input queue:** if the UI falls behind, discard stale oldest sample blocks but preserve control/metadata events and mark loss (Gaps/Drops). Do not create samples to hide missing packets.
- **Display and measurements decoupled:** 60 Hz target trace, ~10 Hz metrics, and 4 Hz diagnostics to reduce UI stutter.
- **0%/100% duty handling:** trigger Auto can refresh a true flat level and suppress noise-only frequency/duty readings on PWM when peak-to-peak is <0.4 V.
- Live footer shows observed UI FPS, time since last frame handled by the GUI, frames, gaps, invalid frames, and drops. The reported age is *time since the latest frame was processed by the GUI*, **not** verified end-to-end latency from ESP32 sampling.

## Run with existing wiring — no hardware changes required

1. Extract ZIP and open the `Generic_DSO` folder.
2. Double-click `Start_DSO.bat`. The first start may install requirements and needs internet on your Windows computer.
3. Upload (or keep) the existing supplied ESP32 GPIO25 PWM / GPIO34 sample streaming firmware at **921600 baud**.
4. Close Arduino IDE **Serial Monitor** and **Serial Plotter** so they do not hold the COM port.
5. In the Python app choose **Serial / your ESP32 COM port / 921600**, then **Connect**.
6. The app automatically recognizes the PWM board and selects the video-matched PWM preset, otherwise press **PWM 100 Hz** manually.
7. Ensure **RESET CH1 GAIN** if any old software calibration is in effect. Do not apply an unverified ×686 correction.
8. Turn the potentiometer: LED brightness and the **HIGH pulse width** should both change; nominal voltage remains ~0–3.3 V.

### Final pin configuration (unchanged)

|ESP32|Circuit connection|
|:--|:--|
|GPIO26|10 kΩ potentiometer outer end (HIGH; low-current only)|
|GPIO35|Potentiometer wiper (middle)|
|GND|Potentiometer other outer end and shared scope ground|
|GPIO25|100 Hz PWM output: LED via 330 Ω or 1 kΩ resistor; Tek CH1 tip; divider resistor R1|
|GPIO34|Midpoint of two equal **1 kΩ** divider resistors; R1 goes to GPIO25, R2 to GND|
|USB|ESP32 to computer running Python DSO|

**Never connect a Tektronix probe ground clip to GPIO25 or 3V3. Connect it to ESP32 GND.**

### Instrument setup

|Setting|Video-matched PWM|
|:--|:--|
|Time/div|5 ms/div|
|Voltage/div|2 V/div|
|Trigger source/slope|CH1 / Rising|
|Trigger voltage|1.65 V (adjust if desired)|
|Coupling|DC|
|Sampling in supplied firmware|Nominal 2,000 Sa/s; actual timing is read from packet timestamps|
|Serial|921600|
|Measure|Frequency ~100 Hz, period ~10 ms, duty follows knob, peak-to-peak ~3.3 V except 0% and 100%|

### Accurate interpretation

The Tektronix traces are obtained from physical GPIO25 PWM and may show narrow pulses that the ESP32's 2,000 Sa/s ADC misses. At 100 Hz the 10 ms period contains ~20 ADC samples. Narrow pulses less than about 0.5–1 ms are not consistently resolvable with this firmware. The new step display does **not** add samples, increase sampling rate, reconstruct missing pulses, or change CSV/calibration values. To capture narrower pulses reliably, higher-speed acquisition firmware/hardware is needed.

### Diagnostics

- **WAITING / No signal:** choose the correct COM port and **921600** baud; close Serial Monitor, reconnect.
- **Gaps or Drops increasing:** performance or UART throughput limitation; shut down other apps and reduce serial load/sampling rate if necessary.
- **No changing LED:** check GPIO26 powering only the 10 kΩ potentiometer, GPIO35 wiper and GPIO25 LED series resistor.
- **LED changes but Python trace is flat:** verify GPIO25 → 1 kΩ → GPIO34 → 1 kΩ → GND, ADC firmware and unit multiplier 2.
- **High duty flat HIGH and low duty flat LOW:** normal PWM behavior. The frequency cannot be inferred from a DC level.
- **CH1 gain not ×1:** reset previous erroneous software calibration before comparing voltages.
- **Plot shows edges with minor timing variations:** due to the ESP32 ADC sample spacing and jitter; the Python graph cannot precisely reproduce the Tek hardware sampling speed.

### Verification coverage

The supplied numerical, protocol, processing, and integration headless tests were run in this environment. **A live PySide6 GUI test and your real ESP32 / Tek hardware test were not possible here** because the graphical dependencies and board were not available. The target refresh is not a measured guarantee on your laptop.
