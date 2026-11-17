import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from scope_app import ScopeWindow, TIME_SCALES
from scope_core import Capture, Metadata, Frame, measurements
APP = QApplication.instance() or QApplication([])

class ScopeFixture(unittest.TestCase):
    def setUp(self):
        self.w = ScopeWindow(); self.w.timer.stop()
        self.w.accept_metadata(Metadata('Test', [{'name':'A0','unit':'V'}, {'name':'CH2','unit':'V'}]))
        self.w.link_open = True
    def tearDown(self):
        self.w.close(); self.w.deleteLater(); QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete); APP.processEvents()
    def record(self, offset=0):
        t=np.arange(500)/50000-.002
        return Capture(t, np.column_stack((np.sin(2*np.pi*500*t)+offset, np.ones(500))), 0., True, False)
class RedesignTests(ScopeFixture):
    def test_empty_measurements_are_mapping(self):
        self.assertIsInstance(measurements([], []), dict)
        self.assertTrue(all(v is None for v in measurements([], []).values()))
    def test_selected_menu_has_style_and_state(self):
        self.w.show_menu('CURSOR')
        self.assertTrue(self.w.menu_buttons['CURSOR'].property('selected'))
        self.assertFalse(self.w.menu_buttons['ACQUIRE'].property('selected'))
        self.assertIn('[selected="true"]',self.w.styleSheet())
    def test_run_state_single_source(self):
        self.w.arm(False)
        self.assertEqual(self.w.run_button.text(), 'STOP')
        self.assertEqual(self.w.run_button.property('running'), True)
        self.w.toggle_run()
        self.assertEqual(self.w.run_button.text(), 'RUN')
        self.assertEqual(self.w.run_button.property('running'), False)
        self.w.disconnect(); self.assertEqual(self.w.run_button.text(),'RUN')
    def test_ac_reference_shared_with_trigger(self):
        t=np.arange(2000)/50000
        y=np.full((2000,2),100.); y[-501:]=3.
        self.w.engine.append(Frame(t,y,1))
        self.w.current=self.record(offset=7)
        self.w.couplings[0].setCurrentText('AC')
        self.w.level.setValue(.4)
        reference=self.w.trigger_engine_level()-.4
        displayed=self.w.displayed_channel(0)
        self.assertAlmostEqual(reference,3.,places=5)
        self.assertAlmostEqual(float(np.mean(self.w.current.y[:,0]-displayed)),reference,places=5)
    def test_probe_scales_position_trigger_and_division(self):
        self.w.positions[0].setValue(1.); self.w.level.setValue(.25)
        self.w.probes[0].setCurrentText('10X')
        self.assertAlmostEqual(self.w.positions[0].value(),10.)
        self.assertAlmostEqual(self.w.level.value(),2.5)
        self.assertAlmostEqual(self.w.vdivs[0].currentData(),10.)
    def test_cursor_recenter_on_timebase(self):
        self.w.current=self.record(); self.w.cursor_mode.setCurrentText('Time')
        self.w.tdiv.setCurrentIndex(TIME_SCALES.index(1e-6))
        low,high=self.w.plot.viewRange()[0]
        self.assertAlmostEqual(self.w.cursors[0].value(),low+(high-low)*.25)
        self.assertAlmostEqual(self.w.cursors[1].value(),low+(high-low)*.75)
    def test_stale_measurements_clear(self):
        self.w.current=self.record(); self.w.update_measurements()
        self.assertNotEqual(self.w.metrics['rms'].text(),'—')
        self.w.current=Capture(np.array([]),np.empty((0,2)),0,False,False)
        self.w.update_measurements()
        self.assertEqual(self.w.metrics['rms'].text(),'—')
        self.w.current=Capture(np.arange(5),np.ones((5,1)),0,False,False)
        self.w.measure_source.setCurrentIndex(1); self.w.update_measurements()
        self.assertEqual(self.w.metrics['rms'].text(),'—')
    def test_interpolation_preserves_samples_and_measurements(self):
        self.assertTrue(hasattr(self.w,'interpolation'))
        c=self.record(); self.w.current=c; saved=c.y.copy()
        self.w.draw_capture(); self.w.update_measurements()
        before=[v.text() for v in self.w.metrics.values()]
        self.w.interpolation.setCurrentText('Linear'); self.w.draw_capture(); self.w.update_measurements()
        self.assertEqual(before,[v.text() for v in self.w.metrics.values()])
        np.testing.assert_array_equal(c.y,saved)
    def test_average_and_hires_preserve_raw(self):
        self.assertTrue(hasattr(self.w,'acquisition_mode'))
        self.w.acquisition_mode.setCurrentText('Average')
        a=self.record(0); b=self.record(2)
        self.w.accept_capture(a); self.w.accept_capture(b)
        np.testing.assert_allclose(self.w.processed_y,(a.y+b.y)/2,atol=1e-12)
        np.testing.assert_array_equal(self.w.current.y,b.y)
        self.w.level.setValue(.1)
        self.assertEqual(self.w.processor.count,0)
        self.w.acquisition_mode.setCurrentText('Hi-Res')
        self.w.accept_capture(b)
        self.assertEqual(self.w.processed_y.shape,b.y.shape)
        np.testing.assert_array_equal(self.w.current.y,b.y)
    def test_channel_name_not_duplicated(self):
        self.assertEqual(self.w.channel_checks[1].text(),'CH2 (V)')
        self.assertEqual(self.w.channel_checks[0].text(),'CH1 · A0 (V)')

