"""Headless checks for truthful 100 Hz live-PWM presentation."""
import unittest
import numpy as np
from pwm_visual import pwm_hold_trace, looks_like_pwm
from scope_core import Decoder, CaptureEngine, measurements, Frame
from transport import RealtimeEventQueue
import json


class PwmScreenTests(unittest.TestCase):
    def test_pwm_classifies_and_inserts_only_visual_vertices(self):
        t=np.arange(100)*0.0005
        y=np.where(np.mod(t,.01)<.004,3.3,0.)
        raw=t.copy(),y.copy()
        self.assertTrue(looks_like_pwm(y))
        tx,vy=pwm_hold_trace(t,y)
        self.assertEqual(len(tx),2*len(t))
        self.assertEqual(float(np.max(vy)),3.3)
        self.assertEqual(float(np.min(vy)),0.)
        np.testing.assert_array_equal(t,raw[0]);np.testing.assert_array_equal(y,raw[1])
        self.assertTrue(np.all(np.diff(tx)>=0))
        self.assertTrue(np.any(np.diff(tx)==0)) # vertical transition edges

    def test_dc_noise_and_analog_sine_not_mislabeled_pwm(self):
        n=200;t=np.arange(n)/2000
        self.assertFalse(looks_like_pwm(np.ones(n)*1.65))
        self.assertFalse(looks_like_pwm(1.65+.05*np.sin(2*np.pi*100*t)))
        self.assertFalse(looks_like_pwm(1.65+1.2*np.sin(2*np.pi*100*t)))

    def test_pwm_at_many_knob_duties_preserves_physical_metrics(self):
        for duty in [0.1,.25,.5,.75,.9]:
            t=np.arange(800)*.0005
            y=np.where(np.mod(t,.01)<duty*.01,3.3,0.)
            tx,vy=pwm_hold_trace(t,y)
            m=measurements(t,y)
            self.assertAlmostEqual(m['frequency'],100,delta=3)
            self.assertAlmostEqual(m['duty'],duty*100,delta=8)
            self.assertEqual(len(vy),len(y)*2)
            self.assertAlmostEqual(np.ptp(y),3.3)

    def test_overrun_discards_oldest_frame_but_never_metadata(self):
        q=RealtimeEventQueue(3)
        q.put(('connected','COM5'))
        q.put(('data',{'hello':'keep'}))
        def f(n):return Frame(np.array([float(n)]),np.array([[3.3]]),n)
        q.put(('data',f(1)))
        q.put(('data',f(2)))
        self.assertEqual(q.dropped,1)
        self.assertEqual(q.get_nowait(),('connected','COM5'))
        self.assertEqual(q.get_nowait(),('data',{'hello':'keep'}))
        self.assertEqual(q.get_nowait()[1].seq,2)

    def test_real_json_packets_stabilize_trigger_at_100hz(self):
        dec=Decoder()
        dec.decode(json.dumps({'type':'hello','protocol':1,'device':'PWM','channels':[{'name':'GPIO34','unit':'V'}]}))
        eng=CaptureEngine()
        t=np.arange(2000)*.0005
        y=np.where(np.mod(t,.01)<.006,3.3,0.)
        for i in range(0,len(t),64):
            packet=dict(type='samples',seq=i//64,times=t[i:i+64].round(6).tolist(),values=[[float(v)] for v in y[i:i+64]])
            eng.append(dec.decode(json.dumps(packet)))
        c=eng.capture(.05,0,1.65,'Rising','Auto',True)
        self.assertIsNotNone(c)
        self.assertTrue(c.triggered)
        self.assertTrue(-.01<=c.t[0]<-.008)
        self.assertAlmostEqual(measurements(c.t,c.y[:,0])['frequency'],100,delta=2)

if __name__=='__main__':unittest.main()
