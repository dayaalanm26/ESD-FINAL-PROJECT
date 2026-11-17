import json
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QPointF
from PySide6.QtWidgets import QApplication

from scope_app import ScopeWindow
from scope_core import CaptureEngine, Decoder, Frame

APP=QApplication.instance() or QApplication([])
META=[{'name':name, 'unit':unit} for name,unit in
      [('Light','V'),('Potentiometer','V'),('Sound','V'),('IR Detection','logic')]]


def frame(times, seq=0):
    t=np.asarray(times,dtype=float)
    return Frame(t,np.column_stack([t*0+1, t*.1, t*0+2, t*0+1]),seq)


class RightwardRollTests(unittest.TestCase):
    def test_roll_keeps_timestamps_sorted_and_history_bounded(self):
        engine=CaptureEngine()
        engine.append(frame(np.arange(1,35,.25)),keep_seconds=10)
        capture=engine.capture(5,0,0,'Rising','Roll',True)
        self.assertTrue(capture.roll)
        self.assertEqual(capture.t[-1],0)
        self.assertTrue(np.all(np.diff(capture.t)>0))
        self.assertLessEqual(engine.t[-1]-engine.t[0],10)
        np.testing.assert_allclose(capture.origin + capture.t, capture.y[:,1] * 10)

    def test_potentiometer_and_older_sample_moves_right(self):
        d=Decoder()
        meta=d.decode(json.dumps(dict(type='hello',protocol=1,device='ESP32 Four Sensors',channels=META)))
        w=ScopeWindow()
        w.timer.stop()
        w.show()
        APP.processEvents()
        try:
            w.accept_metadata(meta)
            self.assertEqual(w.mode.currentText(),'Roll')
            self.assertEqual(w.channels[1].name,'Potentiometer')
            self.assertEqual(w.channels[1].unit,'V')
            w.engine.append(frame([10,10.25,11]))
            w.accept_capture(w.engine.capture(5,0,0,'Rising','Roll',True))
            w._render_capture()
            vb=w.plot.getViewBox()
            self.assertTrue(vb.state['xInverted'])
            self.assertTrue(w.roll_axis.isVisible())
            def x(relative_time):
                return vb.mapViewToScene(QPointF(relative_time,0)).x()
            self.assertLess(x(0),x(-1))
            first_position=x(10-w.current.origin)
            w.engine.append(frame([11.25,11.5],1))
            w.accept_capture(w.engine.capture(5,0,0,'Rising','Roll',True))
            self.assertGreater(x(10-w.current.origin),first_position)
            w.plot_view.setCurrentIndex(w.plot_view.findData(1))
            w._render_capture()
            self.assertEqual(w.measure_source.currentIndex(),1)
            self.assertEqual(w.measure_caption.text(),'MEAS CH2')
            self.assertGreater(len(w.curves[1].getData()[0]),0)
            self.assertIsNone(w.curves[0].getData()[0])
        finally:
            w.close()
            w.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
