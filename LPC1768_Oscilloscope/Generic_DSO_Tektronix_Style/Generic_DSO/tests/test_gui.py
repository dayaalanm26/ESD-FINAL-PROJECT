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

    def test_tektronix_control_surface_exists(self):
        self.assertEqual(self.w.menu_title.text(), 'ACQUIRE')
        self.assertEqual(self.w.scope_brand.text(), 'Tek')
        self.assertIn('M ', self.w.bottom_status.text())
        self.assertTrue(hasattr(self.w, 'time_slider'))
        self.assertEqual(len(self.w.vdiv_sliders), 4)

    def test_time_slider_tracks_timebase_selector(self):
        target = min(self.w.time_slider.maximum(), self.w.time_slider.value() + 1)
        self.w.time_slider.setValue(target); APP.processEvents()
        self.assertEqual(self.w.tdiv.currentIndex(), target)
        self.w.tdiv.setCurrentIndex(max(0, target - 1)); APP.processEvents()
        self.assertEqual(self.w.time_slider.value(), self.w.tdiv.currentIndex())

    def test_channel_scale_slider_tracks_selector(self):
        target = min(self.w.vdiv_sliders[0].maximum(), self.w.vdiv_sliders[0].value() + 1)
        self.w.vdiv_sliders[0].setValue(target); APP.processEvents()
        self.assertEqual(self.w.vdivs[0].currentIndex(), target)
        self.w.vdivs[0].setCurrentIndex(max(0, target - 1)); APP.processEvents()
        self.assertEqual(self.w.vdiv_sliders[0].value(), self.w.vdivs[0].currentIndex())

    def test_soft_menu_switches_like_scope_menu(self):
        self.w.show_menu('TRIGGER'); APP.processEvents()
        self.assertEqual(self.w.menu_title.text(), 'TRIGGER')
        self.assertIs(self.w.menu_stack.currentWidget(), self.w.menu_pages['TRIGGER'])
        self.w.show_menu('CURSOR'); APP.processEvents()
        self.assertEqual(self.w.menu_title.text(), 'CURSOR')
        self.assertIs(self.w.menu_stack.currentWidget(), self.w.menu_pages['CURSOR'])

    def test_capture_updates_tektronix_status_readouts(self):
        self.capture(); APP.processEvents()
        self.assertIn('Trig', self.w.top_status.text())
        self.assertIn('CH1', self.w.bottom_status.text())
        self.assertIn('M ', self.w.bottom_status.text())

    def test_autoset_centres_waveform_and_trigger(self):
        self.metadata()
        t=np.linspace(-.005,.005,1001)
        y=(1.2+0.7*np.sin(2*np.pi*400*t)).reshape(-1,1)
        self.w.current=Capture(t,y,0.,True,False)
        self.w.autoset()
        self.assertAlmostEqual(self.w.positions[0].value(), 1.2, places=2)
        self.assertAlmostEqual(self.w.level.value(), 1.2, places=2)
        self.assertEqual(self.w.pan.value(), 0)

    def test_profile_dialog_fits(self):
        from scope_app import ProfileDialog
        dlg=ProfileDialog(self.w); dlg.show(); APP.processEvents()
        self.assertEqual(len(dlg.fields),4); dlg.close()


if __name__=='__main__': unittest.main()
