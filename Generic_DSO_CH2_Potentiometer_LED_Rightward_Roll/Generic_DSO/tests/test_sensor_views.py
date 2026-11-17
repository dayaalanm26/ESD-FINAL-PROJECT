import json
import os
import queue
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from scope_app import ScopeWindow
from scope_core import Decoder, DeviceStatus, ProtocolError

APP = QApplication.instance() or QApplication([])
CHANNELS = [
    {'name':'Light','unit':'V'},
    {'name':'DHT11 Temperature','unit':'°C','nullable':True},
    {'name':'Sound','unit':'V'},
    {'name':'IR Detection','unit':'logic'},
]
HELLO = json.dumps(dict(type='hello', protocol=1, device='ESP32 Four Sensors', channels=CHANNELS))


def records(seq, temperature):
    times=[seq*.024+i*.004 for i in range(6)]
    return json.dumps(dict(type='samples',seq=seq,times=times,values=[
        [1.0,temperature,2.0,i%2] for i in range(len(times))
    ]))


class SensorViewTest(unittest.TestCase):
    def test_only_nullable_column_accepts_null(self):
        d=Decoder()
        meta=d.decode(HELLO)
        self.assertEqual(len(meta.channels),4)
        frame=d.decode(records(0,None))
        self.assertTrue(np.isnan(frame.y[:,1]).all())
        self.assertTrue(np.isfinite(frame.y[:,[0,2,3]]).all())
        packet=json.loads(records(1,28))
        packet['values'][0][0]=None
        with self.assertRaises(ProtocolError):
            d.decode(json.dumps(packet))

    def test_fixed_order_while_waiting_and_single_stacked_overlay(self):
        class Feed:
            events=queue.Queue()
            invalid=dropped=0
            last_error=''
            def close(self): pass
            def join(self,timeout): pass
        w=ScopeWindow()
        w.timer.stop()
        w.show()
        APP.processEvents()
        try:
            decoder=Decoder()
            w.reader=Feed()
            w.pending_run=True
            w.reader.events.put(('connected','Fixture'))
            w.reader.events.put(('data',decoder.decode(HELLO)))
            w.reader.events.put(('data',DeviceStatus('dht11_waiting')))
            w.reader.events.put(('data',decoder.decode(records(0,None))))
            w.poll()
            w.last_capture=0
            w.poll()
            self.assertIsNotNone(w.current)
            self.assertTrue(w.running)
            self.assertEqual(w.plot_view.currentData(),'stacked')
            self.assertIn('GPIO27',w.connection_hint.text())
            self.assertTrue(w.csv_button.isEnabled())
            w._render_capture()
            self.assertIsNone(w.curves[1].getData()[0])
            self.assertTrue(w.lane_labels[1].isVisible())
            self.assertIn('Waiting',w.lane_labels[1].toPlainText())
            for channel in [0,2,3]:
                x,y=w.curves[channel].getData()
                self.assertGreater(len(x),0)
                center=3-2*channel
                self.assertTrue(np.all(np.abs(y-center)<1))
            w.plot_view.setCurrentIndex(w.plot_view.findData(3))
            w._render_capture()
            self.assertEqual(w.measure_source.currentIndex(),3)
            self.assertIsNone(w.curves[0].getData()[0])
            self.assertGreater(len(w.curves[3].getData()[0]),0)
            w.plot_view.setCurrentIndex(w.plot_view.findData('overlay'))
            w.channel_checks[2].setChecked(False)
            w._render_capture()
            self.assertIsNone(w.curves[2].getData()[0])
            self.assertFalse(w.lane_labels[3].isVisible())
            w.reader.events.put(('data',DeviceStatus('ready')))
            w.reader.events.put(('data',decoder.decode(records(1,28))))
            w.last_capture=0
            w.poll()
            w.plot_view.setCurrentIndex(w.plot_view.findData(1))
            w._render_capture()
            self.assertEqual(w.measure_source.currentIndex(),1)
            self.assertEqual(w.metrics['mean'].text(),'28 °C')
            self.assertFalse(w.connection_hint.isVisible())
            self.assertTrue(w.running)
            self.assertEqual([channel.name for channel in w.channels],
                             ['Light','DHT11 Temperature','Sound','IR Detection'])
        finally:
            w.close()
            w.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
