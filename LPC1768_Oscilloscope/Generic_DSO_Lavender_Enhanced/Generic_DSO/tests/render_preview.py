"""Test-only synthetic fixture. Never imported by the application."""
import os,sys
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
w.accept_metadata(Metadata('Screenshot fixture',[{'name':'A0','unit':'V'},{'name':'A1','unit':'V'}]))
w.link_open=True
w.positions[0].setValue(-1.35); w.positions[1].setValue(1.7)
t=np.arange(500)/50000-.002
w.accept_capture(Capture(t,np.column_stack((.9*np.sin(2*np.pi*500*t),.62*np.where(np.sin(2*np.pi*500*t)>=0,1,-1))),0,True,False))
w.set_run_state(True); w.capture_label.setText("TRIG'D")
w.connect_button.setText('Disconnect'); w.port.setCurrentText('COM5'); w.notice.setText('TEST FIXTURE · Synthetic signals for visual validation only')
w.controls_enabled(True); w.draw_capture(); w.update_measurements()
for width,height in [(1280,800),(1440,940),(1920,1080)]:
    w.resize(width,height); w.show(); app.processEvents(); w.update_axes(); QTest.qWait(40); app.processEvents()
    assert w.size().width()==width and w.size().height()==height,(w.size(),width,height)
    target=Path('previews')/f'DSO_Lavender_{width}x{height}.png'; w.grab().save(str(target))
    print(target, 'plot',w.plot.size().width(),w.plot.size().height())
    for name in w.menu_pages:
        w.show_menu(name); app.processEvents()
        assert w.run_button.isVisible() and w.single_button.isVisible() and w.auto_button.isVisible()
    w.show_menu('ACQUIRE')
w.resize(1280,800); w.channels[0].unit='m/s²'; w.channel_labels(); w.update_measurements()
w.metrics['mean'].setText('1.421e-17 m/s²'); w.metrics['mean'].setToolTip('1.421e-17 m/s²')
QTest.qWait(40); app.processEvents(); w.grab().save('previews/DSO_Units_1280x800.png')
w.close(); w.deleteLater(); QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete); app.processEvents()
