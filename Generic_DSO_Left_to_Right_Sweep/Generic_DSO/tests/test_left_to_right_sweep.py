import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from scope_app import ScopeWindow
from scope_core import CaptureEngine, Frame, Metadata

APP = QApplication.instance() or QApplication([])


def make_frame(times, seq=0, gap=False):
    t = np.asarray(times,dtype=float)
    return Frame(t,np.column_stack([t,t,t,t]),seq,gap)


def sweep(engine, span=5):
    return engine.capture(span,0,0,'Rising','Sweep',True)


class SweepEngineTest(unittest.TestCase):
    def test_moves_right_then_wraps_without_bridging(self):
        engine = CaptureEngine()
        engine.append(make_frame([100,100.25,102]))
        early = sweep(engine)
        self.assertTrue(early.sweep)
        np.testing.assert_allclose(early.t,[0,.25,2])
        self.assertIsNone(sweep(engine))
        engine.append(make_frame([103,104.5],1))
        later = sweep(engine)
        self.assertEqual(later.origin,100)
        self.assertEqual(later.t[-1],4.5)
        engine.append(make_frame([105,105.25],2))
        wrapped = sweep(engine)
        self.assertEqual(wrapped.origin,105)
        np.testing.assert_allclose(wrapped.t,[0,.25])
        np.testing.assert_allclose(wrapped.origin+wrapped.t,wrapped.y[:,0])

    def test_gap_restarts_sweep_and_history_is_bounded(self):
        engine = CaptureEngine()
        t=np.arange(0,100,.25)
        engine.append(make_frame(t),keep_seconds=10)
        self.assertLessEqual(engine.t[-1]-engine.t[0],10)
        sweep(engine)
        engine.append(make_frame([101.75,102],1,True),keep_seconds=10)
        restarted = sweep(engine)
        self.assertEqual(restarted.origin,101.75)
        np.testing.assert_allclose(restarted.t,[0,.25])


class SweepGuiTest(unittest.TestCase):
    def test_default_axis_marker_and_roll_button(self):
        w=ScopeWindow()
        w.timer.stop()
        w.show()
        APP.processEvents()
        try:
            channels=[{'name':name,'unit':unit} for name,unit in
                      [('Light','V'),('Temperature','°C'),('Sound','V'),('IR','logic')]]
            w.accept_metadata(Metadata('ESP32 Four Sensors',channels))
            self.assertEqual(w.mode.currentText(),'Sweep')
            self.assertTrue(w.sweep_axis.isVisible())
            np.testing.assert_allclose(w.plot.viewRange()[0],[0,5])
            w.engine.append(make_frame([100,100.25,101]))
            w.accept_capture(sweep(w.engine))
            w._render_capture()
            self.assertTrue(w.sweep_marker.isVisible())
            self.assertEqual(w.sweep_marker.value(),1)
            w.sweep_button.click()
            self.assertEqual(w.mode.currentText(),'Roll')
            w.engine.append(make_frame([101.25,101.5],1))
            w.accept_capture(w.engine.capture(5,0,0,'Rising','Roll',True))
            w._render_capture()
            self.assertFalse(w.sweep_marker.isVisible())
            self.assertFalse(w.sweep_axis.isVisible())
        finally:
            w.close()
            w.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
