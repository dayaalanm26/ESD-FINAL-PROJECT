"""Simulated classroom inputs for the labeled offline Demo modes."""
import math
from time import monotonic

from scope_data import Device, Packet


class Demo:
    def __init__(self, pwm):
        self.pwm = pwm
        self.rate = 2000 if pwm else 160
        self.start = monotonic()
        self.index = -int(self.rate * (0.2 if pwm else 4.8))
        self.light = 0.50
        self.sound_time = -5.0
        self.object_near = False
        self.duty = 0.5
        self.device = (
            Device("Simulated 100 Hz PWM", (("GPIO25 PWM", "V"),)) if pwm else
            Device("Simulated ADS1115 sensors", tuple((label, "V") for label in
                   ("AIN0 Light", "AIN1 Thermistor", "AIN2 Sound envelope", "AIN3 IR level")))
        )

    def clap(self):
        self.sound_time = monotonic() - self.start

    def next(self):
        due = int((monotonic() - self.start) * self.rate)
        if due <= self.index:
            return None
        times = tuple(i / self.rate for i in range(self.index, due))
        self.index = due
        if self.pwm:
            values = tuple((3.3 if (round(t * self.rate) % 20 + 0.5) / 20 < self.duty else 0.0,)
                           for t in times)
        else:
            values = []
            for t in times:
                since_clap = t - self.sound_time
                peak = 2.55 * math.exp(-max(since_clap, 0) * 9) if since_clap >= 0 else 0
                if t < 0:
                    peak += 2.0 * math.exp(-((t + 2.1) / 0.15) ** 2)
                values.append((0.30 + 2.4 * self.light + 0.18 * math.sin(3.0 * t),
                               1.32 + 0.25 * math.tanh(t / 5),
                               0.23 + peak + 0.04 * math.sin(47 * t),
                               2.95 if self.object_near or -3.7 < t < -2.8 else 0.10))
            values = tuple(values)
        return Packet(None, times, values)