if __name__=='__main__': unittest.main()

class HeldCaptureTests(ScopeFixture):
    def test_stopped_average_survives_display_settings(self):
        self.w.acquisition_mode.setCurrentText('Average')
        self.w.accept_capture(self.record(0)); self.w.accept_capture(self.record(2))
        self.w.set_run_state(False); self.w.update_measurements()
        expected=self.w.displayed_channel(0).copy()
        before=self.w.metrics['mean'].text()
        self.w.vdivs[0].setCurrentIndex(self.w.vdivs[0].currentIndex()+1)
        np.testing.assert_allclose(self.w.displayed_channel(0),expected)
        self.assertEqual(self.w.metrics['mean'].text(),before)
        self.w.tdiv.setCurrentIndex(self.w.tdiv.currentIndex()+1)
        np.testing.assert_allclose(self.w.displayed_channel(0),expected)
        self.w.level.setValue(.2)
        np.testing.assert_allclose(self.w.displayed_channel(0),expected)
        self.assertEqual(self.w.processor.count,0)
    def test_held_ac_reference_does_not_drift_on_probe_change(self):
        self.w.couplings[0].setCurrentText('AC')
        self.w.accept_capture(self.record(4),[3.,1.])
        expected=self.w.displayed_channel(0).copy()
        t=np.arange(1000)/50000
        self.w.engine.append(Frame(t,np.full((1000,2),100.),1))
        self.w.probes[0].setCurrentText('10X')
        np.testing.assert_allclose(self.w.displayed_channel(0),expected*10)

class RenderingTests(ScopeFixture):
    def test_redraw_requests_coalesce(self):
        self.assertTrue(hasattr(self.w,'_render_capture'))
        from PySide6.QtTest import QTest
        QTest.qWait(40)
        rendered=[]
        actual=self.w._render_capture
        def counted():
            import time
            rendered.append(time.monotonic()); actual()
        self.w._render_capture=counted
        self.w.current=self.record()
        for _ in range(20): self.w.draw_capture()
        self.assertLessEqual(len(rendered),1)
        QTest.qWait(80)
        self.assertLessEqual(len(rendered),2)
        if len(rendered)>1: self.assertGreaterEqual(rendered[1]-rendered[0],1/30-.001)
    def test_long_measurement_fits(self):
        self.w.resize(1280,800); self.w.show(); APP.processEvents()
        label=self.w.metrics['mean']; label.setText('1.421e-17 m/s²')
        self.assertTrue(hasattr(label,'fitted_font'))
        from PySide6.QtGui import QFontMetrics
        font=label.fitted_font()
        self.assertLessEqual(QFontMetrics(font).horizontalAdvance(label.text()),label.contentsRect().width())

class ExportTests(ScopeFixture):
    def test_csv_exports_raw_record_in_hires_mode(self):
        import csv, tempfile
        from pathlib import Path
        from unittest.mock import patch
        c=self.record(); c.y[250,0]=8.
        self.w.acquisition_mode.setCurrentText('Hi-Res'); self.w.accept_capture(c)
        self.w.probes[0].setCurrentText('10X'); self.w.couplings[0].setCurrentText('AC')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'capture.csv'
            with patch('scope_app.QFileDialog.getSaveFileName',return_value=(str(path),'CSV (*.csv)')):
                self.w.export_csv()
            with path.open() as stream: rows=list(csv.reader(stream))
            actual=np.asarray([[float(x) for x in row[2:]] for row in rows[1:]])
            np.testing.assert_array_equal(actual,c.y)
            self.assertTrue(path.with_suffix('.metadata.json').exists())
