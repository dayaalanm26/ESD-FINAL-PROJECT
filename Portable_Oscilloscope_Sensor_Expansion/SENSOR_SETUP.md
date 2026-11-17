# Portable Oscilloscope sensor expansion

Use a classic ESP32 DevKit/WROOM-32 and an ADS1115 breakout. The ESP32 sends timestamped sensor voltages in the existing Generic DSO JSON protocol v1, over USB at 921600 baud. The firmware identifies itself as **ESP32 ADS1115 Sensors**.

## Install and run

1. Extract the ZIP, then copy its contents into the existing DSO folder alongside `scope_app.py` and `Start_DSO.bat`.
2. Open `firmware/ESP32_ADS1115_Sensors/ESP32_ADS1115_Sensors.ino` in Arduino IDE. Select your classic ESP32 board and upload. The sketch uses the ESP32 board package's built-in `Wire` library.
3. Close Arduino Serial Monitor/Plotter. Open **Start_DSO_Sensors.bat** for the automatic sensor display preset, or open the original **Start_DSO.bat** and select **500 ms/div**, **Auto** trigger and **Linear** display manually. Run the original launcher once if dependencies need to be installed. On macOS or Linux, run `python sensor_scope.py` in the same environment as the DSO.
4. Choose **Serial**, your ESP32 port, and **921600 baud**. Click **Connect** and press **RUN** if capture has not started. Enable the sensor channels you want to see. For an initial voltage check use **DC**, **1X**, **1 V/div**, with a vertical center around **1.65 V**. Use the channel gain of **1** and offset of **0** when checking the firmware's voltage readings. Clear the old ×686 PWM correction if it is present.
5. To return to the PWM experiment on the same ESP32, reflash your existing PWM sketch and launch your regular DSO. Each sketch runs separately.

## ADS1115 wiring

| ADS1115 pin | Connect to |
| --- | --- |
| VDD | Regulated **3.3 V** |
| GND | ESP32 GND and sensor grounds |
| SDA | ESP32 **GPIO21** |
| SCL | ESP32 **GPIO22** |
| ADDR | GND for address **0x48** |
| ALERT/RDY | Leave open |

Use I²C pull-ups to 3.3 V if the breakout does not already have them. If the ESP32's 3V3 pin is inaccessible on your breadboard, power the ADS1115 and compatible sensors from a regulated external 3.3 V supply with its ground tied to ESP32 GND. Reserve GPIO26 for the low-current potentiometer circuit. Use 0–3.3 V signals on the ADS1115 inputs and share ground between devices.

## Sensor channels

| ADS1115 input | Wire the input here | What appears on the DSO |
| --- | --- | --- |
| AIN0 | Junction of an **LDR to 3.3 V** and a **10 kΩ resistor to GND** | Voltage rises with increasing light |
| AIN1 | Junction of a **10 kΩ NTC thermistor to 3.3 V** and a **10 kΩ resistor to GND** | Voltage rises as the NTC warms |
| AIN2 | Analog **envelope** output of a 3.3 V-compatible sound sensor | Voltage peaks during a clap or tap |
| AIN3 | LOW/HIGH output of a 3.3 V-compatible IR obstacle sensor | Voltage switches when an object enters or leaves detection range |

With the default sketch the DSO maps AIN0–AIN3 to CH1–CH4. Start with the LDR alone. A 1 kΩ fixed resistor also works for a basic light test, though the voltage will be lower for the same light level. For any unused inputs, connect them to GND or remove their entries from `INPUTS` in the sketch. Removing an entry increases the available update rate for the remaining inputs. The DSO channel order then follows the order of the remaining entries. If you have a sensor powered from 5 V, check its output level before using it with this circuit. The sensor traces are in volts. Lux, temperature and sound level require their own calibrated conversions.

## Timing and troubleshooting

- The ADS1115 is configured for 860 conversions per second nominal. Active inputs are read sequentially. I²C transactions and conversion wait time reduce the actual per-channel rate. Each row uses the midpoint of its scan as a shared timestamp, so the channels have small time offsets within a row. Use this mode for light, temperature, sound envelopes and slow logic transitions. Use the existing ESP32 PWM sketch for the 100 Hz demo.
- **WAITING / No signal:** verify the selected port and baud, then check ADS1115 power, SDA, SCL and ADDR. The sensor sketch sends identification after the first successful scan. It retries on a bus error and flags any interrupted capture when communication recovers.
- **Trace is flat:** enable the right channel, select DC coupling, and check its input voltage with a meter. Temporarily connecting a sensor input to GND and then to the same regulated 3.3 V rail gives two known levels for a wiring check.
- **Right reading but off-screen:** adjust volts/div and vertical position. A wire can be checked without using two-point calibration. Calibrate only with independently measured reference voltages.

Hardware references: [TI ADS1115 datasheet](https://www.ti.com/lit/ds/symlink/ads1115.pdf) and [Espressif Arduino ESP32 I²C API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/i2c.html).
