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
    QFormLayout,QScrollArea,QSlider,QStackedWidget)
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
        root=QWidget(); self.setCentralWidget(root)
        outer=QVBoxLayout(root); outer.setContentsMargins(10,8,10,8); outer.setSpacing(8)

        header=QHBoxLayout()
        title=QLabel('GENERIC DSO'); title.setObjectName('title'); header.addWidget(title)
        subtitle=QLabel('TEKTRONIX-STYLE HARDWARE OSCILLOSCOPE'); subtitle.setObjectName('muted'); header.addWidget(subtitle)
        header.addStretch(); self.state=QLabel('●  DISCONNECTED'); self.state.setObjectName('state'); header.addWidget(self.state)
        self.profile_button=QPushButton('Device & sensors'); self.profile_button.clicked.connect(self.configure); header.addWidget(self.profile_button)
        outer.addLayout(header)

        link=QHBoxLayout(); self.transport=combo(['Serial','TCP']); self.transport.currentTextChanged.connect(self.transport_changed)
        self.port=QComboBox(); self.port.setEditable(True); self.port.setMinimumWidth(170)
        self.refresh=QPushButton('Refresh'); self.refresh.clicked.connect(self.refresh_ports)
        self.rate=combo(['115200','230400','460800','921600','1000000','2000000']); self.rate.setEditable(True)
        self.rate_label=QLabel('Baud'); self.connect_button=QPushButton('Connect'); self.connect_button.setObjectName('primary'); self.connect_button.clicked.connect(self.toggle_connection)
        self.device_label=QLabel('No acquisition device'); self.device_label.setObjectName('muted')
        for w in [self.transport,self.port,self.refresh,self.rate_label,self.rate,self.connect_button]: link.addWidget(w)
        link.addSpacing(10); link.addWidget(self.device_label,1); outer.addLayout(link)

        instrument=QFrame(); instrument.setObjectName('instrument'); outer.addWidget(instrument,1)
        body=QHBoxLayout(instrument); body.setContentsMargins(8,8,8,8); body.setSpacing(8)
        screen=QVBoxLayout(); screen.setSpacing(5); body.addLayout(screen,1)

        top=QFrame(); top.setObjectName('tekBar'); top_l=QHBoxLayout(top); top_l.setContentsMargins(8,4,8,4)
        self.scope_brand=QLabel('Tek'); self.scope_brand.setObjectName('tekBrand'); top_l.addWidget(self.scope_brand)
        self.top_status=QLabel('T Ready     M Pos: 0 s'); self.top_status.setObjectName('tekStatus'); top_l.addWidget(self.top_status,1)
        self.capture_label=QLabel('WAITING FOR HARDWARE'); self.capture_label.setObjectName('tekStatus'); self.capture_label.setAlignment(Qt.AlignRight|Qt.AlignVCenter); top_l.addWidget(self.capture_label)
        screen.addWidget(top)

        pg.setConfigOptions(antialias=True,background='#020303',foreground='#8ea5b8')
        self.plot=pg.PlotWidget(); self.plot.setMinimumHeight(360); self.plot.setMenuEnabled(False)
        self.plot.hideButtons(); self.plot.setMouseEnabled(x=False,y=False); self.plot.setBackground('#020303')
        self.plot.getPlotItem().setLabel('bottom',''); self.plot.getPlotItem().setLabel('left','')
        self.plot.showGrid(x=True,y=True,alpha=.42)
        self.plot.getAxis('left').setTicks([[(x,'') for x in range(-4,5)]])
        self.plot.getAxis('left').setStyle(showValues=False, tickLength=0)
        self.plot.getAxis('bottom').setStyle(showValues=False, tickLength=0)
        self.plot.setYRange(-4,4,padding=0)
        self.curves=[self.plot.plot([],[],pen=pg.mkPen(c,width=1.9),connect='finite') for c in COLORS]
        for curve in self.curves:
            curve.setClipToView(True)
            try: curve.setDownsampling(auto=True,method='peak')
            except TypeError: pass
        self.trigger_line=pg.InfiniteLine(angle=0,movable=True,pen=pg.mkPen('#ff8a42',width=1.4,style=Qt.DashLine))
        self.trigger_line.sigPositionChangeFinished.connect(self.trigger_dragged); self.plot.addItem(self.trigger_line); self.trigger_line.hide()
        self.trigger_time=pg.InfiniteLine(angle=90,movable=False,pen=pg.mkPen('#ff9c45',width=1.1,style=Qt.DotLine)); self.plot.addItem(self.trigger_time); self.trigger_time.hide()
        self.cursors=[]
        for pos,angle,color in [(-.002,90,'#e9eef4'),(.002,90,'#e9eef4'),(-1,0,'#b8c7d6'),(1,0,'#b8c7d6')]:
            line=pg.InfiniteLine(pos=pos,angle=angle,movable=True,pen=pg.mkPen(color,width=1.2,style=Qt.DashLine),hoverPen=pg.mkPen('white',width=2))
            line.sigPositionChanged.connect(self.update_cursor); self.plot.addItem(line); line.hide(); self.cursors.append(line)
        self.empty=pg.TextItem('Connect acquisition hardware\nReal ADC samples appear here',color='#9db0c1',anchor=(.5,.5))
        self.plot.addItem(self.empty); screen.addWidget(self.plot,1)

        bottom=QFrame(); bottom.setObjectName('tekBar'); bottom_l=QHBoxLayout(bottom); bottom_l.setContentsMargins(8,4,8,4)
        self.bottom_status=QLabel('CH1 1 V/div     M 1 ms/div     CH1 / 0 V     — Hz'); self.bottom_status.setObjectName('tekStatus'); bottom_l.addWidget(self.bottom_status,1)
        self.sample_label=QLabel('Sample timing —'); self.sample_label.setObjectName('tekStatus'); self.sample_label.setAlignment(Qt.AlignRight|Qt.AlignVCenter); bottom_l.addWidget(self.sample_label)
        screen.addWidget(bottom)

        channel_row=QHBoxLayout(); channel_row.setSpacing(6)
        self.channel_checks=[]; self.vdivs=[]; self.vdiv_sliders=[]; self.positions=[]; self.position_sliders=[]
        self.couplings=[]; self.probes=[]
        for i in range(4):
            box=QGroupBox(); box.setObjectName(f'ch{i+1}Box'); box.setStyleSheet(f'QGroupBox {{ border-top: 3px solid {COLORS[i]}; }}')
            form=QVBoxLayout(box); form.setSpacing(3)
            check=QCheckBox(f'CH{i+1}  {self.channels[i].name}'); check.setChecked(i==0); check.setStyleSheet(f'color:{COLORS[i]};font-weight:700;')
            check.toggled.connect(self.draw_capture); form.addWidget(check); self.channel_checks.append(check)
            scale=QComboBox()
            for v in SCALES: scale.addItem(fmt(v),v)
            scale.setCurrentIndex(SCALES.index(1)); self.vdivs.append(scale)
            scale_slider=QSlider(Qt.Horizontal); scale_slider.setRange(0,len(SCALES)-1); scale_slider.setValue(scale.currentIndex()); self.vdiv_sliders.append(scale_slider)
            scale.currentIndexChanged.connect(lambda idx,i=i: self.scale_combo_changed(i,idx))
            scale_slider.valueChanged.connect(lambda value,i=i: self.vdivs[i].setCurrentIndex(value))
            r=QHBoxLayout(); r.addWidget(QLabel('VOLTS/DIV')); r.addWidget(scale,1); form.addLayout(r); form.addWidget(scale_slider)
            position=spin(0); position.setDecimals(5); self.positions.append(position)
            pslider=QSlider(Qt.Horizontal); pslider.setRange(-400,400); pslider.setValue(0); pslider.setToolTip('Vertical position in divisions'); self.position_sliders.append(pslider)
            position.valueChanged.connect(lambda _value,i=i: self.position_spin_changed(i))
            pslider.valueChanged.connect(lambda value,i=i: self.position_slider_changed(i,value))
            r=QHBoxLayout(); r.addWidget(QLabel('POSITION')); r.addWidget(position,1); form.addLayout(r); form.addWidget(pslider)
            coupling=combo(['DC','AC','GND']); probe=combo(['1X','10X','100X'])
            coupling.currentTextChanged.connect(lambda _text,i=i: self.channel_display_changed(i)); probe.currentTextChanged.connect(lambda _text,i=i: self.channel_display_changed(i))
            self.couplings.append(coupling); self.probes.append(probe)
            r=QHBoxLayout(); r.addWidget(coupling,1); r.addWidget(QLabel('Probe')); r.addWidget(probe,1); form.addLayout(r)
            channel_row.addWidget(box,1)
        screen.addLayout(channel_row)

        metric_grid=QGridLayout(); metric_grid.setSpacing(5); self.metrics={}
        metric_defs=[('frequency','FREQUENCY'),('period','PERIOD'),('vpp','PEAK–PEAK'),('rms','RMS'),('mean','MEAN'),('minimum','MIN'),('maximum','MAX'),('duty','DUTY')]
        for n,(key,title_text) in enumerate(metric_defs):
            card=QFrame(); card.setObjectName('metric'); v=QVBoxLayout(card); v.setContentsMargins(7,4,7,4)
            label=QLabel(title_text); label.setObjectName('small'); value=QLabel('—'); value.setObjectName('reading')
            v.addWidget(label); v.addWidget(value); self.metrics[key]=value; metric_grid.addWidget(card,n//4,n%4)
        screen.addLayout(metric_grid)

        right=QFrame(); right.setObjectName('softPanel'); right.setFixedWidth(292); rlayout=QVBoxLayout(right); rlayout.setContentsMargins(7,7,7,7); rlayout.setSpacing(6); body.addWidget(right)
        self.menu_title=QLabel('ACQUIRE'); self.menu_title.setObjectName('menuTitle'); self.menu_title.setAlignment(Qt.AlignCenter); rlayout.addWidget(self.menu_title)
        self.menu_stack=QStackedWidget(); self.menu_pages={}; rlayout.addWidget(self.menu_stack,1)

        def page(name):
            w=QWidget(); w.setObjectName('menuPage'); lay=QVBoxLayout(w); lay.setContentsMargins(2,2,2,2); lay.setSpacing(7)
            self.menu_stack.addWidget(w); self.menu_pages[name]=w; return w,lay

        acquire,lay=page('ACQUIRE')
        self.run_button=QPushButton('RUN / STOP'); self.run_button.setObjectName('run'); self.run_button.clicked.connect(self.toggle_run)
        self.single_button=QPushButton('SINGLE SEQUENCE'); self.single_button.clicked.connect(self.single)
        self.auto_button=QPushButton('AUTOSET'); self.auto_button.setObjectName('primary'); self.auto_button.clicked.connect(self.autoset)
        self.autorange_box=QCheckBox('AUTORANGE · CONTINUOUS'); self.autorange_box.setChecked(False)
        for w in [self.run_button,self.single_button,self.auto_button,self.autorange_box]: lay.addWidget(w)
        note=QLabel('Continuous, normal and single acquisition use real timestamped ADC samples.'); note.setWordWrap(True); note.setObjectName('softNote'); lay.addWidget(note); lay.addStretch()

        horizontal,lay=page('HORIZONTAL')
        self.tdiv=QComboBox()
        for v in TIME_SCALES: self.tdiv.addItem(fmt(v,'s')+'/div',v)
        self.tdiv.setCurrentIndex(TIME_SCALES.index(.001))
        self.time_slider=QSlider(Qt.Horizontal); self.time_slider.setRange(0,len(TIME_SCALES)-1); self.time_slider.setValue(self.tdiv.currentIndex())
        self.tdiv.currentIndexChanged.connect(self.time_combo_changed); self.time_slider.valueChanged.connect(self.tdiv.setCurrentIndex)
        self.pan=spin(0,decimals=6); self.pan_slider=QSlider(Qt.Horizontal); self.pan_slider.setRange(-400,400); self.pan_slider.setValue(0)
        self.pan.valueChanged.connect(self.pan_spin_changed); self.pan_slider.valueChanged.connect(self.pan_slider_changed)
        lay.addWidget(QLabel('SEC/DIV')); lay.addWidget(self.tdiv); lay.addWidget(self.time_slider); lay.addWidget(QLabel('HORIZONTAL POSITION')); lay.addWidget(self.pan); lay.addWidget(self.pan_slider); lay.addStretch()

        trigger,lay=page('TRIGGER')
        self.mode=combo(['Auto','Normal']); self.source=combo(['CH1','CH2','CH3','CH4']); self.slope=combo(['Rising','Falling']); self.level=spin(0)
        self.trigger_slider=QSlider(Qt.Horizontal); self.trigger_slider.setRange(-400,400); self.trigger_slider.setValue(0)
        self.mode.currentTextChanged.connect(self.trigger_changed); self.source.currentIndexChanged.connect(self.trigger_changed); self.slope.currentTextChanged.connect(self.trigger_changed); self.level.valueChanged.connect(self.trigger_changed)
        self.trigger_slider.valueChanged.connect(self.trigger_slider_changed)
        for label,w in [('MODE',self.mode),('SOURCE',self.source),('SLOPE',self.slope),('LEVEL',self.level)]: lay.addWidget(QLabel(label)); lay.addWidget(w)
        lay.addWidget(self.trigger_slider)
        note=QLabel('Edge trigger · 20% pre-trigger'); note.setObjectName('softNote'); lay.addWidget(note); lay.addStretch()

        cursor,lay=page('CURSOR')
        self.cursor_mode=combo(['Off','Time','Value','Time + Value']); self.cursor_mode.currentTextChanged.connect(self.update_cursor)
        self.reset_cursor_button=QPushButton('CENTRE CURSORS'); self.reset_cursor_button.clicked.connect(self.reset_cursors)
        self.cursor_readout=QLabel('Enable cursors after a capture'); self.cursor_readout.setObjectName('cursor'); self.cursor_readout.setWordWrap(True)
        lay.addWidget(QLabel('TYPE')); lay.addWidget(self.cursor_mode); lay.addWidget(self.reset_cursor_button); lay.addWidget(self.cursor_readout); lay.addStretch()

        measure,lay=page('MEASURE')
        self.measure_source=combo(['CH1','CH2','CH3','CH4']); self.measure_source.currentIndexChanged.connect(self.measure_changed)
        lay.addWidget(QLabel('SOURCE')); lay.addWidget(self.measure_source)
        self.measure_summary=QLabel('Frequency\nPeriod\nPeak-to-peak\nRMS\nMean\nMin / Max\nDuty cycle'); self.measure_summary.setObjectName('softNote'); lay.addWidget(self.measure_summary); lay.addStretch()

        save,lay=page('SAVE')
        self.csv_button=QPushButton('EXPORT SAMPLES · CSV'); self.csv_button.clicked.connect(self.export_csv)
        self.png_button=QPushButton('SAVE SCREEN · PNG'); self.png_button.clicked.connect(self.export_png)
        self.help_button=QPushButton('CONNECTION GUIDE'); self.help_button.clicked.connect(self.help)
        for w in [self.csv_button,self.png_button,self.help_button]: lay.addWidget(w)
        lay.addStretch()

        nav=QGridLayout(); self.menu_buttons={}
        for n,name in enumerate(['ACQUIRE','HORIZONTAL','TRIGGER','CURSOR','MEASURE','SAVE']):
            b=QPushButton(name); b.setObjectName('softKey'); b.clicked.connect(lambda _checked=False,name=name: self.show_menu(name)); nav.addWidget(b,n//2,n%2); self.menu_buttons[name]=b
        rlayout.addLayout(nav)

        self.notice=QLabel('Hardware only  •  No samples received  •  Accuracy requires hardware calibration'); self.notice.setObjectName('notice'); outer.addWidget(self.notice)
        self.show_menu('ACQUIRE')

    def theme(self):
        self.setStyleSheet(f"""
            QWidget {{background:{BG};color:{TEXT};font-family:'Segoe UI';font-size:12px;}}
            QLabel#title {{font-size:22px;font-weight:800;letter-spacing:2px;}}
            QLabel#muted,QLabel#small {{color:{MUTED};}} QLabel#small {{font-size:9px;font-weight:700;}}
            QLabel#state {{color:#c5d4e2;background:#202d39;padding:7px 11px;border:1px solid #314355;border-radius:3px;}}
            QFrame#instrument {{background:#071019;border:2px solid #31475b;border-radius:4px;}}
            QFrame#tekBar {{background:#0755a9;border:1px solid #2983d6;}}
            QLabel#tekBrand {{background:transparent;color:white;font-size:18px;font-weight:900;font-style:italic;padding-right:12px;}}
            QLabel#tekStatus {{background:transparent;color:#e8f5ff;font-family:Consolas;font-size:11px;font-weight:700;}}
            QFrame#softPanel {{background:#095bb1;border:1px solid #2a84d4;}}
            QLabel#menuTitle {{background:#0b70cf;color:white;font-size:17px;font-weight:800;padding:9px;border:1px solid #55a5eb;}}
            QWidget#menuPage {{background:#095bb1;}}
            QLabel#softNote {{background:#e5f1fb;color:#123a5f;border:1px solid #aacbe6;border-radius:2px;padding:8px;}}
            QPushButton#softKey {{background:#dbeefc;color:#123d64;border:1px solid #8ab9df;font-weight:700;padding:8px 3px;}}
            QPushButton#softKey:hover {{background:#ffffff;}}
            QGroupBox {{background:{PANEL};border:1px solid #2d4050;border-radius:3px;margin-top:7px;padding:8px 6px 5px;font-size:10px;font-weight:600;}}
            QGroupBox QLabel,QGroupBox QCheckBox {{background:transparent;}}
            QPushButton {{background:#263746;border:1px solid #456078;border-radius:3px;padding:7px 9px;font-weight:600;}}
            QPushButton:hover {{background:#365069;}} QPushButton:disabled {{color:#647788;border-color:#263747;background:#18232d;}}
            QPushButton#primary {{background:#176fa9;border:1px solid #46a7df;color:white;}}
            QPushButton#run {{background:#26784f;border-color:#58ae7e;color:white;}}
            QComboBox,QDoubleSpinBox,QSpinBox,QLineEdit {{background:#071019;border:1px solid #49637a;border-radius:2px;padding:4px;min-height:20px;}}
            QComboBox:disabled,QDoubleSpinBox:disabled {{color:#627183;}}
            QComboBox QAbstractItemView {{selection-background-color:#206ba3;}}
            QSlider::groove:horizontal {{height:5px;background:#273b4d;border:1px solid #40586c;}}
            QSlider::handle:horizontal {{background:#d6e7f2;border:1px solid #6c8ea6;width:13px;margin:-5px 0;border-radius:6px;}}
            QFrame#metric {{background:#0a141e;border:1px solid #304556;border-radius:2px;}}
            QFrame#metric QLabel {{background:transparent;}} QLabel#reading {{font-family:Consolas;font-size:16px;font-weight:700;color:#e9f5ff;}}
            QLabel#cursor {{background:#052b50;color:white;font-family:Consolas;font-size:11px;padding:8px;min-height:90px;border:1px solid #61a6df;}}
            QLabel#notice {{color:#9baabd;font-size:11px;padding:3px 0;}}
        """)

    def show_menu(self,name):
        if name not in self.menu_pages: return
        self.menu_title.setText(name); self.menu_stack.setCurrentWidget(self.menu_pages[name])
        for key,b in self.menu_buttons.items(): b.setProperty('selected',key==name)

    def probe_factor(self,i):
        if i>=len(self.probes): return 1.0
        text=self.probes[i].currentText().upper().rstrip('X')
        try: return float(text)
        except ValueError: return 1.0

    def displayed_channel(self,i):
        if self.current is None or i>=self.current.y.shape[1]: return None
        y=self.current.y[:,i].astype(float,copy=True)*self.probe_factor(i)
        coupling=self.couplings[i].currentText() if i<len(self.couplings) else 'DC'
        if coupling=='AC' and len(y): y-=float(np.mean(y))
        elif coupling=='GND': y[:]=0.0
        return y

    def scale_combo_changed(self,i,idx):
        if i<len(self.vdiv_sliders):
            self.vdiv_sliders[i].blockSignals(True); self.vdiv_sliders[i].setValue(idx); self.vdiv_sliders[i].blockSignals(False)
            self.position_spin_changed(i); self.update_trigger_slider()
        self.draw_capture()

    def position_slider_changed(self,i,value):
        if i>=len(self.positions): return
        target=(value/100.0)*self.vdivs[i].currentData()
        self.positions[i].blockSignals(True); self.positions[i].setValue(target); self.positions[i].blockSignals(False)
        self.draw_capture(); self.update_trigger_slider()

    def position_spin_changed(self,i):
        if i>=len(self.position_sliders): return
        scale=self.vdivs[i].currentData() or 1.0
        slider=int(round(100*self.positions[i].value()/scale)); slider=max(-400,min(400,slider))
        self.position_sliders[i].blockSignals(True); self.position_sliders[i].setValue(slider); self.position_sliders[i].blockSignals(False)
        self.draw_capture(); self.update_trigger_slider()

    def channel_display_changed(self,i):
        self.draw_capture(); self.update_measurements(); self.update_trigger_slider()

    def time_combo_changed(self,idx):
        if hasattr(self,'time_slider'):
            self.time_slider.blockSignals(True); self.time_slider.setValue(idx); self.time_slider.blockSignals(False)
        self.time_changed(); self.sync_pan_slider(); self.update_status_readouts()

    def pan_slider_changed(self,value):
        target=(value/100.0)*self.tdiv.currentData()
        self.pan.blockSignals(True); self.pan.setValue(target); self.pan.blockSignals(False); self.update_axes()

    def pan_spin_changed(self,_value=None):
        self.sync_pan_slider(); self.update_axes()

    def sync_pan_slider(self):
        if not hasattr(self,'pan_slider'): return
        scale=self.tdiv.currentData() or 1.0
        value=int(round(100*self.pan.value()/scale)); value=max(-400,min(400,value))
        self.pan_slider.blockSignals(True); self.pan_slider.setValue(value); self.pan_slider.blockSignals(False)

    def trigger_slider_changed(self,value):
        if not hasattr(self,'source') or not self.vdivs: return
        ch=max(0,self.source.currentIndex()); ch=min(ch,len(self.vdivs)-1)
        level=self.positions[ch].value()+(value/100.0)*self.vdivs[ch].currentData()
        self.level.blockSignals(True); self.level.setValue(level); self.level.blockSignals(False); self.trigger_changed()

    def update_trigger_slider(self):
        if not hasattr(self,'trigger_slider') or not hasattr(self,'source'): return
        ch=max(0,self.source.currentIndex()); ch=min(ch,len(self.vdivs)-1); scale=self.vdivs[ch].currentData() or 1.0
        value=int(round(100*(self.level.value()-self.positions[ch].value())/scale)); value=max(-400,min(400,value))
        self.trigger_slider.blockSignals(True); self.trigger_slider.setValue(value); self.trigger_slider.blockSignals(False)

    def trigger_engine_level(self):
        ch=max(0,self.source.currentIndex()); ch=min(ch,len(self.vdivs)-1); factor=self.probe_factor(ch)
        coupling=self.couplings[ch].currentText() if ch<len(self.couplings) else 'DC'
        if coupling=='GND': return float('inf')
        raw=self.level.value()/factor
        if coupling=='AC' and len(self.engine.y) and ch<self.engine.y.shape[1]: raw+=float(np.mean(self.engine.y[:,ch]))
        return raw

    def update_status_readouts(self):
        if not hasattr(self,'top_status'): return
        if self.single_pending: trig='Armed'
        elif self.current is None: trig='Ready' if self.metadata is not None else 'Waiting'
        elif self.current.triggered: trig="Trig'd"
        else: trig='Auto'
        self.top_status.setText(f'T {trig}     M Pos: {fmt(self.pan.value(),"s")}     {self.mode.currentText() if hasattr(self,"mode") else "Auto"}')
        ch=max(0,self.source.currentIndex()) if hasattr(self,'source') else 0; ch=min(ch,len(self.vdivs)-1)
        unit=self.channels[ch].unit; freq=None
        if self.current is not None and ch<self.current.y.shape[1]:
            y=self.displayed_channel(ch); freq=measurements(self.current.t,y)['frequency'] if y is not None else None
        self.bottom_status.setText(f'CH{ch+1} {fmt(self.vdivs[ch].currentData(),unit)}/div     M {fmt(self.tdiv.currentData(),"s")}/div     CH{ch+1} / {fmt(self.level.value(),unit)}     {fmt(freq,"Hz")}')

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
            for w in [self.channel_checks[i],self.vdivs[i],self.vdiv_sliders[i],self.positions[i],self.position_sliders[i],self.couplings[i],self.probes[i]]: w.setEnabled(present)
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
        self.refresh.setEnabled(self.transport.currentText()=='Serial'); self.controls_enabled(False); self.update_status_readouts()

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
        self.channel_labels(); self.controls_enabled(True); self.update_status_readouts()

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
                    self.tdiv.blockSignals(True); self.tdiv.setCurrentIndex(TIME_SCALES.index(choice)); self.tdiv.blockSignals(False)
                    self.time_slider.blockSignals(True); self.time_slider.setValue(self.tdiv.currentIndex()); self.time_slider.blockSignals(False); self.update_axes()
                self.initial_scale=False
            auto=now-self.last_capture>=.5
            capture=self.engine.capture(10*self.tdiv.currentData(),max(0,self.source.currentIndex()),self.trigger_engine_level(),self.slope.currentText(),'Normal' if self.single_pending else self.mode.currentText(),auto,prefer_newest=not self.single_pending)
            if capture is not None:
                self.current=capture; self.last_capture=now
                if self.autorange_box.isChecked(): self.autorange_capture()
                self.draw_capture(); self.update_measurements(); self.controls_enabled(True)
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
        self.notice.setText('  |  '.join(parts)); self.update_status_readouts()

    def toggle_run(self):
        if not self.link_open or self.metadata is None: return
        if self.running:
            self.running=False; self.single_pending=False; self.run_button.setText('RUN'); self.state.setText('●  STOPPED'); self.capture_label.setText('STOPPED · CAPTURE HELD'); self.update_status_readouts()
        else: self.arm(False)

    def single(self):
        if self.link_open and self.metadata is not None: self.arm(True)

    def arm(self,single):
        self.engine.clear(); self.running=True; self.single_pending=single; self.last_capture=time.monotonic()
        self.run_button.setText('STOP'); self.state.setText('●  SINGLE ARMED' if single else '●  RUNNING'); self.capture_label.setText('WAITING FOR NEW HARDWARE SAMPLES'); self.update_status_readouts()

    def clear_capture(self):
        self.current=None
        for curve in self.curves: curve.setData([],[])
        self.empty.show(); self.trigger_time.hide(); self.update_measurements(); self.update_cursor(); self.update_status_readouts()

    def time_changed(self):
        self.initial_scale=False; self.engine.clear(); self.update_axes()

    def update_axes(self):
        span=10*self.tdiv.currentData(); centre=self.pan.value()
        low=centre-.2*span; high=centre+.8*span
        self.plot.setXRange(low,high,padding=0); self.plot.setYRange(-4,4,padding=0)
        ticks=[(low+i*span/10,'') for i in range(11)]
        self.plot.getAxis('bottom').setTicks([ticks]); self.empty.setPos((low+high)/2,0); self.update_status_readouts()

    def draw_capture(self,*_):
        if not hasattr(self,'trigger_line') or not hasattr(self,'source'): return
        c=self.current
        if c is not None:
            self.empty.hide()
            for i,curve in enumerate(self.curves):
                if i<c.y.shape[1] and self.channel_checks[i].isChecked():
                    y=self.displayed_channel(i)
                    curve.setData(c.t,(y-self.positions[i].value())/self.vdivs[i].currentData(),symbol='o' if len(c.t)<80 else None,symbolSize=3,symbolBrush=COLORS[i],symbolPen=None)
                else: curve.setData([],[])
            self.trigger_time.setVisible(c.triggered)
        ch=max(0,self.source.currentIndex())
        self.syncing=True
        self.trigger_line.setValue((self.level.value()-self.positions[ch].value())/self.vdivs[ch].currentData())
        self.syncing=False; self.trigger_line.setVisible(self.metadata is not None)
        self.update_cursor(); self.update_status_readouts()

    def trigger_dragged(self):
        if self.syncing: return
        ch=max(0,self.source.currentIndex())
        self.level.setValue(self.trigger_line.value()*self.vdivs[ch].currentData()+self.positions[ch].value()); self.update_trigger_slider()

    def trigger_changed(self,*_):
        self.engine.last_trigger=-math.inf
        self.update_trigger_slider()
        if hasattr(self,'vdivs'): self.draw_capture()
        self.update_status_readouts()

    def measure_changed(self,*_): self.update_measurements(); self.update_cursor()

    def update_measurements(self):
        if not hasattr(self,'metrics'): return
        if self.current is None:
            for v in self.metrics.values(): v.setText('—')
            self.sample_label.setText('Sample timing —'); self.measure_summary.setText('No capture available'); return
        ch=max(0,self.measure_source.currentIndex())
        if ch>=self.current.y.shape[1]: return
        y=self.displayed_channel(ch); m=measurements(self.current.t,y); unit=self.channels[ch].unit
        for key,w in self.metrics.items(): w.setText(fmt(m[key],'Hz' if key=='frequency' else 's' if key=='period' else '%' if key=='duty' else unit))
        dt=np.diff(self.current.t)
        if len(dt):
            median=float(np.median(dt)); jitter=np.max(np.abs(dt-median))/median if median>0 else math.inf
            label='Uniform' if jitter<.01 else 'Irregular'; timing=f'{label} · {fmt(1/median,"Sa/s")} median · {len(self.current.t):,} pts' if median>0 else f'{len(self.current.t):,} pts'
        else: timing='1 point · sample rate unavailable'
        self.sample_label.setText(timing); self.measure_summary.setText(f'Frequency  {fmt(m["frequency"],"Hz")}\nPeriod      {fmt(m["period"],"s")}\nPeak-Peak   {fmt(m["vpp"],unit)}\nRMS         {fmt(m["rms"],unit)}\nMean        {fmt(m["mean"],unit)}\nMin / Max   {fmt(m["minimum"],unit)} / {fmt(m["maximum"],unit)}\nDuty        {fmt(m["duty"],"%")}'); self.update_status_readouts()

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

    def autorange_capture(self):
        if self.current is None: return
        for i in range(self.current.y.shape[1]):
            if not self.channel_checks[i].isChecked(): continue
            y=self.displayed_channel(i)
            if y is None or not len(y): continue
            extent=max(float(np.ptp(y))/6,1e-6)
            choice=next((v for v in SCALES if v>=extent),SCALES[-1])
            if self.vdivs[i].currentData()!=choice: self.vdivs[i].setCurrentIndex(SCALES.index(choice))
            centre=float((np.max(y)+np.min(y))/2)
            if abs(centre-self.positions[i].value()) > .15*self.vdivs[i].currentData(): self.positions[i].setValue(centre)
        ch=max(0,self.source.currentIndex()); ch=min(ch,self.current.y.shape[1]-1)
        y=self.displayed_channel(ch); m=measurements(self.current.t,y)
        if m['period'] is not None:
            target=max(m['period']/2,1e-6); choice=next((v for v in TIME_SCALES if v>=target),TIME_SCALES[-1])
            if self.tdiv.currentData()!=choice: self.tdiv.setCurrentIndex(TIME_SCALES.index(choice))
        midpoint=float((np.max(y)+np.min(y))/2)
        if abs(midpoint-self.level.value()) > .1*self.vdivs[ch].currentData(): self.level.setValue(midpoint)

    def autoset(self):
        if self.current is None: return
        for i in range(self.current.y.shape[1]):
            y=self.displayed_channel(i)
            if y is None or not len(y): continue
            extent=max(float(np.ptp(y))/6,1e-6)
            choice=next((v for v in SCALES if v>=extent),SCALES[-1]); self.vdivs[i].setCurrentIndex(SCALES.index(choice))
            self.positions[i].setValue(float((np.max(y)+np.min(y))/2))
        ch=max(0,self.source.currentIndex()); ch=min(ch,self.current.y.shape[1]-1)
        y=self.displayed_channel(ch); m=measurements(self.current.t,y)
        if m['period'] is not None: target=max(m['period']/2,1e-6)
        elif len(self.current.t)>1: target=max(float(self.current.t[-1]-self.current.t[0])/8,1e-6)
        else: target=self.tdiv.currentData()
        choice=next((v for v in TIME_SCALES if v>=target),TIME_SCALES[-1]); self.tdiv.setCurrentIndex(TIME_SCALES.index(choice)); self.pan.setValue(0)
        self.level.setValue(float((np.max(y)+np.min(y))/2)); self.draw_capture(); self.reset_cursors(); self.show_menu('ACQUIRE')

    def autoscale(self):
        self.autoset()

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
