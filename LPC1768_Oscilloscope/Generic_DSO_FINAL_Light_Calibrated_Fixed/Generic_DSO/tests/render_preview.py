"""Synthetic VISUAL TEST ONLY: build light reference screenshots with Qt installed.

No test waveform is ever imported into the runnable hardware application.
"""
import os, sys
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication,QEvent
from scope_app import ScopeWindow
from scope_core import Capture, Metadata
app=QApplication.instance() or QApplication([])
w=ScopeWindow(); w.timer.stop()
w.accept_metadata(Metadata('SYNTHETIC VISUAL TEST · NOT HARDWARE',[
    {'name':'GPIO34','unit':'V'}]))
w.link_open=True
w.apply_tek_preset()
t=np.arange(-1.,4.,.02)
y=np.where(np.mod(t+.25,1.) < .5, 3.3, 0.).reshape(-1,1)
w.accept_capture(Capture(t,y,10.,True,False))
w.set_run_state(True); w.capture_label.setText("TRIG'D")
w.connect_button.setText('Disconnect'); w.port.setCurrentText('COM5')
w.notice.setText('SYNTHETIC VISUAL TEST FIXTURE — NOT A HARDWARE MEASUREMENT')
w.controls_enabled(True); w.update_measurements(); w.draw_capture()
for width,height in [(1280,800),(1500,970),(1920,1080)]:
    w.resize(width,height); w.show(); app.processEvents()
    w.update_axes(); QTest.qWait(45); app.processEvents()
    target=Path('previews')/f'DSO_Light_SYNTHETIC_{width}x{height}.png'
    w.grab().save(str(target))
    print(target, 'plot',w.plot.width(),w.plot.height())
    for name in w.menu_pages:
        w.show_menu(name); app.processEvents()
        assert w.run_button.isVisible() and w.single_button.isVisible() and w.auto_button.isVisible()
w.close(); w.deleteLater()
QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete); app.processEvents()
