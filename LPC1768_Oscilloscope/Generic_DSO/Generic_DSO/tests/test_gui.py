import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import unittest
import numpy as np
from PySide6.QtWidgets import QApplication
from scope_app import ScopeWindow
from scope_core import Decoder, Capture


APP=QApplication.instance() or QApplication([])


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.w=ScopeWindow(); self.w.timer.stop(); self.w.show(); APP.processEvents()
    def tearDown(self): self.w.close(); APP.processEvents()
    def metadata(self):
        d=Decoder(); self.w.accept_metadata(d.decode(json.dumps({'type':'hello','protocol':1,'device':'Test ADC','channels':[{'name':'A0','unit':'V'}]})))
        self.w.link_open=True
    def capture(self):
        self.metadata(); self.w.current=Capture(np.array([-.001,0,.001]),np.array([[1.],[2.],[3.]]),12.,True,False)
        self.w.draw_capture(); self.w.controls_enabled(True); self.w.update_measurements()
    def test_startup_empty_and_run_disabled(self):
        self.assertIsNone(self.w.current); self.assertFalse(self.w.run_button.isEnabled())
        self.assertEqual(self.w.metrics['vpp'].text(),'—')
        self.assertTrue(self.w.empty.isVisible())
    def test_metadata_does_not_generate_a_capture(self):
        self.metadata(); self.assertIsNone(self.w.current); self.assertFalse(self.w.running)
        self.assertTrue(self.w.run_button.isEnabled())
    def test_calibration_survives_connection(self):
        self.w.channels[0].gain=2; self.w.channels[0].offset=-.5
        self.metadata()
        self.assertEqual(self.w.channels[0].gain,2); self.assertEqual(self.w.channels[0].offset,-.5)
    def test_cursor_uses_real_channel_scale(self):
        self.capture(); self.w.cursor_mode.setCurrentText('Time + Value')
        self.w.cursors[0].setValue(0); self.w.cursors[1].setValue(.002)
        text=self.w.cursor_readout.text()
        self.assertIn('2 ms',text); self.assertIn('500 Hz',text); self.assertIn('2 V',text)
    def test_stop_and_disconnect_hold_capture(self):
        self.capture(); saved=self.w.current
        self.w.running=True; self.w.toggle_run(); self.assertFalse(self.w.running)
        self.w.disconnect(); self.assertIs(self.w.current,saved)
        self.assertFalse(self.w.run_button.isEnabled()); self.assertTrue(self.w.csv_button.isEnabled())
    def test_single_arms_without_generating(self):
        self.metadata(); self.w.single(); self.assertTrue(self.w.single_pending)
        self.assertIsNone(self.w.current); self.assertEqual(len(self.w.engine.t),0)
    def test_profile_dialog_fits(self):
        from scope_app import ProfileDialog
        dlg=ProfileDialog(self.w); dlg.show(); APP.processEvents()
        self.assertEqual(len(dlg.fields),4); dlg.close()


if __name__=='__main__': unittest.main()
