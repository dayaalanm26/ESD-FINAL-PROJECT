import json
import os
import queue
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from scope_app import ScopeWindow
from scope_core import Decoder, DeviceStatus, ProtocolError

APP = QApplication.instance() or QApplication([])
LIGHT = {'name': 'Light', 'unit': 'V'}
TEMP = {'name': 'DHT11 Temperature', 'unit': '°C'}
SOUND = {'name': 'Sound', 'unit': 'V'}
IR = {'name': 'IR level', 'unit': 'V'}


def hello(channels):
    return json.dumps(dict(type='hello', protocol=1, device='ESP32 Four Sensors', channels=channels))


def samples(seq, start, values, gap=False):
    times = [start + i * .005 for i in range(6)]
    return json.dumps(dict(type='samples', seq=seq, times=times,
                           values=[values] * len(times), discontinuity=gap))


class SensorProtocolTest(unittest.TestCase):
    def test_restart_and_status(self):
        d = Decoder()
        d.decode(hello([LIGHT, SOUND, IR]))
        self.assertEqual(d.decode('{"type":"status","code":"dht11_waiting"}'),
                         DeviceStatus('dht11_waiting'))
        d.decode(samples(9, 12, [1.2, 2.3, 3.3]))
        with self.assertRaises(ProtocolError):
            d.decode(samples(0, .1, [1.2, 2.3, 3.3]))
        first = d.decode(samples(0, .1, [1.2, 2.3, 3.3], True))
        self.assertTrue(first.gap)
        self.assertFalse(d.decode(samples(1, .13, [1.2, 2.3, 3.3])).gap)


class SensorGuiTest(unittest.TestCase):
    def test_three_to_four_channels_keeps_capture_running(self):
        class Feed:
            events = queue.Queue()
            invalid = dropped = 0
            last_error = ''
            def close(self): pass
            def join(self, timeout): pass
        w = ScopeWindow()
        w.timer.stop()
        w.show()
        APP.processEvents()
        try:
            d = Decoder()
            w.reader = Feed()
            w.pending_run = True
            w.reader.events.put(('connected', 'Test COM'))
            w.reader.events.put(('data', d.decode(hello([LIGHT, SOUND, IR]))))
            w.reader.events.put(('data', DeviceStatus('dht11_waiting')))
            w.poll()
            self.assertTrue(w.running)
            self.assertIn('GPIO27', w.connection_hint.text())
            self.assertEqual(w.mode.currentText(), 'Roll')
            self.assertEqual(w.channels[1].name, 'Sound')
            w.channels[1].gain = 2
            for n in range(8):
                w.reader.events.put(('data', d.decode(samples(n, .2 + n * .03, [1.2, 2, 3.3]))))
            w.last_capture = time.monotonic() - 1
            w.poll()
            self.assertIsNotNone(w.current)
            self.assertTrue(w.csv_button.isEnabled())
            self.assertEqual(w.current.y[-1].tolist(), [1.2, 4, 3.3])
            w.reader.events.put(('data', d.decode(hello([LIGHT, TEMP, SOUND, IR]))))
            w.reader.events.put(('data', DeviceStatus('ready')))
            w.poll()
            self.assertTrue(w.running)
            self.assertEqual(w.channels[1].unit, '°C')
            self.assertAlmostEqual(w.positions[1].value(), 25.)
            self.assertAlmostEqual(w.channels[1].gain, 1.)
            self.assertAlmostEqual(w.channels[2].gain, 2.)
            w.reader.events.put(('data', d.decode(samples(8, .5, [1.4, 29., 2.4, 0], True))))
            w.last_capture = time.monotonic() - 1
            w.poll()
            self.assertEqual(w.current.y[-1].tolist(), [1.4, 29., 4.8, 0.])
            self.assertFalse(w.connection_hint.isVisible())
        finally:
            w.close()
            w.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
