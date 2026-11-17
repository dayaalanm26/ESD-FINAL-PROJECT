"""Requires Qt dependencies: offscreen tests of light surface and Tek preset."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication,QEvent
from scope_app import ScopeWindow
APP=QApplication.instance() or QApplication([])

class LightGuiTest(unittest.TestCase):
    def setUp(self):
        self.w=ScopeWindow(); self.w.timer.stop()
    def tearDown(self):
        self.w.close(); self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete); APP.processEvents()

    def test_white_light_background(self):
        self.assertIn('#F3F6FB',self.w.styleSheet())
        self.assertIn('#FCFDFE',self.w.styleSheet())
        self.assertIsNotNone(self.w.root_scroll)
        self.assertTrue(hasattr(self.w,'calibration_button'))

    def test_tektronix_preset_sets_scope_controls_not_samples(self):
        self.w.apply_tek_preset()
        self.assertEqual(self.w.tdiv.currentData(),.5)
        self.assertEqual(self.w.vdivs[0].currentData(),1.)
        self.assertEqual(self.w.level.value(),1.65)
        self.assertEqual(self.w.positions[0].value(),1.65)
        self.assertEqual(self.w.couplings[0].currentText(),'DC')
        self.assertEqual(self.w.interpolation.currentText(),'Linear')
        self.assertIsNone(self.w.current)
        self.assertIn('awaiting',self.w.reference_status.text())

if __name__=='__main__': unittest.main()
