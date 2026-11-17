"""Generic DSO: a native instrument for real, timestamped hardware measurements."""
import csv
import json
import math
from pathlib import Path
import queue
import sys
import time
import traceback
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,
    QGridLayout,QLabel,QPushButton,QComboBox,QDoubleSpinBox,QSpinBox,QLineEdit,
    QGroupBox,QCheckBox,QFrame,QFileDialog,QMessageBox,QDialog,QDialogButtonBox,
    QFormLayout,QScrollArea)
import pyqtgraph as pg
from scope_core import Metadata, Frame, CaptureEngine, measurements
from transport import Reader

COLORS = ['#f9d84b','#54d8ee','#c898ff','#7ede97']
BG, PANEL, TEXT, MUTED = '#11161d','#1a222c','#e8eef5','#8b9bac'
SCALES = [1e-6,2e-6,5e-6,1e-5,2e-5,5e-5,.0001,.0002,.0005,.001,.002,.005,.01,.02,.05,.1,.2,.5,1,2,5,10,20,50,100,200,500,1000]
TIME_SCALES = [1e-6,2e-6,5e-6,1e-5,2e-5,5e-5,.0001,.0002,.0005,.001,.002,.005,.01,.02,.05,.1,.2,.5,1,2,5,10,20,50,100]
SENSOR_UNITS = {'Device units':None,'Voltage':'V','Temperature':'°C','Current':'A',
                'Pressure':'Pa','Acceleration':'m/s²','Light':'lx','Humidity':'%RH',
                'Speed':'RPM','Position':'mm','Digital level':'logic','Custom':None}


def fmt(value, unit=''):
    if value is None or not math.isfinite(float(value)): return '—'
    if value == 0: return ('0 '+unit).strip()
    for factor, prefix in [(1e9,'G'),(1e6,'M'),(1e3,'k'),(1,''),(1e-3,'m'),(1e-6,'µ'),(1e-9,'n')]:
        if abs(value) >= factor:
            return f'{value/factor:.5g} {prefix}{unit}'.strip()
    return f'{value:.4g} {unit}'.strip()


def combo(items):
    w=QComboBox(); w.addItems(items); return w


def spin(value=0, low=-1e9, high=1e9, decimals=6):
    w=QDoubleSpinBox(); w.setDecimals(decimals); w.setRange(low,high); w.setValue(value)
    w.setKeyboardTracking(False); return w


class Channel:
    def __init__(self,i):
        self.name=f'CH{i+1}'; self.unit='V'; self.gain=1.; self.offset=0.; self.sensor='Device units'


class ProfileDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent); self.setWindowTitle('Device & sensor configuration'); self.resize(790,500)
        layout=QVBoxLayout(self)
        note=QLabel('Profiles label and calibrate received data. Your board needs compatible firmware.\n'
                    'Converted value = received value × gain + offset. Changing units alone is not a conversion.')
        note.setWordWrap(True); layout.addWidget(note)
        form=QFormLayout()
        self.board=combo(['Generic / Custom','LPC family','Arduino family','ESP32 / ESP8266','RP2040 / RP2350','STM32 family','Raspberry Pi / Linux','Other microprocessor'])
        self.board.setCurrentText(parent.board_profile); form.addRow('Board profile',self.board); layout.addLayout(form)
        grid=QGridLayout(); layout.addLayout(grid)
        for j,s in enumerate(['Channel','Sensor / quantity','Name','Unit','Gain','Offset']): grid.addWidget(QLabel(s),0,j)
        self.fields=[]
        for i,ch in enumerate(parent.channels):
            sensor=combo(list(SENSOR_UNITS)); sensor.setCurrentText(ch.sensor)
            name=QLineEdit(ch.name); name.setMaxLength(40)
            unit=QLineEdit(ch.unit); unit.setMaxLength(16); unit.setMaximumWidth(75)
            gain=spin(ch.gain,decimals=9); offset=spin(ch.offset,decimals=9)
            sensor.currentTextChanged.connect(lambda s,u=unit: u.setText(SENSOR_UNITS[s]) if SENSOR_UNITS[s] is not None else None)
            for j,w in enumerate([QLabel(f'CH{i+1}'),sensor,name,unit,gain,offset]): grid.addWidget(w,i+1,j)
            self.fields.append((sensor,name,unit,gain,offset))
        layout.addWidget(QLabel('Calibration is user supplied. Measurement accuracy has not been certified.'))
        layout.addStretch()
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)


class ScopeWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle('Generic DSO | Hardware acquisition'); self.resize(1440,940)
        self.setMinimumSize(1120,760)
        self.channels=[Channel(i) for i in range(4)]; self.board_profile='Generic / Custom'
        self.reader=None; self.metadata=None; self.engine=CaptureEngine(); self.current=None
        self.running=False; self.single_pending=False; self.link_open=False
        self.last_rx=0.; self.last_capture=0.; self.gaps=0; self.frames=0; self.invalid=0; self.syncing=False
        self.initial_scale=True
        self.config_path=Path.home()/'.generic_dso_profiles.json'
        self.load_profiles()
        self.build_ui(); self.theme(); self.refresh_ports(); self.controls_enabled(False)
        self.timer=QTimer(self); self.timer.timeout.connect(self.poll); self.timer.start(40)
        self.update_axes(); self.update_cursor(); self.update_measurements()

    def load_profiles(self):
        try:
            obj=json.loads(self.config_path.read_text())
            self.board_profile=obj.get('board','Generic / Custom')
            for ch,raw in zip(self.channels,obj.get('channels',[])):
                for key in ['name','unit','sensor']:
                    if isinstance(raw.get(key),str): setattr(ch,key,raw[key][:40])
                for key in ['gain','offset']:
                    val=float(raw.get(key,getattr(ch,key)))
                    if math.isfinite(val) and abs(val)<=1e9: setattr(ch,key,val)
        except (OSError,ValueError,TypeError,AttributeError): pass

    def save_profiles(self):
        try: self.config_path.write_text(json.dumps({'board':self.board_profile,'channels':[vars(c) for c in self.channels]},indent=2))
        except OSError as exc: QMessageBox.warning(self,'Settings',str(exc))

    def build_ui(self):
        root=QWidget(); self.setCentralWidget(root); outer=QVBoxLayout(root); outer.setContentsMargins(18,14,18,12); outer.setSpacing(12)
        header=QHBoxLayout(); title=QLabel('GENERIC  DSO'); title.setObjectName('title'); header.addWidget(title)
        subtitle=QLabel('HARDWARE ACQUISITION'); subtitle.setObjectName('muted'); header.addWidget(subtitle); header.addStretch()
        self.state=QLabel('●  DISCONNECTED'); self.state.setObjectName('state'); header.addWidget(self.state)
        self.profile_button=QPushButton('Device & sensors'); self.profile_button.clicked.connect(self.configure); header.addWidget(self.profile_button)
        outer.addLayout(header)
        link=QHBoxLayout(); self.transport=combo(['Serial','TCP']); self.transport.currentTextChanged.connect(self.transport_changed)
        self.port=QComboBox(); self.port.setEditable(True); self.port.setMinimumWidth(180)
        self.refresh=QPushButton('Refresh'); self.refresh.clicked.connect(self.refresh_ports)
        self.rate=combo(['115200','230400','460800','921600','1000000','2000000']); self.rate.setEditable(True)
        self.rate_label=QLabel('Baud'); self.connect_button=QPushButton('Connect'); self.connect_button.setObjectName('primary'); self.connect_button.clicked.connect(self.toggle_connection)
        self.device_label=QLabel('No acquisition device'); self.device_label.setObjectName('muted')
        for w in [self.transport,self.port,self.refresh,self.rate_label,self.rate,self.connect_button]: link.addWidget(w)
        link.addSpacing(12); link.addWidget(self.device_label,1); outer.addLayout(link)
        body=QHBoxLayout(); body.setSpacing(14); outer.addLayout(body,1)
        left=QVBoxLayout(); body.addLayout(left,1)
        strip=QHBoxLayout(); self.capture_label=QLabel('WAITING FOR HARDWARE'); self.capture_label.setObjectName('muted')
        strip.addWidget(self.capture_label); strip.addStretch(); self.sample_label=QLabel('Sample timing  —     Record  —'); strip.addWidget(self.sample_label); left.addLayout(strip)
        pg.setConfigOptions(antialias=False,background='#080d13',foreground=MUTED)
        self.plot=pg.PlotWidget(); self.plot.setMinimumHeight(300); self.plot.setMenuEnabled(False)
        self.plot.hideButtons(); self.plot.setMouseEnabled(x=False,y=False)
        self.plot.getPlotItem().setLabel('bottom','Time',units='s')
        self.plot.getPlotItem().setLabel('left','Channel divisions')
        self.plot.showGrid(x=True,y=True,alpha=.22)
        self.plot.getAxis('left').setTicks([[(x,str(x)) for x in range(-4,5)]])
        self.plot.setYRange(-4,4,padding=0)
        self.curves=[self.plot.plot([],[],pen=pg.mkPen(c,width=1.4),connect='finite') for c in COLORS]
        for curve in self.curves: curve.setClipToView(True)
        self.trigger_line=pg.InfiniteLine(angle=0,movable=True,pen=pg.mkPen('#f18f5c',style=Qt.DashLine))
        self.trigger_line.sigPositionChangeFinished.connect(self.trigger_dragged); self.plot.addItem(self.trigger_line); self.trigger_line.hide()
        self.trigger_time=pg.InfiniteLine(angle=90,movable=False,pen=pg.mkPen('#768699',style=Qt.DotLine)); self.plot.addItem(self.trigger_time); self.trigger_time.hide()
        self.cursors=[]
        for pos,angle,color in [(-.002,90,'#dbe6f3'),(.002,90,'#dbe6f3'),(-1,0,'#a9b7ca'),(1,0,'#a9b7ca')]:
            line=pg.InfiniteLine(pos=pos,angle=angle,movable=True,pen=pg.mkPen(color,style=Qt.DashLine),hoverPen=pg.mkPen('white',width=2))
            line.sigPositionChanged.connect(self.update_cursor); self.plot.addItem(line); line.hide(); self.cursors.append(line)
        self.empty=pg.TextItem('Connect an acquisition device\nReal hardware samples appear here',color=MUTED,anchor=(.5,.5))
        self.plot.addItem(self.empty); left.addWidget(self.plot,1)
        channel_row=QHBoxLayout(); self.channel_checks=[]; self.vdivs=[]; self.positions=[]
        for i in range(4):
            box=QGroupBox(); box.setStyleSheet(f'QGroupBox {{ border-top: 2px solid {COLORS[i]}; }}')
            form=QVBoxLayout(box); check=QCheckBox(f'CH{i+1}  {self.channels[i].name}'); check.setChecked(i==0); check.setStyleSheet(f'color:{COLORS[i]}')
            check.toggled.connect(self.draw_capture); form.addWidget(check); self.channel_checks.append(check)
            scale=QComboBox()
            for v in SCALES: scale.addItem(fmt(v),v)
            scale.setCurrentIndex(SCALES.index(1)); scale.currentIndexChanged.connect(self.draw_capture); self.vdivs.append(scale)
            r=QHBoxLayout(); r.addWidget(QLabel('Value/div')); r.addWidget(scale,1); form.addLayout(r)
            position=spin(0); position.valueChanged.connect(self.draw_capture); self.positions.append(position)
            r=QHBoxLayout(); r.addWidget(QLabel('Centre')); r.addWidget(position,1); form.addLayout(r); channel_row.addWidget(box,1)
        left.addLayout(channel_row)
        metrics=QHBoxLayout(); self.metrics={}
        for key,title in [('frequency','FREQUENCY'),('period','PERIOD'),('vpp','PEAK–PEAK'),('rms','RMS'),('mean','MEAN')]:
            card=QFrame(); card.setObjectName('metric'); v=QVBoxLayout(card); label=QLabel(title); label.setObjectName('small'); value=QLabel('—'); value.setObjectName('reading')
            v.addWidget(label); v.addWidget(value); self.metrics[key]=value; metrics.addWidget(card,1)
        left.addLayout(metrics)
        side_widget=QWidget(); side=QVBoxLayout(side_widget); side.setContentsMargins(0,0,6,0); side.setSpacing(12)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame); scroll.setWidget(side_widget); scroll.setFixedWidth(330); body.addWidget(scroll)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        group=QGroupBox('ACQUISITION'); g=QGridLayout(group)
        self.run_button=QPushButton('Run'); self.run_button.setObjectName('run'); self.run_button.clicked.connect(self.toggle_run)
        self.single_button=QPushButton('Single'); self.single_button.clicked.connect(self.single)
        self.auto_button=QPushButton('Auto Scale'); self.auto_button.clicked.connect(self.autoscale)
        g.addWidget(self.run_button,0,0); g.addWidget(self.single_button,0,1); g.addWidget(self.auto_button,1,0,1,2); side.addWidget(group)
        group=QGroupBox('HORIZONTAL'); form=QFormLayout(group)
        self.tdiv=QComboBox()
        for v in TIME_SCALES: self.tdiv.addItem(fmt(v,'s')+'/div',v)
        self.tdiv.setCurrentIndex(TIME_SCALES.index(.001)); self.tdiv.currentIndexChanged.connect(self.time_changed)
        self.pan=spin(0,decimals=6); self.pan.valueChanged.connect(self.update_axes)
        form.addRow('Time base',self.tdiv); form.addRow('Position (s)',self.pan); side.addWidget(group)
        group=QGroupBox('TRIGGER'); form=QFormLayout(group)
        self.mode=combo(['Auto','Normal']); self.source=combo(['CH1','CH2','CH3','CH4']); self.slope=combo(['Rising','Falling']); self.level=spin(0)
        self.mode.currentTextChanged.connect(self.trigger_changed); self.source.currentIndexChanged.connect(self.trigger_changed); self.slope.currentTextChanged.connect(self.trigger_changed); self.level.valueChanged.connect(self.trigger_changed)
        form.addRow('Mode',self.mode); form.addRow('Source',self.source); form.addRow('Edge',self.slope); form.addRow('Level',self.level)
        note=QLabel('Software edge trigger • 20% pre-trigger'); note.setObjectName('small'); form.addRow(note); side.addWidget(group)
        group=QGroupBox('CURSORS & MEASUREMENTS'); form=QFormLayout(group)
        self.measure_source=combo(['CH1','CH2','CH3','CH4']); self.measure_source.currentIndexChanged.connect(self.measure_changed)
        self.cursor_mode=combo(['Off','Time','Value','Time + Value']); self.cursor_mode.currentTextChanged.connect(self.update_cursor)
        self.reset_cursor_button=QPushButton('Centre cursors'); self.reset_cursor_button.clicked.connect(self.reset_cursors)
        form.addRow('Channel',self.measure_source); form.addRow('Cursors',self.cursor_mode); form.addRow(self.reset_cursor_button)
        self.cursor_readout=QLabel('Enable cursors after a capture'); self.cursor_readout.setObjectName('cursor'); self.cursor_readout.setWordWrap(True); form.addRow(self.cursor_readout); side.addWidget(group)
        group=QGroupBox('CAPTURE'); g=QVBoxLayout(group)
        self.csv_button=QPushButton('Export samples · CSV'); self.csv_button.clicked.connect(self.export_csv)
        self.png_button=QPushButton('Save screen · PNG'); self.png_button.clicked.connect(self.export_png)
        self.help_button=QPushButton('Connection guide'); self.help_button.clicked.connect(self.help)
        for w in [self.csv_button,self.png_button,self.help_button]: g.addWidget(w)
        side.addWidget(group); side.addStretch()
        self.notice=QLabel('Hardware only  •  No samples received  •  Accuracy requires hardware calibration'); self.notice.setObjectName('notice'); outer.addWidget(self.notice)

    def theme(self):
        self.setStyleSheet(f'''
            QWidget {{background:{BG};color:{TEXT};font-family:"Segoe UI";font-size:12px;}}
            QLabel#title {{font-size:24px;font-weight:700;letter-spacing:2px;}}
            QLabel#muted,QLabel#small {{color:{MUTED};}} QLabel#small {{font-size:10px;}}
            QLabel#state {{color:#a4b1c2;background:#232d39;padding:8px 12px;border-radius:4px;}}
            QGroupBox {{background:{PANEL};border:1px solid #2a3542;border-radius:5px;margin-top:10px;padding:13px 9px 8px;font-size:11px;font-weight:600;}}
            QGroupBox::title {{subcontrol-origin:margin;left:10px;padding:0 4px;}}
            QGroupBox QLabel,QGroupBox QCheckBox {{background:transparent;}}
            QPushButton {{background:#273340;border:1px solid #3b4a5d;border-radius:4px;padding:8px 12px;}}
            QPushButton:hover {{background:#36465a;}} QPushButton:disabled {{color:#637080;border-color:#293440;background:#1b242e;}}
            QPushButton#primary {{background:#245e8a;border:1px solid #347bab;}}
            QPushButton#run {{background:#245441;border-color:#33745a;}}
            QComboBox,QDoubleSpinBox,QSpinBox,QLineEdit {{background:#0e151e;border:1px solid #3a4758;border-radius:3px;padding:5px;min-height:20px;}}
            QComboBox:disabled,QDoubleSpinBox:disabled {{color:#627183;}}
            QComboBox QAbstractItemView {{selection-background-color:#285c82;}}
            QFrame#metric {{background:{PANEL};border:1px solid #2a3542;border-radius:4px;}}
            QFrame#metric QLabel {{background:transparent;}} QLabel#reading {{font-size:21px;font-weight:600;}}
            QLabel#cursor {{background:#101720;font-family:Consolas;font-size:12px;padding:8px;min-height:64px;}}
            QLabel#notice {{color:#9baabd;font-size:11px;padding:5px 0;}}
            QScrollBar:vertical {{width:7px;background:#101720;}} QScrollBar::handle:vertical {{background:#3a4758;min-height:24px;}}
        ''')

    def transport_changed(self):
        tcp=self.transport.currentText()=='TCP'; self.refresh.setEnabled(not tcp)
        self.port.clear(); self.rate.clear()
        if tcp: self.port.addItem('127.0.0.1'); self.rate.addItem('9000')
        else: self.rate.addItems(['115200','230400','460800','921600','1000000','2000000']); self.refresh_ports()
        self.rate_label.setText('Port' if tcp else 'Baud')

    def refresh_ports(self):
        old=self.port.currentText(); self.port.clear()
        try:
            from serial.tools.list_ports import comports
            self.port.addItems([p.device for p in comports()])
        except ImportError: pass
        if old: self.port.setCurrentText(old)
        self.port.setPlaceholderText('Select COM port')

    def controls_enabled(self,ready):
        for w in [self.run_button,self.single_button]: w.setEnabled(ready)
        for i in range(4):
            present=self.metadata is not None and i<len(self.metadata.channels)
            for w in [self.channel_checks[i],self.vdivs[i],self.positions[i]]: w.setEnabled(present)
        has=self.current is not None
        for w in [self.auto_button,self.csv_button,self.cursor_mode,self.reset_cursor_button]: w.setEnabled(has)

    def toggle_connection(self):
        if self.reader is not None: self.disconnect(); return
        try:
            address=self.port.currentText().strip(); rate=int(self.rate.currentText())
            if not address or rate<=0 or (self.transport.currentText()=='TCP' and rate>65535): raise ValueError()
        except ValueError: QMessageBox.warning(self,'Connection','Select a port/address and a valid baud rate or TCP port.'); return
        self.clear_capture(); self.engine.clear(); self.metadata=None; self.gaps=self.frames=0; self.last_rx=0
        self.reader=Reader(self.transport.currentText(),address,rate); self.reader.start()
        self.connect_button.setText('Disconnect'); self.state.setText('●  CONNECTING')
        for w in [self.port,self.rate,self.transport,self.refresh]: w.setEnabled(False)
        self.controls_enabled(False)

    def disconnect(self,error=None):
        if self.reader: self.reader.close()
        self.reader=None; self.link_open=False; self.running=False; self.single_pending=False
        self.run_button.setText('Run'); self.connect_button.setText('Connect'); self.state.setText('●  DISCONNECTED')
        self.capture_label.setText('DISCONNECTED · LAST CAPTURE HELD' if self.current else 'WAITING FOR HARDWARE')
        self.notice.setText(error or 'Disconnected • last capture is held, if available')
        for w in [self.port,self.rate,self.transport]: w.setEnabled(True)
        self.refresh.setEnabled(self.transport.currentText()=='Serial'); self.controls_enabled(False)

    def accept_metadata(self,meta):
        if meta==self.metadata: return
        self.metadata=meta; self.engine.clear(); self.clear_capture(); self.running=False; self.single_pending=False
        self.initial_scale=True
        self.device_label.setText(meta.device); self.state.setText('●  READY'); self.capture_label.setText('READY · PRESS RUN')
        self.run_button.setText('Run')
        for i,raw in enumerate(meta.channels):
            ch=self.channels[i]
            if ch.sensor=='Device units': ch.name,ch.unit=raw['name'],raw['unit']
        self.source.clear(); self.measure_source.clear()
        self.source.addItems([f'CH{i+1}' for i in range(len(meta.channels))]); self.measure_source.addItems([f'CH{i+1}' for i in range(len(meta.channels))])
        for i,check in enumerate(self.channel_checks): check.setChecked(i<len(meta.channels))
        self.channel_labels(); self.controls_enabled(True)

    def channel_labels(self):
        for i,ch in enumerate(self.channels):
            self.channel_checks[i].setText(f'CH{i+1}  {ch.name} [{ch.unit}]')
            for j,v in enumerate(SCALES): self.vdivs[i].setItemText(j,fmt(v,ch.unit))
        self.draw_capture()

    def poll(self):
        reader=self.reader
        if reader is None: return
        for _ in range(64):
            try: kind,item=reader.events.get_nowait()
            except queue.Empty: break
            if kind=='error': self.disconnect(item); QMessageBox.warning(self,'Hardware connection',item); return
            if kind=='connected': self.link_open=True; self.state.setText('●  WAITING FOR DEVICE'); self.capture_label.setText('CONNECTED · WAITING FOR METADATA')
            if kind=='data':
                if isinstance(item,Metadata): self.accept_metadata(item)
                else:
                    self.last_rx=time.monotonic(); self.frames+=1
                    if item.gap: self.gaps+=1
                    if self.running:
                        y=item.y.copy()
                        for i in range(y.shape[1]): y[:,i]=y[:,i]*self.channels[i].gain+self.channels[i].offset
                        if not np.all(np.isfinite(y)):
                            self.disconnect('Calibration overflow; reduce gain/offset'); return
                        self.engine.append(Frame(item.t,y,item.seq,item.gap,item.clipped))
        now=time.monotonic()
        if self.running and self.metadata:
            if self.initial_scale and len(self.engine.t)>=2:
                dt=float(np.median(np.diff(self.engine.t)))
                if self.tdiv.currentData()<dt*2:
                    choice=next((v for v in TIME_SCALES if v>=dt*2),TIME_SCALES[-1])
                    self.tdiv.blockSignals(True); self.tdiv.setCurrentIndex(TIME_SCALES.index(choice)); self.tdiv.blockSignals(False); self.update_axes()
                self.initial_scale=False
            auto=now-self.last_capture>=.5
            capture=self.engine.capture(10*self.tdiv.currentData(),max(0,self.source.currentIndex()),self.level.value(),self.slope.currentText(),'Normal' if self.single_pending else self.mode.currentText(),auto,prefer_newest=not self.single_pending)
            if capture is not None:
                self.current=capture; self.last_capture=now; self.draw_capture(); self.update_measurements(); self.controls_enabled(True)
                if self.single_pending:
                    self.running=False; self.single_pending=False; self.state.setText('●  STOPPED'); self.run_button.setText('Run'); self.capture_label.setText('SINGLE · CAPTURE COMPLETE')
                else: self.capture_label.setText('TRIGGERED' if capture.triggered else 'AUTO · FREE RUN')
            elif now-self.last_capture>1: self.capture_label.setText('ARMED · WAITING FOR EDGE / RECORD' if self.single_pending or self.mode.currentText()=='Normal' else 'WAITING FOR SAMPLES')
        stale=not self.last_rx or now-self.last_rx>max(3.,20*self.tdiv.currentData())
        parts=[f'Frames {self.frames}',f'Gaps {self.gaps}',f'Invalid {reader.invalid}',f'Queue drops {reader.dropped}']
        if stale: parts.append('No recent samples')
        if self.current and self.current.clipped: parts.append('CLIPPING / RANGE LIMIT')
        if reader.last_error: parts.append(reader.last_error[:100])
        else: parts.append('Unfiltered • calibration unverified')
        self.notice.setText('  |  '.join(parts))

    def toggle_run(self):
        if not self.link_open or self.metadata is None: return
        if self.running:
            self.running=False; self.single_pending=False; self.run_button.setText('Run'); self.state.setText('●  STOPPED'); self.capture_label.setText('STOPPED · CAPTURE HELD')
        else: self.arm(False)

    def single(self):
        if self.link_open and self.metadata is not None: self.arm(True)

    def arm(self,single):
        self.engine.clear(); self.running=True; self.single_pending=single; self.last_capture=time.monotonic()
        self.run_button.setText('Stop'); self.state.setText('●  SINGLE ARMED' if single else '●  RUNNING'); self.capture_label.setText('WAITING FOR NEW HARDWARE SAMPLES')

    def clear_capture(self):
        self.current=None
        for curve in self.curves: curve.setData([],[])
        self.empty.show(); self.trigger_time.hide(); self.update_measurements(); self.update_cursor()

    def time_changed(self):
        self.initial_scale=False; self.engine.clear(); self.update_axes()

    def update_axes(self):
        span=10*self.tdiv.currentData(); centre=self.pan.value()
        low=centre-.2*span; high=centre+.8*span
        self.plot.setXRange(low,high,padding=0); self.plot.setYRange(-4,4,padding=0)
        ticks=[(low+i*span/10,fmt(low+i*span/10,'s')) for i in range(11)]
        self.plot.getAxis('bottom').setTicks([ticks]); self.empty.setPos((low+high)/2,0)

    def draw_capture(self,*_):
        if not hasattr(self,'trigger_line') or not hasattr(self,'source'): return
        c=self.current
        if c is not None:
            self.empty.hide()
            for i,curve in enumerate(self.curves):
                if i<c.y.shape[1] and self.channel_checks[i].isChecked():
                    curve.setData(c.t,(c.y[:,i]-self.positions[i].value())/self.vdivs[i].currentData(),symbol='o' if len(c.t)<128 else None,symbolSize=3,symbolBrush=COLORS[i],symbolPen=None)
                else: curve.setData([],[])
            self.trigger_time.setVisible(c.triggered)
        ch=max(0,self.source.currentIndex())
        self.syncing=True
        self.trigger_line.setValue((self.level.value()-self.positions[ch].value())/self.vdivs[ch].currentData())
        self.syncing=False; self.trigger_line.setVisible(self.metadata is not None)
        self.update_cursor()

    def trigger_dragged(self):
        if self.syncing: return
        ch=max(0,self.source.currentIndex())
        self.level.setValue(self.trigger_line.value()*self.vdivs[ch].currentData()+self.positions[ch].value())

    def trigger_changed(self,*_):
        self.engine.last_trigger=-math.inf
        if hasattr(self,'vdivs'): self.draw_capture()

    def measure_changed(self,*_): self.update_measurements(); self.update_cursor()

    def update_measurements(self):
        if not hasattr(self,'metrics'): return
        if self.current is None:
            for v in self.metrics.values(): v.setText('—')
            self.sample_label.setText('Sample timing  —     Record  —'); return
        ch=max(0,self.measure_source.currentIndex())
        if ch>=self.current.y.shape[1]: return
        m=measurements(self.current.t,self.current.y[:,ch]); unit=self.channels[ch].unit
        for key,w in self.metrics.items(): w.setText(fmt(m[key],'Hz' if key=='frequency' else 's' if key=='period' else unit))
        dt=np.diff(self.current.t); median=float(np.median(dt))
        jitter=np.max(np.abs(dt-median))/median if median>0 else math.inf
        label='Uniform' if jitter<.01 else 'Irregular'
        self.sample_label.setText(f'{label} · {fmt(1/median,"Sa/s")} median     {len(self.current.t):,} points')

    def update_cursor(self,*_):
        if not hasattr(self,'cursor_mode'): return
        mode=self.cursor_mode.currentText(); has=self.current is not None
        for i,line in enumerate(self.cursors): line.setVisible(has and (mode in ('Time','Time + Value') if i<2 else mode in ('Value','Time + Value')))
        if not has: self.cursor_readout.setText('Waiting for a real capture'); return
        if mode=='Off': self.cursor_readout.setText('Cursors off'); return
        ch=max(0,self.measure_source.currentIndex()); unit=self.channels[ch].unit; lines=[]
        if mode in ('Time','Time + Value'):
            a,b=[line.value() for line in self.cursors[:2]]; delta=b-a
            lines.extend([f'T1  {fmt(a,"s")}    T2  {fmt(b,"s")}',f'ΔT  {fmt(delta,"s")}',f'1/|ΔT|  {fmt(1/abs(delta),"Hz") if abs(delta)>1e-15 else "—"}'])
        if mode in ('Value','Time + Value'):
            a,b=[line.value()*self.vdivs[ch].currentData()+self.positions[ch].value() for line in self.cursors[2:]]
            lines.extend([f'Y1  {fmt(a,unit)}',f'Y2  {fmt(b,unit)}',f'ΔY  {fmt(b-a,unit)}'])
        self.cursor_readout.setText('\n'.join(lines))

    def reset_cursors(self):
        span=10*self.tdiv.currentData(); centre=self.pan.value()
        for line,value in zip(self.cursors,[centre,centre+span*.3,-1,1]): line.setValue(value)

    def autoscale(self):
        if self.current is None: return
        for i in range(self.current.y.shape[1]):
            y=self.current.y[:,i]; extent=max(float(np.ptp(y))/6,1e-6)
            choice=next((v for v in SCALES if v>=extent),SCALES[-1]); self.vdivs[i].setCurrentIndex(SCALES.index(choice))
            self.positions[i].setValue(float((np.max(y)+np.min(y))/2))
        duration=float(self.current.t[-1]-self.current.t[0]); target=max(duration/8,1e-6)
        choice=next((v for v in TIME_SCALES if v>=target),TIME_SCALES[-1]); self.tdiv.setCurrentIndex(TIME_SCALES.index(choice)); self.pan.setValue(0)
        ch=max(0,self.source.currentIndex()); self.level.setValue(float(np.mean(self.current.y[:,ch]))); self.draw_capture(); self.reset_cursors()

    def configure(self):
        dlg=ProfileDialog(self)
        if dlg.exec()!=QDialog.Accepted: return
        self.board_profile=dlg.board.currentText()
        for ch,fields in zip(self.channels,dlg.fields):
            sensor,name,unit,gain,offset=fields
            ch.sensor,ch.name,ch.unit,ch.gain,ch.offset=sensor.currentText(),name.text().strip() or 'Input',unit.text().strip(),gain.value(),offset.value()
        self.running=False; self.single_pending=False; self.run_button.setText('Run'); self.engine.clear(); self.clear_capture()
        self.channel_labels(); self.save_profiles(); self.controls_enabled(self.link_open and self.metadata is not None)
        self.capture_label.setText('PROFILE UPDATED · PRESS RUN' if self.metadata else 'WAITING FOR HARDWARE')
        self.state.setText('●  READY' if self.metadata else '●  DISCONNECTED')

    def export_csv(self):
        if self.current is None: return
        path,_=QFileDialog.getSaveFileName(self,'Export measured samples','dso_capture.csv','CSV (*.csv)')
        if not path: return
        try:
            c=self.current
            with open(path,'w',newline='',encoding='utf-8') as f:
                w=csv.writer(f); w.writerow(['device_time_s','relative_time_s']+[f'CH{i+1}_{self.channels[i].name} [{self.channels[i].unit}]' for i in range(c.y.shape[1])])
                w.writerows([[float(t+c.origin),float(t),*row] for t,row in zip(c.t,c.y)])
            Path(path).with_suffix('.metadata.json').write_text(json.dumps({'device':self.metadata.device if self.metadata else '', 'channels':[vars(ch) for ch in self.channels[:c.y.shape[1]]], 'triggered':c.triggered,'clipped_or_range_limit':c.clipped,'accuracy':'unverified','filter':'none'},indent=2))
        except OSError as exc: QMessageBox.warning(self,'Export',str(exc))

    def export_png(self):
        path,_=QFileDialog.getSaveFileName(self,'Save instrument screen','dso_screen.png','PNG (*.png)')
        if path and not self.grab().save(path): QMessageBox.warning(self,'Save','Could not save the image.')

    def help(self):
        QMessageBox.information(self,'Connect real hardware',
            '1. Load firmware or an adapter that implements docs/PROTOCOL.md.\n'
            '2. Select Serial and the COM port/baud, or TCP and host/port.\n'
            '3. Connect. The device must send hello metadata before samples.\n'
            '4. Configure channel units/calibration if needed. Press Run.\n'
            '5. Set time/div, channel scales, trigger and cursors.\n\n'
            'Board selection does not install sensor drivers or change ADC hardware.\n'
            'Normal and Single wait for a complete edge-triggered record. Auto can show untriggered data.\n'
            'Sample timestamps must come from acquisition hardware, not serial arrival time.\n'
            'Use protected acquisition inputs suitable for the signal voltage.')

    def closeEvent(self,event):
        if self.reader: self.reader.close(); self.reader.join(timeout=.5)
        event.accept()


def main():
    app=QApplication(sys.argv); app.setFont(QFont('Segoe UI',10))
    window=ScopeWindow(); window.show(); sys.exit(app.exec())


if __name__=='__main__': main()
