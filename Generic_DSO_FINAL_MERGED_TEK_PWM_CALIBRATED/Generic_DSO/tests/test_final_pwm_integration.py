import unittest
import json
import math
from pathlib import Path
import numpy as np
from scope_core import Decoder, Metadata, Frame, CaptureEngine, measurements
from calibration import fit_two_point

ROOT = Path(__file__).resolve().parent.parent

class MergedPwmIntegration(unittest.TestCase):
    def test_pwm_preset_is_not_clobbered_by_first_capture_autofit(self):
        text = (ROOT / 'scope_app.py').read_text(encoding='utf8')
        chunk = text.split('def apply_pwm_preset(self):')[1].split('def update_reference_status(self):')[0]
        self.assertIn('TIME_SCALES.index(.005)',chunk)
        self.assertIn('SCALES.index(1.)',chunk)
        self.assertIn('self.initial_fit_pending=False',chunk)

    def test_pwm_pot_firmware_has_real_adc_and_volts(self):
        code = (ROOT/'examples'/'ESP32_POT_PWM_LED_DSO_GPIO34_FINAL.ino').read_text()
        for item in ('SENSE_PIN     = 34', 'POT_PIN       = 35', 'PWM_PIN       = 25',
                     'POT_POWER_PIN = 26', 'DIVIDER_MULTIPLIER = 2.0f',
                     'analogReadMilliVolts(SENSE_PIN)', 'BAUD = 921600'):
            self.assertIn(item, code)

    def test_pwm_wire_protocol_and_measurement(self):
        dec = Decoder()
        hello = dict(type='hello',protocol=1,device='ESP32 POT to PWM Scope',
                     channels=[dict(name='GPIO25 PWM via GPIO34',unit='V')])
        self.assertIsInstance(dec.decode(json.dumps(hello)), Metadata)
        times = np.arange(0, .200, .0005)
        data = np.where(np.mod(times,.01)<.005,3.3,0.)
        n = 64
        engine = CaptureEngine()
        for offset in range(0,len(times),n):
            packet = dict(type='samples',seq=offset//n,times=times[offset:offset+n].round(6).tolist(),
                          values=[[float(v)] for v in data[offset:offset+n]])
            frame=dec.decode(json.dumps(packet))
            self.assertIsInstance(frame,Frame)
            engine.append(frame)
        capture=engine.capture(.05,0,1.65,'Rising','Auto',True)
        self.assertIsNotNone(capture)
        metric = measurements(capture.t,capture.y[:,0])
        self.assertAlmostEqual(metric['vpp'],3.3,places=3)
        self.assertAlmostEqual(metric['frequency'],100,delta=1)
        self.assertAlmostEqual(metric['duty'],50,delta=8)

    def test_verified_volts_gain_is_near_unity(self):
        samples=np.tile(np.r_[np.zeros(50),np.full(50,3.3)],3)
        corrected=fit_two_point(samples,1.,0.,0.,3.3)
        self.assertAlmostEqual(corrected.gain,1.,places=7)
        self.assertFalse(corrected.suspicious)

if __name__ == '__main__': unittest.main()
