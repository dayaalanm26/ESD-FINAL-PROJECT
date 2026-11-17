import json
import unittest

from scope_data import CaptureBuffer, Decoder, Device, Packet, ProtocolError, measure, pixel_envelope


class ProtocolTests(unittest.TestCase):
    def test_existing_firmware_v1(self):
        decoder = Decoder()
        self.assertIsNone(decoder.decode('{"type":"samples","times":[0],"values":[[0]]}'))
        hello = {"type": "hello", "protocol": 1, "device": "ESP32 POT to PWM Scope",
                 "channels": [{"name": "GPIO25 PWM via GPIO34", "unit": "V"}]}
        self.assertEqual(decoder.decode(json.dumps(hello)),
                         Device(hello["device"], (("GPIO25 PWM via GPIO34", "V"),)))
        packet = decoder.decode('{"type":"samples","seq":20,"times":[1,1.0005],"values":[[0],[3.3]]}')
        self.assertEqual(packet, Packet(20, (1.0, 1.0005), ((0.0,), (3.3,))))

    def test_sensor_packets_match_announced_channels(self):
        decoder = Decoder()
        decoder.decode(json.dumps({"type": "hello", "protocol": 1,
                                   "channels": [{"name": f"AIN{i}"} for i in range(4)]}))
        samples = '{"type":"samples","times":[0],"values":[%s]}'
        packet = decoder.decode(samples % "[0,0.2,0.7,3.3]")
        self.assertEqual(packet.values[0], (0.0, 0.2, 0.7, 3.3))
        for bad in ("[0,1]", "[0,1,2,NaN]", "[0,1,2,true]"):
            with self.assertRaises(ProtocolError):
                decoder.decode(samples % bad)

    def test_gap_and_uint32_wrap(self):
        history = CaptureBuffer()
        self.assertFalse(history.append(Packet(0xFFFFFFFF, (1,), ((0,),))))
        self.assertFalse(history.append(Packet(0, (2,), ((1,),))))
        self.assertTrue(history.append(Packet(2, (3,), ((0,),))))
        self.assertEqual(list(history.rows), [(3, (0,))])
        self.assertTrue(history.append(Packet(3, (0.5,), ((1,),))))
        self.assertEqual(history.gaps, 2)
        self.assertTrue(history.append(Packet(4, (1.1,), ((1,),), True)))


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.times = tuple(i / 2000 for i in range(500))
        self.values = tuple((3.3 if i % 20 < 7 else 0.0,) for i in range(500))
        self.history = CaptureBuffer()
        self.history.append(Packet(0, self.times, self.values))

    def test_trigger_and_legacy_pwm_measurements(self):
        capture = self.history.capture(.05, 0, 1.65, True, False)
        self.assertIsNotNone(capture.trigger)
        self.assertAlmostEqual(capture.trigger - capture.start, .01)
        result = measure(capture.times, [row[0] for row in capture.values])
        self.assertAlmostEqual(result["frequency"], 100)
        self.assertAlmostEqual(result["duty"], 35)
        self.assertAlmostEqual(result["vpp"], 3.3)

    def test_roll_and_single_wait(self):
        rolling = self.history.capture(.05, 0, 1.65, True, True, rolling=True)
        self.assertIsNone(rolling.trigger)
        self.assertAlmostEqual(rolling.times[-1], self.times[-1])
        self.assertIsNone(self.history.capture(.05, 0, 1.65, True, False, after=self.times[-1]))

    def test_constant_input_does_not_report_edge_measurements(self):
        result = measure(self.times, [3.3] * len(self.times))
        self.assertIsNone(result["frequency"])
        self.assertIsNone(result["duty"])

    def test_pixel_envelope_preserves_a_narrow_spike(self):
        signal = [0.0] * len(self.times)
        signal[231] = 3.3
        points = pixel_envelope(self.times, signal, 0.0, 0.25, 10)
        self.assertIn((self.times[231], 3.3), points)
        self.assertLess(len(points), 50)


if __name__ == "__main__":
    unittest.main()
