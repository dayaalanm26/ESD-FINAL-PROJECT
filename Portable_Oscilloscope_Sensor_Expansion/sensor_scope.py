"""Launch the existing DSO with a preset for the ESP32 ADS1115 sensor sketch."""

import sys

from PySide6.QtWidgets import QApplication
from scope_app import ScopeWindow

SENSOR_DEVICE = "ESP32 ADS1115 Sensors"


class SensorScopeWindow(ScopeWindow):
    def __init__(self):
        self._sensor_view = False
        super().__init__()

    def accept_metadata(self, metadata):
        sensor_device = metadata.device == SENSOR_DEVICE
        entering = sensor_device and not self._sensor_view
        if self._sensor_view and not sensor_device:
            self._leave_sensor_view()
        self._sensor_view = sensor_device
        super().accept_metadata(metadata)
        if entering:
            self._saved_time_div = self.tdiv.currentData()
            self._saved_interpolation = self.interpolation.currentText()
            self._saved_trigger_mode = self.mode.currentText()
            dark_screen = getattr(self, "tek_screen_check", None)
            self._saved_dark_screen = dark_screen.isChecked() if dark_screen is not None else None
            self._set_time_div(0.5)
            self.interpolation.setCurrentText("Linear")
            self.mode.setCurrentText("Auto")
            if dark_screen is not None:
                dark_screen.setChecked(False)

    def _set_time_div(self, seconds):
        index = self.tdiv.findData(seconds)
        if index >= 0:
            self.tdiv.setCurrentIndex(index)

    def _leave_sensor_view(self):
        self._sensor_view = False
        self._set_time_div(self._saved_time_div)
        self.interpolation.setCurrentText(self._saved_interpolation)
        self.mode.setCurrentText(self._saved_trigger_mode)
        if self._saved_dark_screen is not None:
            self.tek_screen_check.setChecked(self._saved_dark_screen)

    def disconnect(self, *args, **kwargs):
        result = super().disconnect(*args, **kwargs)
        if self._sensor_view:
            self._leave_sensor_view()
        return result


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SensorScopeWindow()
    window.show()
    sys.exit(app.exec())
