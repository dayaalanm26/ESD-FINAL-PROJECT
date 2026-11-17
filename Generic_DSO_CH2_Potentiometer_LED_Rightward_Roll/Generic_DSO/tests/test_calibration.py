import unittest
import numpy as np
from calibration import fit_two_point, estimate_plateaus
from scope_core import CaptureEngine, Frame, measurements


class CalibrationTests(unittest.TestCase):
    def test_recover_reference_with_known_two_point_gain_and_offset(self):
        raw=np.r_[np.full(50,.0001144),np.full(50,.002518)]
        c=fit_two_point(raw,1.,0.,0.,3.3)
        self.assertAlmostEqual(c.gain * raw.min()+c.offset,0.,places=9)
        self.assertAlmostEqual(c.gain * raw.max()+c.offset,3.3,places=9)
        self.assertTrue(c.suspicious)
        self.assertGreater(c.gain,1000.)

    def test_recalibration_inverts_previous_conversion(self):
        raw=np.r_[np.full(50,.1),np.full(50,1.1)]
        c=fit_two_point(raw*3+1,3.,1.,0.,2.)
        self.assertAlmostEqual(c.gain,2.,places=9)
        self.assertAlmostEqual(c.offset,-.2,places=9)
        self.assertFalse(c.suspicious)

    def test_rejects_flat_unsafe_reference_or_zero_gain(self):
        with self.assertRaises(ValueError): fit_two_point(np.ones(100),1.,0.,0.,3.3)
        with self.assertRaises(ValueError): fit_two_point(np.r_[np.zeros(40),np.ones(40)],1.,0.,3.3,0.)
        with self.assertRaises(ValueError): fit_two_point(np.r_[np.zeros(40),np.ones(40)],0.,0.,0.,3.3)

    def test_reference_one_hz_squarewave_measurements(self):
        # Pure numerical fixture: this test does not imitate a connected device.
        t=np.arange(0,6.,.02)
        y=np.where(np.mod(t,1.)<.5,3.3,0.)
        v=measurements(t,y)
        self.assertAlmostEqual(v['frequency'],1.,places=4)
        self.assertAlmostEqual(v['vpp'],3.3,places=4)
        self.assertAlmostEqual(v['duty'],50.,places=1)

if __name__=='__main__': unittest.main()
