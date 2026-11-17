"""Static regression checks that do not require a graphical display."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PwmRunFix(unittest.TestCase):
    def test_pwm_baud_is_preferred(self):
        app = (ROOT / 'scope_app.py').read_text(encoding='utf-8')
        self.assertIn("combo(['921600','115200'", app)
        self.assertIn("self.pending_run=True", app)
        self.assertIn("if auto_arm:", app)
        self.assertIn("self.arm(False)", app)
        self.assertIn("def apply_pwm_preset(self):", app)
        self.assertIn("TIME_SCALES.index(.005)", app)
        self.assertIn("self.controls_enabled(False)", app)
        self.assertIn("def toggle_run(self):", app)

    def test_pwm_firmware_handshake_baud(self):
        code = (ROOT/'examples'/'ESP32_POT_PWM_LED_DSO_GPIO34_FINAL.ino').read_text(encoding='utf-8')
        self.assertIn("constexpr uint32_t BAUD = 921600;", code)
        self.assertIn(">= 2000", code)
        self.assertIn('"type":"hello"', code.replace('\\', ''))


if __name__ == '__main__':
    unittest.main()
