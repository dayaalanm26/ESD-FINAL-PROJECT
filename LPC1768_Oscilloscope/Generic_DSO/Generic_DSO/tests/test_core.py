import json
import math
import unittest
import numpy as np
from scope_core import Decoder, ProtocolError, CaptureEngine, measurements


def hello():
    return json.dumps({'type':'hello','protocol':1,'device':'ADC board',
                       'channels':[{'name':'Input','unit':'V','min':0,'max':3.3}]})


def frame(seq=0, t0=0, samples=None):
    if samples is None: samples=[[1.0],[2.0],[1.0],[0.0]]
    return json.dumps({'type':'samples','seq':seq,'t0':t0,'fs':1000,'values':samples})


class ProtocolTests(unittest.TestCase):
    def test_requires_metadata(self):
        with self.assertRaises(ProtocolError): Decoder().decode(frame())

    def test_timing_and_units(self):
        d=Decoder(); meta=d.decode(hello()); f=d.decode(frame())
        self.assertEqual(meta.channels[0]['unit'],'V')
        np.testing.assert_allclose(f.t,[0,.001,.002,.003])
        self.assertFalse(f.gap)

    def test_invalid_numeric_data(self):
        for values in ([[float('nan')]], [[float('inf')]], [['1']], [[True]], [[1,2]]):
            d=Decoder(); d.decode(hello())
            with self.assertRaises(ProtocolError): d.decode(frame(samples=values))

    def test_sequence_loss_and_overlap(self):
        d=Decoder(); d.decode(hello()); d.decode(frame())
        self.assertTrue(d.decode(frame(2,.004)).gap)
        with self.assertRaises(ProtocolError): d.decode(frame(3,.003))

    def test_duplicate_sequence_rejected(self):
        d=Decoder(); d.decode(hello()); d.decode(frame())
        with self.assertRaises(ProtocolError): d.decode(frame(0,.004))

    def test_irregular_times_preserved(self):
        d=Decoder(); d.decode(hello())
        f=d.decode(json.dumps({'type':'samples','seq':0,'times':[0,.001,.003], 'values':[[1],[2],[3]]}))
        np.testing.assert_equal(f.t,[0,.001,.003])

    def test_explicit_timestamp_jump_breaks_capture(self):
        d=Decoder(); d.decode(hello())
        d.decode(json.dumps({'type':'samples','seq':0,'times':[0,.001,.002],'values':[[0],[1],[0]]}))
        f=d.decode(json.dumps({'type':'samples','seq':1,'times':[1000,1000.001],'values':[[0],[1]]}))
        self.assertTrue(f.gap)

    def test_internal_timestamp_gap_keeps_only_latest_segment(self):
        d=Decoder(); d.decode(hello())
        f=d.decode(json.dumps({'type':'samples','seq':0,'times':[0,.001,.002,100,100.001],'values':[[0],[1],[0],[1],[0]]}))
        self.assertTrue(f.gap); np.testing.assert_allclose(f.t,[100,100.001])

    def test_device_can_report_discontinuity(self):
        d=Decoder(); d.decode(hello())
        obj=json.loads(frame()); obj['discontinuity']=True
        self.assertTrue(d.decode(json.dumps(obj)).gap)

    def test_metadata_rejects_empty_or_invalid_channels(self):
        for channels in ([], [{'name':'x','unit':'V','min':2,'max':1}], [{}]*5):
            with self.assertRaises(ProtocolError):
                Decoder().decode(json.dumps({'type':'hello','protocol':1,'device':'x','channels':channels}))


class CaptureTests(unittest.TestCase):
    def test_empty_engine_has_no_capture(self):
        self.assertIsNone(CaptureEngine().capture(.01,0,.5,'Rising','Auto',True))

    def test_trigger_and_single_record_geometry(self):
        d=Decoder(); d.decode(hello()); e=CaptureEngine()
        vals=[[0.0] for _ in range(30)]+[[1.0] for _ in range(70)]
        e.append(d.decode(frame(samples=vals)))
        c=e.capture(.04,0,.5,'Rising','Normal',False)
        self.assertIsNotNone(c)
        self.assertTrue(c.triggered)
        self.assertLess(c.t[0],0)
        self.assertGreater(c.t[-1],0)
        self.assertIsNone(e.capture(.04,0,.5,'Rising','Normal',False))

    def test_loss_discards_previous_segment(self):
        d=Decoder(); d.decode(hello()); e=CaptureEngine()
        e.append(d.decode(frame())); e.append(d.decode(frame(2,.004)))
        self.assertEqual(len(e.t),4)

    def test_dc_frequency_is_unavailable(self):
        m=measurements(np.arange(100)*.01,np.ones(100))
        self.assertIsNone(m['frequency']); self.assertAlmostEqual(m['rms'],1)

    def test_frequency_uses_sample_time(self):
        t=np.arange(2000)*.0001; y=2*np.sin(2*np.pi*50*t)
        m=measurements(t,y)
        self.assertAlmostEqual(m['frequency'],50,places=3)
        self.assertAlmostEqual(m['vpp'],4,places=3)
        self.assertAlmostEqual(m['rms'],math.sqrt(2),places=3)

    def test_continuous_trigger_uses_latest_complete_record(self):
        d=Decoder(); d.decode(hello()); e=CaptureEngine()
        vals=[[float((i//5)%2)] for i in range(1000)]
        e.append(d.decode(frame(samples=vals)))
        c=e.capture(.02,0,.5,'Rising','Normal',False)
        self.assertGreater(c.origin,.95)

    def test_single_trigger_uses_first_complete_record(self):
        d=Decoder(); d.decode(hello()); e=CaptureEngine()
        vals=[[float((i//5)%2)] for i in range(1000)]
        e.append(d.decode(frame(samples=vals)))
        c=e.capture(.02,0,.5,'Rising','Normal',False,prefer_newest=False)
        self.assertLess(c.origin,.02)


if __name__=='__main__': unittest.main()
