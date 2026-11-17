"""Calibration/display regression tests for the user's 2026-10-25 screenshot."""
import unittest
import numpy as np
from scale_guard import fit_voltage_scale,display_range_health,raw_span_from_calibrated,suspicious_gain

SCALES = [1e-6,2e-6,5e-6,1e-5,2e-5,5e-5,.0001,.0002,.0005,.001,.002,.005,.01,.02,.05,.1,.2,.5,1,2,5,10,20,50,100]

class DisplayCalibrationRegression(unittest.TestCase):
    def test_33_volt_signal_automatically_uses_one_volt_per_division(self):
        values=np.r_[np.full(125,0.),np.full(125,3.3)]
        scale,mid=fit_voltage_scale(values,SCALES)
        self.assertEqual(scale,1.)
        self.assertAlmostEqual(mid,1.65)
        self.assertEqual(display_range_health(values,scale,mid),(0.,False))

    def test_screenshot_bug_33_volts_on_1mv_div_is_flagged(self):
        values=np.r_[np.full(125,0.),np.full(125,3.3)]
        percent, clipped=display_range_health(values,.001,.00263)
        self.assertTrue(clipped)
        self.assertGreater(percent,.45)

    def test_suspect_686_gain_also_retains_native_diagnostic(self):
        raw=np.r_[np.full(125,.000229),np.full(125,.005035)]
        correction=3.3/.004806
        calibrated=(raw-raw[0])*correction
        self.assertTrue(suspicious_gain(correction))
        self.assertAlmostEqual(raw_span_from_calibrated(calibrated,correction),.004806,places=8)
        self.assertAlmostEqual(float(np.ptp(calibrated)),3.3,places=6)

    def test_nominal_unit_gain_is_not_marked_unverified(self):
        self.assertFalse(suspicious_gain(1))
        self.assertTrue(suspicious_gain(686.62))
        self.assertTrue(suspicious_gain(0))

    def test_narrow_signal_retains_millivolt_scale(self):
        values=np.r_[np.zeros(30),np.full(30,.004)]
        self.assertEqual(fit_voltage_scale(values,SCALES)[0],.001)

if __name__=='__main__':unittest.main()
