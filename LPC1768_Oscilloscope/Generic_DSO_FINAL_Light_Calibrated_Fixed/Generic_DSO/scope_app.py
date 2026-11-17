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
from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QPointF
from PySide6.QtGui import QColor, QFont, QPainter, QPicture, QFontMetrics
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,
    QGridLayout,QLabel,QPushButton,QComboBox,QDoubleSpinBox,QSpinBox,QLineEdit,
    QGroupBox,QCheckBox,QFrame,QFileDialog,QMessageBox,QDialog,QDialogButtonBox,
    QFormLayout,QScrollArea,QSlider,QStackedWidget,QSizePolicy,QButtonGroup)
import pyqtgraph as pg
from scope_core import Metadata, Frame, CaptureEngine, measurements
from transport import Reader

from theme import (BG, PANEL, TEXT, MUTED, SCREEN, GRID, AXES, COLORS, TRIGGER,
                   CURSOR, UI_FONTS, OK, ERROR, WARNING, stylesheet, channel_style, repolish)
from display_processing import AcquisitionProcessor, display_interpolate
from calibration import fit_two_point
from scale_guard import fit_voltage_scale, display_range_health, raw_span_from_calibrated, suspicious_gain

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
            return f'{value/factor:.4g} {prefix}{unit}'.strip()
    return f'{value:.4g} {unit}'.strip()


def combo(items):
    w=QComboBox(); w.addItems(items); return w


def spin(value=0, low=-1e9, high=1e9, decimals=6):
    w=QDoubleSpinBox(); w.setDecimals(decimals); w.setRange(low,high); w.setValue(value)
    w.setKeyboardTracking(False); return w


class FittedReading(QLabel):
    """Fit a full precision reading before eliding unusually long custom units."""
    def fitted_font(self):
        font=QFont(self.font()); font.setPixelSize(13)
        while font.pixelSize()>9 and QFontMetrics(font).horizontalAdvance(self.text())>self.contentsRect().width():
            font.setPixelSize(font.pixelSize()-1)
        return font
    def paintEvent(self,event):
        painter=QPainter(self); font=self.fitted_font(); painter.setFont(font)
        painter.setPen(self.palette().color(self.foregroundRole()))
        text=QFontMetrics(font).elidedText(self.text(),Qt.ElideRight,self.contentsRect().width())
        painter.drawText(self.contentsRect(),Qt.AlignLeft|Qt.AlignVCenter,text)


class SegmentedControl(QWidget):
    currentTextChanged=Signal(str)
    def __init__(self,items):
        super().__init__(); self.buttons={}; self.group=QButtonGroup(self); self.group.setExclusive(True)
        row=QHBoxLayout(self); row.setContentsMargins(0,0,0,0); row.setSpacing(3)
        for text in items:
            button=QPushButton(text); button.setCheckable(True); button.setObjectName('segment')
            self.group.addButton(button); self.buttons[text]=button; row.addWidget(button)
            button.clicked.connect(lambda _,text=text:self.currentTextChanged.emit(text))
        self.buttons[items[0]].setChecked(True)
    def currentText(self):
        return next(text for text,button in self.buttons.items() if button.isChecked())
    def setCurrentText(self,text):
        if text in self.buttons and text!=self.currentText():
            self.buttons[text].setChecked(True); self.currentTextChanged.emit(text)


class Graticule(pg.GraphicsObject):
    """10 × 8 divisions; dotted majors and five subdivisions on center axes."""
    def __init__(self):
        super().__init__(); self.picture=QPicture(); self.rect=QRectF(0,-4,1,8)
    def set_window(self,low,high):
        self.prepareGeometryChange(); self.rect=QRectF(low,-4,high-low,8)
        self.picture=QPicture(); painter=QPainter(self.picture)
        major=QColor(GRID); major.setAlphaF(.80)
        painter.setPen(pg.mkPen(major,width=1,style=Qt.DotLine,cosmetic=True))
        span=high-low; center=(low+high)/2
        for n in range(11):
            x=low+n*span/10; painter.drawLine(QPointF(x,-4),QPointF(x,4))
        for n in range(-4,5): painter.drawLine(QPointF(low,n),QPointF(high,n))
        painter.setPen(pg.mkPen(AXES,width=1,cosmetic=True))
        painter.drawLine(QPointF(low,0),QPointF(high,0)); painter.drawLine(QPointF(center,-4),QPointF(center,4))
        for n in range(51):
            x=low+n*span/50; painter.drawLine(QPointF(x,-.055),QPointF(x,.055))
        for n in range(41):
            y=-4+n/5; painter.drawLine(QPointF(center-span*.005,y),QPointF(center+span*.005,y))
        painter.end(); self.update()
    def paint(self,painter,option,widget=None): painter.drawPicture(0,0,self.picture)
    def boundingRect(self): return self.rect


class Channel:
    def __init__(self,i):
        self.name=f'CH{i+1}'; self.unit='V'; self.gain=1.; self.offset=0.; self.sensor='Device units'


class ProfileDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent); self.setWindowTitle('Device & sensor configuration'); self.resize(790,500)
        layout=QVBoxLayout(self)
        note=QLabel('Value = raw × gain + offset')
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
        layout.addStretch()
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)


class ScopeWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle('Generic DSO'); self.resize(1500,970)
        self.setMinimumSize(1150,740)
        self.channels=[Channel(i) for i in range(4)]; self.board_profile='Generic / Custom'
        self.reader=None; self.metadata=None; self.engine=CaptureEngine(); self.current=None
        self.running=False; self.single_pending=False; self.link_open=False
        self.last_rx=0.; self.last_capture=0.; self.gaps=0; self.frames=0; self.invalid=0; self.syncing=False
        self.initial_scale=True; self.initial_fit_pending=True; self.reference_active=False
        self.calibration_note=''
        self.last_display_clip=False
        self.processor=AcquisitionProcessor(); self.processed_y=None; self._processed_capture=None
        self._probe_factors=[1.,1.,1.,1.]; self._capture_ac_reference=None
        self._cursor_adjusting=False; self._last_draw=0.
        self._redraw_timer=QTimer(self); self._redraw_timer.setSingleShot(True); self._redraw_timer.setTimerType(Qt.PreciseTimer)
        self._redraw_timer.timeout.connect(self.draw_capture)
        self.config_path=Path.home()/'.generic_dso_profiles.json'
        self.load_profiles()
        self.build_ui(); self.theme(); self.refresh_ports(); self.controls_enabled(False)
        self.timer=QTimer(self); self.timer.setTimerType(Qt.PreciseTimer); self.timer.timeout.connect(self.poll); self.timer.start(34)
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
        # Scroll the complete panel on laptop-height screens instead of cropping
        # CH1–CH4 probe controls below the bottom edge.
        self.root_scroll=QScrollArea(); self.root_scroll.setWidgetResizable(True)
        self.root_scroll.setFrameShape(QFrame.NoFrame)
        self.root_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.root_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        root=QWidget(); root.setObjectName('rootFrame'); root.setMinimumSize(1120,880)
        self.root_scroll.setWidget(root); self.setCentralWidget(self.root_scroll)
        outer=QVBoxLayout(root); outer.setContentsMargins(16,12,16,10); outer.setSpacing(10)
        header=QHBoxLayout(); header.setSpacing(8)
        brand=QVBoxLayout(); brand.setSpacing(0)
        title=QLabel('Generic DSO'); title.setObjectName('title'); brand.addWidget(title)
        caption=QLabel('PRECISION SIGNAL WORKBENCH'); caption.setObjectName('subtitle'); brand.addWidget(caption)
        header.addLayout(brand)
        header.addSpacing(9)
        self.transport=combo(['Serial','TCP']); self.transport.currentTextChanged.connect(self.transport_changed)
        self.port=QComboBox(); self.port.setEditable(True); self.port.setMinimumWidth(150)
        self.port.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        self.rate=combo(['115200','230400','460800','921600','1000000','2000000']); self.rate.setEditable(True)
        self.rate_label=QLabel('Baud'); self.rate_label.setObjectName('small')
        self.refresh=QPushButton('↻'); self.refresh.setToolTip('Refresh serial ports'); self.refresh.clicked.connect(self.refresh_ports)
        self.connect_button=QPushButton('Connect'); self.connect_button.setObjectName('primary'); self.connect_button.clicked.connect(self.toggle_connection)
        for w in [self.transport,self.port,self.rate_label,self.rate,self.refresh,self.connect_button]: header.addWidget(w)
        header.addStretch(1)
        self.preset_button=QPushButton('TEK PRESET'); self.preset_button.setObjectName('preset')
        self.preset_button.setToolTip('Use the supplied Tektronix TBS1062 reference: CH1 1 V/div and 500 ms/div.')
        self.preset_button.clicked.connect(self.apply_tek_preset); header.addWidget(self.preset_button)
        self.calibration_button=QPushButton('CALIBRATE'); self.calibration_button.setObjectName('preset')
        self.calibration_button.setToolTip('Two-point voltage calibration using a KNOWN signal. Does not repair wiring or ADC saturation.')
        self.calibration_button.clicked.connect(self.calibrate_channel); header.addWidget(self.calibration_button)
        self.reset_gain_button=QPushButton('RESET CH1 GAIN'); self.reset_gain_button.setObjectName('resetGain')
        self.reset_gain_button.setToolTip('Restore CH1 to direct firmware-reported volts (gain 1, offset 0). Also clears the old unsafe ×686 calibration.')
        self.reset_gain_button.clicked.connect(self.reset_ch1_gain)
        self.state=QLabel('●  Disconnected'); self.state.setObjectName('state'); header.addWidget(self.state)
        self.profile_button=QPushButton('⚙'); self.profile_button.setToolTip('Device & sensors'); self.profile_button.clicked.connect(self.configure); header.addWidget(self.profile_button)
        self.device_label=QLabel('No acquisition device'); self.device_label.hide()
        outer.addLayout(header)
        reference=QFrame(); reference.setObjectName('referenceBar')
        reference_row=QHBoxLayout(reference); reference_row.setContentsMargins(11,6,11,6); reference_row.setSpacing(9)
        self.reference_label=QLabel('TEK TBS1062 REFERENCE  ·  CH1 1 V/div  ·  500 ms/div  ·  1 Hz square-wave test')
        self.reference_label.setObjectName('referenceLabel'); reference_row.addWidget(self.reference_label,1)
        self.reference_status=QLabel('Select TEK PRESET to compare live CH1 readings')
        self.reference_status.setObjectName('referenceLabel'); self.reference_status.setAlignment(Qt.AlignRight)
        self.reference_status.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        reference_row.addWidget(self.reference_status,1)
        outer.addWidget(reference)
        self.scale_audit=QFrame(); self.scale_audit.setObjectName('auditBar')
        audit_row=QHBoxLayout(self.scale_audit); audit_row.setContentsMargins(12,6,12,6)
        self.scale_audit_label=QLabel('SCALE CHECK  ·  Waiting for hardware capture')
        self.scale_audit_label.setObjectName('auditText'); self.scale_audit_label.setWordWrap(True)
        audit_row.addWidget(self.scale_audit_label,1)
        audit_row.addWidget(self.reset_gain_button)
        outer.addWidget(self.scale_audit)

        instrument=QFrame(); instrument.setObjectName('instrument'); outer.addWidget(instrument,1)
        body=QHBoxLayout(instrument); body.setContentsMargins(10,10,10,10); body.setSpacing(10)
        screen=QVBoxLayout(); screen.setSpacing(8); body.addLayout(screen,1)
        top=QFrame(); top.setObjectName('readoutBar'); top_l=QHBoxLayout(top); top_l.setContentsMargins(10,7,10,7)
        self.top_status=QLabel('WAITING'); self.top_status.setObjectName('scopeReadout'); top_l.addWidget(self.top_status,1)
        self.capture_label=QLabel('WAITING'); self.capture_label.setObjectName('scopeReadout'); top_l.addWidget(self.capture_label)
        screen.addWidget(top)

        pg.setConfigOptions(antialias=True,background=SCREEN,foreground=MUTED)
        self.plot=pg.PlotWidget(); self.plot.setMinimumHeight(300); self.plot.setMenuEnabled(False)
        self.plot.hideButtons(); self.plot.setMouseEnabled(x=False,y=False); self.plot.setBackground(SCREEN)
        self.plot.hideAxis('left'); self.plot.hideAxis('bottom')
        self.plot.getViewBox().setDefaultPadding(0); self.plot.setYRange(-4,4,padding=0)
        self.graticule=Graticule(); self.plot.addItem(self.graticule); self.graticule.setZValue(-20)
        def trace_pen(color,width,alpha=1.):
            c=QColor(color); c.setAlphaF(alpha); pen=pg.mkPen(c,width=width,cosmetic=True)
            pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin); return pen
        self.glows=[self.plot.plot([],[],pen=trace_pen(c,6,.10)) for c in COLORS]
        self.curves=[self.plot.plot([],[],pen=trace_pen(c,2.5)) for c in COLORS]
        self.sample_dots=[self.plot.plot([],[],pen=None,symbol='o',symbolSize=4,symbolBrush=c,symbolPen=None) for c in COLORS]
        for curve in self.glows+self.curves:
            curve.setClipToView(True); curve.setDownsampling(auto=True,method='peak'); curve.setSkipFiniteCheck(True)
        self.trigger_line=pg.InfiniteLine(angle=0,movable=True,pen=pg.mkPen(TRIGGER,width=1,style=Qt.DashLine))
        self.trigger_line.sigPositionChangeFinished.connect(self.trigger_dragged); self.plot.addItem(self.trigger_line); self.trigger_line.hide()
        self.trigger_time=pg.InfiniteLine(angle=90,movable=False,pen=pg.mkPen(TRIGGER,width=1,style=Qt.DotLine)); self.plot.addItem(self.trigger_time); self.trigger_time.hide()
        self.ground_markers=[pg.TextItem(f'{i+1}▸',color=c,anchor=(0,.5)) for i,c in enumerate(COLORS)]
        self.level_marker=pg.TextItem('◂',color=TRIGGER,anchor=(1,.5))
        self.time_marker=pg.TextItem('T',color=TRIGGER,anchor=(.5,0))
        for marker in self.ground_markers+[self.level_marker,self.time_marker]:
            marker.setZValue(50); self.plot.addItem(marker); marker.hide()
        self.cursors=[]
        cc=QColor(CURSOR); cc.setAlphaF(.75)
        for pos,angle in [(0,90),(.003,90),(-1,0),(1,0)]:
            line=pg.InfiniteLine(pos=pos,angle=angle,movable=True,pen=pg.mkPen(cc,width=1,style=Qt.DashLine),hoverPen=pg.mkPen(CURSOR,width=2))
            line.sigPositionChanged.connect(self.update_cursor); self.plot.addItem(line); line.hide(); self.cursors.append(line)
        self.empty=pg.TextItem('No signal',color=MUTED,anchor=(.5,.5)); self.empty.setFont(QFont(UI_FONTS[0],16))
        self.plot.addItem(self.empty); screen.addWidget(self.plot,1)
        bottom=QFrame(); bottom.setObjectName('readoutBar'); bottom_l=QHBoxLayout(bottom); bottom_l.setContentsMargins(10,7,10,7)
        self.bottom_status=QLabel('—'); self.bottom_status.setObjectName('scopeReadout'); self.bottom_status.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred); bottom_l.addWidget(self.bottom_status,1)
        self.sample_label=QLabel('—'); self.sample_label.setObjectName('scopeReadout'); bottom_l.addWidget(self.sample_label)
        screen.addWidget(bottom)

        metric_row=QHBoxLayout(); metric_row.setSpacing(5); self.metrics={}; self.metric_cards={}
        metric_defs=[('frequency','FREQUENCY'),('period','PERIOD'),('vpp','PEAK–PEAK'),('rms','RMS'),('mean','MEAN'),('minimum','MIN'),('maximum','MAX'),('duty','DUTY')]
        for key,title_text in metric_defs:
            card=QFrame(); card.setObjectName('metric'); card.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
            v=QVBoxLayout(card); v.setContentsMargins(7,6,7,6); v.setSpacing(4)
            label=QLabel(title_text); label.setObjectName('small'); value=FittedReading('—'); value.setObjectName('reading')
            v.addWidget(label); v.addWidget(value); self.metrics[key]=value; self.metric_cards[key]=card; metric_row.addWidget(card,1)
        screen.addLayout(metric_row)

        channel_row=QHBoxLayout(); channel_row.setSpacing(7)
        self.channel_checks=[]; self.vdivs=[]; self.vdiv_sliders=[]; self.positions=[]; self.position_sliders=[]; self.couplings=[]; self.probes=[]; self.channel_boxes=[]
        for i in range(4):
            box=QGroupBox(); box.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred); self.channel_boxes.append(box)
            form=QVBoxLayout(box); form.setContentsMargins(7,7,7,7); form.setSpacing(4)
            check=QPushButton(f'CH{i+1} (V)'); check.setObjectName('channelToggle'); check.setCheckable(True); check.setChecked(i==0); check.setStyleSheet(channel_style(COLORS[i]))
            check.toggled.connect(self.channel_toggled); form.addWidget(check); self.channel_checks.append(check)
            scale=QComboBox(); scale.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)
            for val in SCALES: scale.addItem(fmt(val,'V')+'/div',val)
            scale.setCurrentIndex(SCALES.index(1)); self.vdivs.append(scale)
            scale_slider=QSlider(Qt.Horizontal); scale_slider.setRange(0,len(SCALES)-1); scale_slider.setValue(scale.currentIndex()); self.vdiv_sliders.append(scale_slider)
            scale.currentIndexChanged.connect(lambda idx,i=i: self.scale_combo_changed(i,idx)); scale_slider.valueChanged.connect(lambda value,i=i: self.vdivs[i].setCurrentIndex(value))
            row=QHBoxLayout(); row.addWidget(QLabel('SCALE')); row.addWidget(scale,1); form.addLayout(row); form.addWidget(scale_slider)
            position=spin(0,decimals=5); position.setSuffix(' V'); position.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed); self.positions.append(position)
            pslider=QSlider(Qt.Horizontal); pslider.setRange(-400,400); self.position_sliders.append(pslider); pslider.setToolTip('Vertical position in divisions')
            position.valueChanged.connect(lambda _,i=i: self.position_spin_changed(i)); pslider.valueChanged.connect(lambda value,i=i:self.position_slider_changed(i,value))
            row=QHBoxLayout(); row.addWidget(QLabel('POS')); row.addWidget(position,1); form.addLayout(row); form.addWidget(pslider)
            coupling=SegmentedControl(['DC','AC','GND']); self.couplings.append(coupling)
            coupling.currentTextChanged.connect(lambda _,i=i:self.channel_display_changed(i)); form.addWidget(coupling)
            probe=combo(['1X','10X','100X']); self.probes.append(probe)
            probe.setToolTip('Dimensionless probe attenuation. Scale, position and trigger level follow the selected factor.')
            probe.currentTextChanged.connect(lambda _,i=i:self.probe_changed(i))
            row=QHBoxLayout(); row.addWidget(QLabel('PROBE')); row.addWidget(probe,1); form.addLayout(row); channel_row.addWidget(box,1)
        screen.addLayout(channel_row)

        right=QFrame(); right.setObjectName('softPanel'); right.setMinimumWidth(268); right.setMaximumWidth(292); body.addWidget(right)
        rlayout=QVBoxLayout(right); rlayout.setContentsMargins(10,10,10,10); rlayout.setSpacing(8)
        self.run_button=QPushButton('RUN'); self.run_button.setObjectName('run'); self.run_button.clicked.connect(self.toggle_run); rlayout.addWidget(self.run_button)
        row=QHBoxLayout()
        self.single_button=QPushButton('SINGLE'); self.single_button.clicked.connect(self.single)
        self.auto_button=QPushButton('AUTOSET'); self.auto_button.setObjectName('primary'); self.auto_button.clicked.connect(self.autoset)
        row.addWidget(self.single_button); row.addWidget(self.auto_button); rlayout.addLayout(row)
        nav=QGridLayout(); nav.setSpacing(4); self.menu_buttons={}
        for n,name in enumerate(['ACQUIRE','HORIZONTAL','TRIGGER','CURSOR','MEASURE','SAVE']):
            button=QPushButton(name.title()); button.setObjectName('softKey'); button.clicked.connect(lambda _,name=name:self.show_menu(name)); nav.addWidget(button,n//3,n%3); self.menu_buttons[name]=button
        rlayout.addLayout(nav)
        self.menu_title=QLabel('ACQUIRE'); self.menu_title.setObjectName('menuTitle'); rlayout.addWidget(self.menu_title)
        self.menu_stack=QStackedWidget(); self.menu_pages={}
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame); scroll.setWidget(self.menu_stack); rlayout.addWidget(scroll,1)
        def page(name):
            w=QWidget(); w.setObjectName('menuPage'); lay=QVBoxLayout(w); lay.setContentsMargins(0,0,0,0); lay.setSpacing(8)
            self.menu_stack.addWidget(w); self.menu_pages[name]=w; return lay
        lay=page('ACQUIRE')
        self.acquisition_mode=combo(['Sample','Average','Hi-Res']); self.average_n=combo(['2','4','8','16','32','64']); self.average_n.setCurrentText('16')
        self.hires_width=combo(['2','4','8','16']); self.hires_width.setCurrentText('4')
        self.interpolation=combo(['Linear','Sin(x)/x'])
        for label,w in [('Acquisition mode',self.acquisition_mode),('Average records',self.average_n),('Hi-Res samples',self.hires_width),('Display interpolation',self.interpolation)]: lay.addWidget(QLabel(label)); lay.addWidget(w)
        self.acquisition_mode.currentTextChanged.connect(self.acquisition_changed); self.average_n.currentTextChanged.connect(self.acquisition_changed); self.hires_width.currentTextChanged.connect(self.acquisition_changed)
        self.interpolation.currentTextChanged.connect(self.draw_capture)
        self.autorange_box=QCheckBox('Continuous autoscale'); lay.addWidget(self.autorange_box)
        self.initial_autofit=QCheckBox('Fit first hardware capture to screen')
        self.initial_autofit.setChecked(True)
        self.initial_autofit.setToolTip('Automatically centers/scales the first real captured waveform. Does not change measurements.')
        lay.addWidget(self.initial_autofit)
        self.processing_note=QLabel('Display interpolation does not change samples.'); self.processing_note.setObjectName('softNote'); self.processing_note.setWordWrap(True); lay.addWidget(self.processing_note); lay.addStretch()
        lay=page('HORIZONTAL')
        self.tdiv=QComboBox()
        for val in TIME_SCALES: self.tdiv.addItem(fmt(val,'s')+'/div',val)
        self.tdiv.setCurrentIndex(TIME_SCALES.index(.5))
        self.time_slider=QSlider(Qt.Horizontal); self.time_slider.setRange(0,len(TIME_SCALES)-1); self.time_slider.setValue(self.tdiv.currentIndex())
        self.tdiv.currentIndexChanged.connect(self.time_combo_changed); self.time_slider.valueChanged.connect(self.tdiv.setCurrentIndex)
        self.pan=spin(0,decimals=6); self.pan.setSuffix(' s'); self.pan_slider=QSlider(Qt.Horizontal); self.pan_slider.setRange(-400,400)
        self.pan.valueChanged.connect(self.pan_spin_changed); self.pan_slider.valueChanged.connect(self.pan_slider_changed)
        for w in [QLabel('Time / division'),self.tdiv,self.time_slider,QLabel('Horizontal position'),self.pan,self.pan_slider]: lay.addWidget(w)
        lay.addStretch()
        lay=page('TRIGGER')
        self.mode=combo(['Auto','Normal']); self.source=combo(['CH1','CH2','CH3','CH4']); self.slope=combo(['Rising','Falling']); self.level=spin(0); self.level.setSuffix(' V')
        self.trigger_slider=QSlider(Qt.Horizontal); self.trigger_slider.setRange(-400,400)
        self.mode.currentTextChanged.connect(self.trigger_changed); self.source.currentIndexChanged.connect(self.trigger_changed); self.slope.currentTextChanged.connect(self.trigger_changed); self.level.valueChanged.connect(self.trigger_changed); self.trigger_slider.valueChanged.connect(self.trigger_slider_changed)
        for label,w in [('Mode',self.mode),('Source',self.source),('Slope',self.slope),('Level',self.level)]: lay.addWidget(QLabel(label)); lay.addWidget(w)
        self.mode.setToolTip('Edge trigger · 20% pre-trigger'); lay.addWidget(self.trigger_slider); lay.addStretch()
        lay=page('CURSOR')
        self.cursor_mode=combo(['Off','Time','Value','Time + Value']); self.cursor_mode.currentTextChanged.connect(self.cursor_mode_changed)
        self.reset_cursor_button=QPushButton('Center cursors'); self.reset_cursor_button.clicked.connect(self.reset_cursors)
        self.cursor_readout=QLabel('—'); self.cursor_readout.setObjectName('cursor'); self.cursor_readout.setWordWrap(True)
        for w in [QLabel('Type'),self.cursor_mode,self.reset_cursor_button,self.cursor_readout]: lay.addWidget(w)
        lay.addStretch()
        lay=page('MEASURE')
        self.measure_source=combo(['CH1','CH2','CH3','CH4']); self.measure_source.currentIndexChanged.connect(self.measure_changed)
        lay.addWidget(QLabel('Source')); lay.addWidget(self.measure_source); lay.addWidget(QLabel('Visible measurements'))
        self.measure_checks={}
        for key,label in metric_defs:
            check=QCheckBox(label.title()); check.setChecked(True); check.toggled.connect(self.metric_cards[key].setVisible); self.measure_checks[key]=check; lay.addWidget(check)
        lay.addStretch()
        lay=page('SAVE')
        self.csv_button=QPushButton('Export CSV'); self.csv_button.clicked.connect(self.export_csv)
        self.png_button=QPushButton('Screenshot'); self.png_button.clicked.connect(self.export_png)
        self.help_button=QPushButton('Connection guide'); self.help_button.clicked.connect(self.help)
        self.csv_button.setToolTip('Exports raw calibrated capture samples, before acquisition averaging, Hi-Res, coupling, probe scaling or display interpolation.')
        for w in [self.csv_button,self.png_button,self.help_button]: lay.addWidget(w)
        lay.addStretch()
        footer=QHBoxLayout(); self.notice=QLabel('Frames 0 · Gaps 0 · Invalid 0 · Drops 0'); self.notice.setObjectName('notice'); footer.addWidget(self.notice,1)
        self.error_badge=QLabel(); self.error_badge.setObjectName('errorBadge'); self.error_badge.hide(); footer.addWidget(self.error_badge); outer.addLayout(footer)
        self.show_menu('ACQUIRE'); self.set_run_state(False); self.acquisition_changed()

    def theme(self):
        self.setStyleSheet(stylesheet())

    def show_menu(self,name):
        if name not in self.menu_pages: return
        self.menu_title.setText(name); self.menu_stack.setCurrentWidget(self.menu_pages[name])
        for key,button in self.menu_buttons.items():
            button.setProperty('selected',key==name); repolish(button)

    def probe_factor(self,i):
        if i>=len(self.probes): return 1.0
        text=self.probes[i].currentText().upper().rstrip('X')
        try: return float(text)
        except ValueError: return 1.0

    def displayed_channel(self,i):
        if self.current is None or i>=self.current.y.shape[1]: return None
        data=self.processed_y if self._processed_capture is self.current and self.processed_y is not None else self.current.y
        y=data[:,i].astype(float,copy=True)
        coupling=self.couplings[i].currentText()
        if coupling=='AC' and len(y): y-=self.ac_reference(i)
        elif coupling=='GND': y[:]=0.
        return y*self.probe_factor(i)

    def ac_reference(self,ch):
        # During capture the exact reference passed to the trigger engine is held
        # with that record. Held captures must not drift with later buffer data.
        if self._processed_capture is self.current and self._capture_ac_reference is not None and ch<len(self._capture_ac_reference):
            return float(self._capture_ac_reference[ch])
        return self.latest_reference(ch)

    def latest_reference(self,ch):
        if self.engine.y is not None and len(self.engine.t) and ch<self.engine.y.shape[1]:
            low=self.engine.t[-1]-10*self.tdiv.currentData()
            first=np.searchsorted(self.engine.t,low,side='left')
            return float(np.mean(self.engine.y[first:,ch]))
        if self.current is not None and len(self.current.y) and ch<self.current.y.shape[1]:
            return float(np.mean(self.current.y[:,ch]))
        return 0.

    def accept_capture(self,capture,references=None):
        self.current=capture
        self._capture_ac_reference=np.array(references if references is not None else [self.latest_reference(i) for i in range(capture.y.shape[1])])
        self.processed_y=self.processor.process(capture,self.acquisition_mode.currentText(),int(self.average_n.currentText()),int(self.hires_width.currentText()))
        self._processed_capture=capture

    def reset_processing(self):
        self.processor.reset()
        # Settings invalidate averaging history, not the record held on screen.
        if self.current is None:
            self.processed_y=None; self._processed_capture=None; self._capture_ac_reference=None

    def acquisition_changed(self,*_):
        self.reset_processing()
        mode=self.acquisition_mode.currentText()
        self.average_n.setEnabled(mode=='Average'); self.hires_width.setEnabled(mode=='Hi-Res')
        if self.current is not None: self.accept_capture(self.current,self._capture_ac_reference)
        self.draw_capture(); self.update_measurements()

    def channel_toggled(self,*_):
        self.controls_enabled(self.link_open and self.metadata is not None)
        self.draw_capture()

    def probe_changed(self,i):
        old=self._probe_factors[i]; new=self.probe_factor(i); ratio=new/old
        self._probe_factors[i]=new
        target=self.vdivs[i].currentData()*ratio
        idx=min(range(len(SCALES)),key=lambda j:abs(math.log(SCALES[j]/target)))
        self.vdivs[i].blockSignals(True); self.vdivs[i].setCurrentIndex(idx); self.vdivs[i].blockSignals(False)
        self.vdiv_sliders[i].blockSignals(True); self.vdiv_sliders[i].setValue(idx); self.vdiv_sliders[i].blockSignals(False)
        self.positions[i].blockSignals(True); self.positions[i].setValue(self.positions[i].value()*ratio); self.positions[i].blockSignals(False)
        if self.source.currentIndex()==i:
            self.level.blockSignals(True); self.level.setValue(self.level.value()*ratio); self.level.blockSignals(False)
        self.reset_processing(); self.position_spin_changed(i); self.update_measurements(); self.update_trigger_slider()

    def set_run_state(self,running,single=False):
        self.running=bool(running); self.single_pending=bool(single and running)
        self.run_button.setText('STOP' if running else 'RUN')
        self.run_button.setProperty('running',bool(running)); repolish(self.run_button)
        if running: status='Armed' if single else 'Running'
        elif self.metadata is None or not self.link_open: status='Disconnected'
        elif self.current is None: status='Ready'
        else: status='Stopped'
        self.state.setText('●  '+status); self.state.setProperty('state',status.lower()); repolish(self.state)

    def scale_combo_changed(self,i,idx):
        self.reset_processing()
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
        self.reset_processing()
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
        ch=max(0,self.source.currentIndex()); factor=self.probe_factor(ch)
        if self.couplings[ch].currentText()=='GND': return float('inf')
        raw=self.level.value()/factor
        if self.couplings[ch].currentText()=='AC': raw+=self.latest_reference(ch)
        return raw

    def update_status_readouts(self):
        if not hasattr(self,'tdiv') or not hasattr(self,'level'): return
        if self.single_pending: trig='ARMED'
        elif self.current is None: trig='READY' if self.metadata else 'WAITING'
        elif not self.running: trig='HOLD'
        else: trig="TRIG'D" if self.current.triggered else 'AUTO'
        acquisition=self.acquisition_mode.currentText()
        if acquisition=='Average': acquisition+=f' {self.processor.count}/{self.average_n.currentText()}'
        self.top_status.setText(f'{trig}    H {fmt(self.pan.value(),"s")}    {self.mode.currentText()} · {acquisition}')
        ch=max(0,self.source.currentIndex()); unit=self.channels[ch].unit; freq=None
        if self.current is not None and ch<self.current.y.shape[1]:
            y=self.displayed_channel(ch); freq=measurements(self.current.t,y)['frequency'] if y is not None else None
        channels='  '.join(f'{i+1}: {fmt(self.vdivs[i].currentData(),self.channels[i].unit)}/div' for i in range(4) if self.channel_checks[i].isChecked())
        text=f'{channels}   {fmt(self.tdiv.currentData(),"s")}/div   T: CH{ch+1} {fmt(self.level.value(),unit)}   {fmt(freq,"Hz")}'
        self.bottom_status.setText(text); self.bottom_status.setToolTip(text)

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
        if not hasattr(self,'run_button'): return
        for w in [self.run_button,self.single_button]: w.setEnabled(ready)
        for i in range(4):
            present=self.metadata is not None and i<len(self.metadata.channels)
            self.channel_checks[i].setEnabled(present)
            active=present and self.channel_checks[i].isChecked()
            for w in [self.vdivs[i],self.vdiv_sliders[i],self.positions[i],self.position_sliders[i],self.couplings[i],self.probes[i]]: w.setEnabled(active)
        has=self.current is not None and len(self.current.t)>0
        for w in [self.auto_button,self.csv_button,self.cursor_mode,self.reset_cursor_button,self.calibration_button]: w.setEnabled(has)

    def toggle_connection(self):
        if self.reader is not None: self.disconnect(); return
        try:
            address=self.port.currentText().strip(); rate=int(self.rate.currentText())
            if not address or rate<=0 or (self.transport.currentText()=='TCP' and rate>65535): raise ValueError()
        except ValueError: QMessageBox.warning(self,'Connection','Select a port/address and a valid baud rate or TCP port.'); return
        self.clear_capture(); self.engine.clear(); self.metadata=None; self.gaps=self.frames=0; self.last_rx=0
        self.initial_fit_pending=True
        self.reader=Reader(self.transport.currentText(),address,rate); self.reader.start()
        self.connect_button.setText('Disconnect'); self.state.setText('●  CONNECTING')
        for w in [self.port,self.rate,self.transport,self.refresh]: w.setEnabled(False)
        self.controls_enabled(False)

    def disconnect(self,error=None):
        if self.reader: self.reader.close()
        self.reader=None; self.link_open=False; self.set_run_state(False)
        self.connect_button.setText('Connect'); self.capture_label.setText('HOLD' if self.current else 'WAITING')
        self.error_badge.setText('ERROR' if error else ''); self.error_badge.setToolTip(error or ''); self.error_badge.setVisible(bool(error))
        for w in [self.port,self.rate,self.transport]: w.setEnabled(True)
        self.refresh.setEnabled(self.transport.currentText()=='Serial'); self.controls_enabled(False); self.update_status_readouts()

    def accept_metadata(self,meta):
        if meta==self.metadata: return
        self.metadata=meta; self.engine.clear(); self.clear_capture(); self.running=False; self.single_pending=False
        self.initial_scale=True
        self.initial_fit_pending=True
        self.device_label.setText(meta.device); self.profile_button.setToolTip('Device & sensors · '+meta.device); self.capture_label.setText('READY')
        self.link_open=True; self.set_run_state(False)
        for i,raw in enumerate(meta.channels):
            ch=self.channels[i]
            if ch.sensor=='Device units': ch.name,ch.unit=raw['name'],raw['unit']
        self.source.clear(); self.measure_source.clear()
        self.source.addItems([f'CH{i+1}' for i in range(len(meta.channels))]); self.measure_source.addItems([f'CH{i+1}' for i in range(len(meta.channels))])
        for i,check in enumerate(self.channel_checks): check.setChecked(i<len(meta.channels))
        self.channel_labels(); self.controls_enabled(True); self.update_status_readouts()

    def channel_labels(self):
        for i,ch in enumerate(self.channels):
            ident=f'CH{i+1}'; label=ident if ch.name.strip().upper()==ident else f'{ident} · {ch.name}'
            self.channel_checks[i].setText(f'{label} ({ch.unit})'); self.channel_checks[i].setToolTip(f'{label} ({ch.unit})')
            for j,v in enumerate(SCALES): self.vdivs[i].setItemText(j,fmt(v,ch.unit)+'/div')
            self.positions[i].setSuffix(' '+ch.unit)
        self.level.setSuffix(' '+self.channels[max(0,self.source.currentIndex())].unit)
        self.draw_capture()

    def poll(self):
        reader=self.reader
        if reader is None: return
        for _ in range(64):
            try: kind,item=reader.events.get_nowait()
            except queue.Empty: break
            if kind=='error': self.disconnect(item); QMessageBox.warning(self,'Hardware connection',item); return
            if kind=='connected': self.link_open=True; self.state.setText('●  WAITING'); self.capture_label.setText('WAITING')
            if kind=='data':
                if isinstance(item,Metadata): self.accept_metadata(item)
                else:
                    self.last_rx=time.monotonic(); self.frames+=1
                    if item.gap: self.gaps+=1; self.reset_processing()
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
            references=[self.latest_reference(i) for i in range(len(self.metadata.channels))]
            capture=self.engine.capture(10*self.tdiv.currentData(),max(0,self.source.currentIndex()),self.trigger_engine_level(),self.slope.currentText(),'Normal' if self.single_pending else self.mode.currentText(),auto,prefer_newest=not self.single_pending)
            if capture is not None:
                self.accept_capture(capture,references); self.last_capture=now
                if self.initial_fit_pending:
                    self.initial_fit_pending=False
                    if self.initial_autofit.isChecked(): self.fit_first_capture()
                elif self.autorange_box.isChecked(): self.autorange_capture()
                self.draw_capture(); self.update_measurements(); self.controls_enabled(True)
                if self.single_pending:
                    self.set_run_state(False); self.capture_label.setText('STOP')
                else: self.capture_label.setText("TRIG'D" if capture.triggered else 'AUTO')
            elif now-self.last_capture>1: self.capture_label.setText('ARMED' if self.single_pending or self.mode.currentText()=='Normal' else 'WAITING')
        stale=not self.last_rx or now-self.last_rx>max(3.,20*self.tdiv.currentData())
        self.notice.setText(f'Frames {self.frames} · Gaps {self.gaps} · Invalid {reader.invalid} · Drops {reader.dropped}')
        errors=[]
        if self.current is not None and self.current.clipped: errors.append('ADC LIMIT')
        if self.last_display_clip: errors.append('TRACE OFF-SCREEN')
        if suspicious_gain(self.channels[0].gain): errors.append('UNVERIFIED GAIN')
        if reader.last_error: errors.append('ERROR')
        if reader.invalid: errors.append('INVALID')
        if reader.dropped: errors.append('DROPS')
        self.error_badge.setText(' · '.join(errors)); self.error_badge.setToolTip(reader.last_error or 'Acquisition range or data quality warning')
        self.error_badge.setVisible(bool(errors))
        if stale and self.running: self.capture_label.setText('WAITING')
        self.update_status_readouts()

    def toggle_run(self):
        if not self.link_open or self.metadata is None: return
        if self.running:
            self.set_run_state(False); self.capture_label.setText('STOP'); self.update_status_readouts()
        else: self.arm(False)

    def single(self):
        if self.link_open and self.metadata is not None: self.arm(True)

    def arm(self,single):
        self.engine.clear(); self.reset_processing(); self.last_capture=time.monotonic()
        self.set_run_state(True,single); self.capture_label.setText('ARMED' if single else 'RUN'); self.update_status_readouts()

    def clear_capture(self):
        self.current=None; self.last_display_clip=False; self.reset_processing(); self._capture_ac_reference=None
        for curve in self.curves+self.glows+self.sample_dots: curve.setData([],[])
        self.empty.show(); self.trigger_time.hide()
        for marker in self.ground_markers+[self.level_marker,self.time_marker]: marker.hide()
        self.update_measurements(); self.update_cursor(); self.update_status_readouts()

    def time_changed(self):
        self.initial_scale=False; self.engine.clear(); self.reset_processing(); self.update_axes(); self.ensure_cursors_visible()

    def update_axes(self):
        span=10*self.tdiv.currentData(); center=self.pan.value(); low=center-.2*span; high=center+.8*span
        self.plot.setXRange(low,high,padding=0); self.plot.setYRange(-4,4,padding=0)
        self.graticule.set_window(low,high); self.empty.setPos((low+high)/2,0)
        self.draw_capture(); self.update_status_readouts()

    def draw_capture(self,*_):
        if not hasattr(self,'measure_source'): return
        remaining=1/30-(time.monotonic()-self._last_draw)
        if remaining>0:
            if not self._redraw_timer.isActive(): self._redraw_timer.start(max(1,math.ceil(remaining*1000)))
            return
        self._redraw_timer.stop(); self._last_draw=time.monotonic(); self._render_capture()

    def _render_capture(self):
        if not hasattr(self,'level') or not hasattr(self,'measure_source'): return
        c=self.current; low,high=self.plot.viewRange()[0]
        if c is not None and len(c.t):
            self.empty.hide()
            self.last_display_clip=False
            for i,curve in enumerate(self.curves):
                visible=i<c.y.shape[1] and self.channel_checks[i].isChecked()
                if visible:
                    actual_y=self.displayed_channel(i)
                    y=(actual_y-self.positions[i].value())/self.vdivs[i].currentData()
                    # Show display clipping independent of ADC clipping.
                    _,outside=display_range_health(actual_y,self.vdivs[i].currentData(),self.positions[i].value())
                    self.last_display_clip |= outside
                    # Interpolate only the visible span plus neighboring taps.
                    start=max(0,int(np.searchsorted(c.t,low))-8); end=min(len(c.t),int(np.searchsorted(c.t,high))+8)
                    t,values=display_interpolate(c.t[start:end],y[start:end],max(1,int(self.plot.getViewBox().width())),self.interpolation.currentText())
                    curve.setData(t,values,skipFiniteCheck=True); self.glows[i].setData(t,values,skipFiniteCheck=True)
                    mask=(c.t>=low)&(c.t<=high)
                    self.sample_dots[i].setData(c.t[mask] if mask.sum()<80 else [],y[mask] if mask.sum()<80 else [])
                else:
                    curve.setData([],[]); self.glows[i].setData([],[]); self.sample_dots[i].setData([],[])
            self.trigger_time.setVisible(c.triggered)
        else:
            self.empty.show()
            for curve in self.curves+self.glows+self.sample_dots: curve.setData([],[])
        ch=max(0,self.source.currentIndex()); level=(self.level.value()-self.positions[ch].value())/self.vdivs[ch].currentData()
        self.syncing=True; self.trigger_line.setValue(level); self.syncing=False
        self.trigger_line.setVisible(self.metadata is not None)
        for i,marker in enumerate(self.ground_markers):
            marker.setPos(low,max(-3.85,min(3.85,-self.positions[i].value()/self.vdivs[i].currentData())))
            marker.setVisible(self.metadata is not None and i<len(self.metadata.channels) and self.channel_checks[i].isChecked())
        self.level_marker.setPos(high,max(-3.85,min(3.85,level))); self.level_marker.setVisible(self.metadata is not None)
        self.time_marker.setPos(max(low,min(high,0)),4); self.time_marker.setVisible(c is not None and c.triggered)
        self.update_cursor(); self.update_status_readouts()

    def trigger_dragged(self):
        if self.syncing: return
        ch=max(0,self.source.currentIndex())
        self.level.setValue(self.trigger_line.value()*self.vdivs[ch].currentData()+self.positions[ch].value()); self.update_trigger_slider()

    def trigger_changed(self,*_):
        self.engine.last_trigger=-math.inf; self.reset_processing()
        if hasattr(self,'level'): self.level.setSuffix(' '+self.channels[max(0,self.source.currentIndex())].unit)
        self.update_trigger_slider(); self.draw_capture(); self.update_measurements(); self.update_status_readouts()

    def measure_changed(self,*_): self.update_measurements(); self.update_cursor()

    def update_measurements(self):
        if not hasattr(self,'measure_source'): return
        ch=max(0,self.measure_source.currentIndex())
        if self.current is None or not len(self.current.t) or ch>=self.current.y.shape[1]:
            for value in self.metrics.values(): value.setText('—')
            self.sample_label.setText('—'); self.update_reference_status(); return
        y=self.displayed_channel(ch); values=measurements(self.current.t,y); unit=self.channels[ch].unit
        for key,w in self.metrics.items():
            text=fmt(values[key],'Hz' if key=='frequency' else 's' if key=='period' else '%' if key=='duty' else unit)
            w.setText(text); w.setToolTip(text)
        dt=np.diff(self.current.t)
        if len(dt):
            median=float(np.median(dt)); jitter=np.max(np.abs(dt-median))/median if median>0 else math.inf
            timing=f'{fmt(1/median,"Sa/s")} · {len(self.current.t):,} pts' if median>0 else f'{len(self.current.t):,} pts'
            if jitter>=.01: timing+=' · irregular'
        else: timing='1 pt'
        self.sample_label.setText(timing); self.update_status_readouts(); self.update_reference_status()

    def update_cursor(self,*_):
        if not hasattr(self,'cursor_mode'): return
        mode=self.cursor_mode.currentText(); has=self.current is not None
        for i,line in enumerate(self.cursors): line.setVisible(has and (mode in ('Time','Time + Value') if i<2 else mode in ('Value','Time + Value')))
        if not has: self.cursor_readout.setText('—'); return
        if mode=='Off': self.cursor_readout.setText('—'); return
        ch=max(0,self.measure_source.currentIndex()); unit=self.channels[ch].unit; lines=[]
        if mode in ('Time','Time + Value'):
            a,b=[line.value() for line in self.cursors[:2]]; delta=b-a
            lines.extend([f'T1  {fmt(a,"s")}    T2  {fmt(b,"s")}',f'ΔT  {fmt(delta,"s")}',f'1/|ΔT|  {fmt(1/abs(delta),"Hz") if abs(delta)>1e-15 else "—"}'])
        if mode in ('Value','Time + Value'):
            a,b=[line.value()*self.vdivs[ch].currentData()+self.positions[ch].value() for line in self.cursors[2:]]
            lines.extend([f'Y1  {fmt(a,unit)}',f'Y2  {fmt(b,unit)}',f'ΔY  {fmt(b-a,unit)}'])
        self.cursor_readout.setText('\n'.join(lines))

    def reset_cursors(self):
        low,high=self.plot.viewRange()[0]
        for line,value in zip(self.cursors,[low+(high-low)*.25,low+(high-low)*.75,-1,1]): line.setValue(value)

    def ensure_cursors_visible(self):
        if not hasattr(self,'cursor_mode') or self.cursor_mode.currentText()=='Off': return
        low,high=self.plot.viewRange()[0]
        if any(line.value()<low or line.value()>high for line in self.cursors[:2]): self.reset_cursors()

    def cursor_mode_changed(self,*_):
        self.ensure_cursors_visible(); self.update_cursor()

    def fit_first_capture(self):
        """Center the first real capture, rather than leaving 3.3 V off a 1 mV grid.

        Never changes measured samples or their physical calibration. Matches the
        reference's 500 ms/div timebase for the 1 Hz ESP32 reference waveform.
        """
        if self.current is None or len(self.current.t)<8:
            return
        for i in range(min(self.current.y.shape[1],len(self.vdivs))):
            if not self.channel_checks[i].isChecked(): continue
            y=self.displayed_channel(i)
            result=fit_voltage_scale(y,SCALES)
            if result is None: continue
            scale, center=result
            if scale!=self.vdivs[i].currentData():
                self.vdivs[i].setCurrentIndex(SCALES.index(scale))
            self.positions[i].setValue(center)
        ch=min(self.source.currentIndex(),self.current.y.shape[1]-1)
        y=self.displayed_channel(ch)
        if y is not None and len(y):
            mid=(float(np.min(y))+float(np.max(y)))/2
            self.level.setValue(mid)
        self.draw_capture()
        self.update_measurements()

    def autorange_capture(self):
        if self.current is None: return
        for i in range(self.current.y.shape[1]):
            if not self.channel_checks[i].isChecked(): continue
            y=self.displayed_channel(i)
            if y is None or not len(y): continue
            extent=max(float(np.ptp(y))/6,1e-6)
            choice=next((v for v in SCALES if v>=extent),SCALES[-1])
            if self.vdivs[i].currentData()!=choice: self.vdivs[i].setCurrentIndex(SCALES.index(choice))
            center=float((np.max(y)+np.min(y))/2)
            if abs(center-self.positions[i].value()) > .15*self.vdivs[i].currentData(): self.positions[i].setValue(center)
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

    def apply_tek_preset(self):
        """Align DISPLAY controls to reference screenshot, not ADC physics."""
        self.reference_active=True
        self.tdiv.setCurrentIndex(TIME_SCALES.index(.5))
        # Probe changes rescale vertical controls, so restore 1X FIRST.
        self.probes[0].setCurrentText('1X')
        self.vdivs[0].setCurrentIndex(SCALES.index(1.))
        self.positions[0].setValue(1.65)
        self.couplings[0].setCurrentText('DC')
        self.interpolation.setCurrentText('Linear')
        self.pan.setValue(0.)
        self.source.setCurrentIndex(0)
        self.measure_source.setCurrentIndex(0)
        self.mode.setCurrentText('Auto')
        self.slope.setCurrentText('Rising')
        self.level.setValue(1.65)
        self.autorange_box.setChecked(False)
        self.update_axes()
        self.update_measurements()
        self.update_reference_status()

    def update_reference_status(self):
        """Always distinguish incoming firmware volts from software-corrected volts."""
        if not hasattr(self,'reference_status') or not hasattr(self,'scale_audit_label'):
            return
        gain=self.channels[0].gain
        offset=self.channels[0].offset
        if self.current is None or len(self.current.t)<8 or self.current.y.shape[1]<1:
            self.scale_audit_label.setText(
                f'SCALE CHECK  ·  CH1 software gain ×{gain:.6g}, offset {offset:+.6g} V  ·  awaiting live samples')
            self.reference_status.setText('TEK reference: 1 Hz · nominal 3.3 Vpp · 1 V/div')
            self.scale_audit.setProperty('warning',suspicious_gain(gain))
            repolish(self.scale_audit)
            return
        y=self.displayed_channel(0)
        data=measurements(self.current.t,y)
        vpp,freq=data['vpp'],data['frequency']
        raw_pp=raw_span_from_calibrated(self.current.y[:,0],gain)
        raw_text=fmt(raw_pp,self.channels[0].unit)
        corrected_text=fmt(vpp,self.channels[0].unit)
        _,outside=display_range_health(y,self.vdivs[0].currentData(),self.positions[0].value())
        warning=suspicious_gain(gain) or abs(offset)>5 or outside
        flags=[]
        if suspicious_gain(gain): flags.append('UNVERIFIED HIGH GAIN')
        if outside: flags.append('DISPLAY OFF-SCREEN — AUTOSET')
        if vpp is not None and raw_pp is not None and raw_pp<.05 and gain>5:
            flags.append('3.3 V DISPLAY MAY BE SOFTWARE-AMPLIFIED')
        if not flags: flags.append('CH1 values are read from the device; not metrology-certified')
        self.scale_audit_label.setText(
            f'CH1 INPUT {raw_text} p-p  →  DISPLAY {corrected_text} p-p '
            f'  |  gain ×{gain:.6g}  offset {fmt(offset,"V")}  |  '+ ' · '.join(flags))
        self.scale_audit.setProperty('warning',warning)
        repolish(self.scale_audit)
        if not self.reference_active:
            self.reference_status.setText('Select TEK PRESET to match 1 V/div and 500 ms/div')
        elif suspicious_gain(gain):
            self.reference_status.setText('⚠ 3.3 V on display is NOT verified — reset CH1 gain/check firmware')
        elif vpp is None:
            self.reference_status.setText('Awaiting signal')
        elif 2.6<=vpp<=4.0 and (freq is None or abs(freq-1.)<=.25):
            self.reference_status.setText(f'CH1 {fmt(vpp,"V")} p-p · {fmt(freq,"Hz")} · nominal reference')
        else:
            self.reference_status.setText(f'CH1 {fmt(vpp,"V")} p-p · {fmt(freq,"Hz")} · check ADC/units')
        self.reference_status.setToolTip('Hardware waveform and independently verified input are required for actual calibration.')
        self.reference_status.setObjectName('referenceWarning' if warning else 'referenceLabel')
        repolish(self.reference_status)

    def reset_ch1_gain(self):
        """Undo persisted large software calibration; never falsify sensor units."""
        ch=self.channels[0]
        if abs(ch.gain-1.)<1e-12 and abs(ch.offset)<1e-12:
            QMessageBox.information(self,'CH1 scaling','CH1 already uses gain 1 and offset 0. The incoming firmware values are displayed as transmitted.')
            return
        message=(f'CH1 is currently using gain ×{ch.gain:.7g} and offset {ch.offset:+.7g} V.\n\n'
                 'Restore gain = 1 and offset = 0? This will reveal what the board actually transmits. '
                 'If the firmware reports millivolts as volts, you must correct the firmware or its wiring.')
        if QMessageBox.question(self,'Reset software voltage gain',message,QMessageBox.Yes|QMessageBox.No,QMessageBox.Yes)!=QMessageBox.Yes:
            return
        was_running=self.running
        ch.gain=1.0; ch.offset=0.0
        self.reset_after_scaling_change(was_running)

    def reset_after_scaling_change(self,was_running):
        self.engine.clear(); self.clear_capture(); self.save_profiles()
        self.initial_fit_pending=True
        if was_running and self.link_open and self.metadata:
            self.arm(False)
        else:
            self.set_run_state(False)
            self.controls_enabled(self.link_open and self.metadata is not None)
        self.update_reference_status()

    def calibrate_channel(self):
        """User-authorized calibration with transparent before/after calculation."""
        if self.current is None or not len(self.current.t):
            QMessageBox.information(self,'Calibration','Acquire a hardware waveform before calibrating.')
            return
        index=max(0,self.measure_source.currentIndex())
        if index>=self.current.y.shape[1]: return
        channel=self.channels[index]
        d=QDialog(self); d.setWindowTitle(f'CH{index+1} — two-point voltage calibration'); d.resize(510,340)
        layout=QVBoxLayout(d)
        note=QLabel('Enter LOW/HIGH voltages measured independently for the SAME channel quantity transmitted by the firmware (not a different point on the circuit). For the 10k/10k ESP32 divider, the ADC pin is ~0–1.65 V; the supplied firmware reports reconstructed ~0–3.3 V. A major mismatch is a wiring or firmware-unit problem, NOT something this dialog can fix.')
        note.setWordWrap(True); layout.addWidget(note)
        form=QFormLayout(); low=spin(0.,-1000,1000,6); high=spin(3.3,-1000,1000,6)
        low.setSuffix(' V'); high.setSuffix(' V')
        form.addRow('Verified LOW level',low); form.addRow('Verified HIGH level',high)
        preview=QLabel(); preview.setWordWrap(True); preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow('Calculated correction',preview); layout.addLayout(form)
        confirmation=QCheckBox('I verified the voltage reference and GPIO / GND wiring.')
        layout.addWidget(confirmation)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); layout.addWidget(buttons)
        latest=[None]
        def update_preview():
            try:
                result=fit_two_point(self.current.y[:,index],channel.gain,channel.offset,low.value(),high.value())
                latest[0]=result
                notice='\n⚠ Large correction: verify the ADC output and units before accepting.' if result.suspicious else ''
                preview.setText(f'ADC low {fmt(result.measured_low,"V")}  |  high {fmt(result.measured_high,"V")}\nGain {result.gain:.8g}   Offset {result.offset:.8g}\nChange ×{result.gain_multiplier:.5g}{notice}')
            except (ValueError,OverflowError,ZeroDivisionError) as exc:
                latest[0]=None; preview.setText(str(exc))
        low.valueChanged.connect(update_preview); high.valueChanged.connect(update_preview)
        update_preview()
        def apply():
            result=latest[0]
            if result is None:
                QMessageBox.warning(d,'Invalid calibration','A valid captured square wave is required.'); return
            if not confirmation.isChecked():
                QMessageBox.warning(d,'Reference not verified','Check the confirmation box only after verifying the actual signal.'); return
            if result.suspicious:
                QMessageBox.warning(d,'Unsafe calibration blocked',
                    f'This calibration requires a {result.gain_multiplier:.6g}× correction.\n\n'
                    'Corrections outside 0.2×–5× are blocked because they can make a '
                    '4.8 mV firmware error look like a valid 3.3 V waveform.\n\n'
                    'Check USB firmware, ADC wiring, V/mV conversion and resistor divider. '
                    'Use RESET CH1 GAIN to discard the old factor. '
                    'Only use Settings → Gain for independently validated unusual sensor conversions.')
                return
            was_running=self.running
            channel.gain=result.gain; channel.offset=result.offset
            self.reset_after_scaling_change(was_running)
            self.capture_label.setText('RUN' if was_running else 'READY — press RUN')
            d.accept()
        buttons.accepted.connect(apply); buttons.rejected.connect(d.reject)
        d.exec()

    def configure(self):
        dlg=ProfileDialog(self)
        if dlg.exec()!=QDialog.Accepted: return
        self.board_profile=dlg.board.currentText()
        for ch,fields in zip(self.channels,dlg.fields):
            sensor,name,unit,gain,offset=fields
            ch.sensor,ch.name,ch.unit,ch.gain,ch.offset=sensor.currentText(),name.text().strip() or 'Input',unit.text().strip(),gain.value(),offset.value()
        self.set_run_state(False); self.engine.clear(); self.clear_capture()
        self.channel_labels(); self.save_profiles(); self.initial_fit_pending=True
        self.controls_enabled(self.link_open and self.metadata is not None)
        self.capture_label.setText('READY' if self.metadata else 'WAITING')
        self.set_run_state(False)

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
            '1. Load firmware compatible with docs/PROTOCOL.md.\n'
            '2. Choose Serial + port/baud or TCP + host/port.\n'
            '3. Connect; wait for device metadata (READY).\n'
            '4. Set channel calibration if needed, then press RUN.')

    def closeEvent(self,event):
        self.timer.stop(); self._redraw_timer.stop()
        self.plot.removeItem(self.graticule)
        if self.reader: self.reader.close(); self.reader.join(timeout=.5)
        event.accept()


def main():
    app=QApplication(sys.argv); font=QFont(); font.setFamilies(UI_FONTS); font.setPointSize(10); app.setFont(font)
    window=ScopeWindow(); window.show(); sys.exit(app.exec())


if __name__=='__main__': main()
