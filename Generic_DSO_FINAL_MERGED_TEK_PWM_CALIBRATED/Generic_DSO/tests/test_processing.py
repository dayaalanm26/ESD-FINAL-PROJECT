import unittest
import numpy as np
from display_processing import AcquisitionProcessor, display_interpolate
from scope_core import Capture

class ProcessingTests(unittest.TestCase):
    def capture(self,values,dt=.001,triggered=True):
        values=np.asarray(values,dtype=float).reshape(-1,1)
        return Capture(np.arange(len(values))*dt,values,0,triggered,False)
    def test_average_uses_last_n_triggered_records(self):
        p=AcquisitionProcessor()
        for offset in [1,3,5]: result=p.process(self.capture(np.full(16,offset)),'Average',2)
        np.testing.assert_array_equal(result,np.full((16,1),4))
        self.assertEqual(p.count,2)
    def test_length_or_timing_change_resets_average(self):
        p=AcquisitionProcessor()
        p.process(self.capture(np.zeros(16)),'Average',4)
        changed=self.capture(np.ones(8))
        np.testing.assert_array_equal(p.process(changed,'Average',4),changed.y)
        changed=self.capture(np.full(8,7),dt=.002)
        np.testing.assert_array_equal(p.process(changed,'Average',4),changed.y)
        self.assertEqual(p.count,1)
    def test_untriggered_records_never_join_average(self):
        p=AcquisitionProcessor(); p.process(self.capture(np.zeros(16)),'Average',4)
        c=self.capture(np.full(16,8),triggered=False)
        np.testing.assert_array_equal(p.process(c,'Average',4),c.y); self.assertEqual(p.count,0)
    def test_hires_reduces_impulse_and_keeps_raw(self):
        p=AcquisitionProcessor(); c=self.capture([0,0,0,0,8,0,0,0,0]); raw=c.y.copy()
        result=p.process(c,'Hi-Res',hires_width=4)
        self.assertEqual(float(np.max(result)),2.); self.assertEqual(result.shape,c.y.shape)
        np.testing.assert_array_equal(c.y,raw)
    def test_sinc_retains_original_extrema_and_does_not_mutate(self):
        t=np.arange(100)/50000; y=np.sin(2*np.pi*500*t); y[47]=10
        raw=y.copy(); tx,values=display_interpolate(t,y,600)
        self.assertGreater(len(tx),len(t)); self.assertGreaterEqual(np.max(values),10)
        for time,value in zip(t,y): self.assertTrue(np.any(values[tx==time]==value))
        np.testing.assert_array_equal(y,raw)
    def test_irregular_and_dense_traces_skip_sinc(self):
        t=np.array([0,.001,.0021,.003]); y=np.array([1,4,2,3])
        tx,values=display_interpolate(t,y,600)
        np.testing.assert_array_equal(tx,t); np.testing.assert_array_equal(values,y)
        t=np.arange(2000)/50000; y=np.ones(2000)
        tx,values=display_interpolate(t,y,100)
        self.assertEqual(len(tx),len(t))
