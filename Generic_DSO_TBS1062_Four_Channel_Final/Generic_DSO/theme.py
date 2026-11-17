"""Light, high-contrast laboratory instrument palette. Channel colors work on white."""
BG = '#F3F7FC'
PANEL = '#FFFFFF'
CARD = '#F7F9FD'
BORDER = '#DCE4EF'
ACCENT = '#3263D9'
HOVER = '#2453C7'
PRESSED = '#1644AC'
ACCENT_TEXT = '#FFFFFF'
SOFT = '#E8EEFF'
SOFT_TEXT = '#1943A3'
TEXT = '#19283E'
MUTED = '#63728B'
SCREEN = '#FFFFFF'
GRID = '#CAD7E7'
AXES = '#98A9BD'
COLORS = ['#C87900', '#007E99', '#9556BD', '#19825B']
TRIGGER = '#B65843'
CURSOR = '#334A70'
OK = '#147954'
WARNING = '#A45D0B'
ERROR = '#B42337'
UI_FONTS = ['Segoe UI', 'Inter', 'Helvetica Neue', 'DejaVu Sans', 'sans-serif']
MONO_FONTS = ['Cascadia Code', 'Consolas', 'JetBrains Mono', 'DejaVu Sans Mono', 'monospace']


def repolish(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def stylesheet():
    ui = ','.join(f'"{x}"' for x in UI_FONTS)
    mono = ','.join(f'"{x}"' for x in MONO_FONTS)
    return f'''
QWidget {{ background:{BG}; color:{TEXT}; font-family:{ui}; font-size:12px; }}
QLabel {{ background:transparent; }}
QWidget#rootFrame {{ background:{BG}; }}
QLabel#title {{ color:{TEXT}; font-size:24px; font-weight:800; padding-right:6px; }}
QLabel#subtitle {{ color:{MUTED}; font-size:10px; font-weight:700; letter-spacing:1.4px; }}
QLabel#muted, QLabel#small {{ color:{MUTED}; }}
QLabel#small {{ font-size:10px; font-weight:700; letter-spacing:.8px; }}
QLabel#state {{ color:{MUTED}; background:{CARD}; border:1px solid {BORDER}; border-radius:8px; padding:7px 10px; font-weight:750; }}
QLabel#state[state="running"], QLabel#state[state="ready"] {{ color:{OK}; background:#ECFAF2; border-color:#B8E9D0; }}
QLabel#state[state="stopped"] {{ color:{WARNING}; background:#FFF8EA; border-color:#F6E1B8; }}
QLabel#state[state="armed"] {{ color:{ACCENT}; }}
QFrame#instrument {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:12px; }}
QFrame#readoutBar {{ background:#EDF3FA; border:1px solid {BORDER}; border-radius:7px; }}
QLabel#scopeReadout {{ font-family:{mono}; font-size:10px; color:{TEXT}; }}
QFrame#referenceBar {{ background:#EBF3FF; border:1px solid #CADCF7; border-radius:8px; }}
QLabel#referenceLabel {{ color:#2252A2; font-size:11px; font-weight:650; }}
QLabel#referenceWarning {{ color:{WARNING}; font-size:11px; font-weight:650; }}
QFrame#softPanel {{ background:#F7F9FE; border:1px solid {BORDER}; border-radius:8px; }}
QWidget#menuPage, QScrollArea, QScrollArea > QWidget > QWidget {{ background:#F7F9FE; border:0; }}
QLabel#menuTitle {{ color:{ACCENT}; font-size:11px; font-weight:800; letter-spacing:1.6px; padding:8px 2px; }}
QLabel#softNote {{ color:{MUTED}; font-size:11px; }}
QLabel#connectionHint {{ color:#945000; background:#FFF5DF; border:1px solid #EAC78A; border-radius:7px; padding:7px 11px; font-size:11px; font-weight:650; }}
QPushButton {{ background:#FFFFFF; border:1px solid #CDD8E8; border-radius:7px; padding:7px 10px; font-weight:650; }}
QPushButton:hover {{ border-color:{ACCENT}; background:#EDF3FF; }}
QPushButton:pressed {{ background:#DBE7FF; color:{SOFT_TEXT}; }}
QPushButton:disabled {{ color:#9BAAC0; background:#F3F5F9; border-color:#E0E7F0; }}
QPushButton#primary {{ background:{ACCENT}; color:{ACCENT_TEXT}; border-color:{ACCENT}; }}
QPushButton#primary:hover {{ background:{HOVER}; }}
QPushButton#primary:pressed {{ background:{PRESSED}; }}
QPushButton#preset {{ background:#EDF3FF; color:#2355B9; border-color:#C8D8F9; font-weight:750; }}
QPushButton#preset:hover {{ background:#DCE8FF; }}
QPushButton#resetGain {{ background:#FFF3E7; color:#925107; border-color:#EECBA5; font-size:10px; font-weight:700; }}
QPushButton#resetGain:hover {{ background:#FFE3C4; border-color:#DDA970; }}
QFrame#auditBar {{ background:#E8F6F2; border:1px solid #B7E3D6; border-radius:8px; }}
QFrame#auditBar[warning="true"] {{ background:#FFF0E7; border:1px solid #EEC39D; }}
QLabel#auditText {{ color:#126749; font-family:{mono}; font-size:11px; font-weight:700; }}
QFrame#auditBar[warning="true"] QLabel#auditText {{ color:#A45412; }}
QPushButton#run {{ background:#DFF8EA; color:#12784B; border:1px solid #A9E8C9; padding:14px; font-size:16px; font-weight:800; letter-spacing:1.6px; }}
QPushButton#run[running="true"] {{ background:#FCE3E7; color:#B42337; border-color:#F2B8C1; }}
QPushButton#run:disabled {{ background:#EEF2F8; color:#9AA8BB; border-color:#DCE4EF; }}
QPushButton#softKey {{ background:#FFFFFF; color:{MUTED}; padding:9px 2px; font-size:10px; }}
QPushButton#softKey[selected="true"] {{ background:#DFEAFF; color:#16449C; border-color:#84A7ED; }}
QPushButton#segment {{ padding:5px 2px; font-size:10px; color:{MUTED}; }}
QPushButton#segment:checked {{ background:#DDE9FF; color:{SOFT_TEXT}; border-color:#92AFE6; }}
QPushButton#segment:disabled {{ background:#F5F7FB; color:#9DA9B9; border-color:{BORDER}; }}
QGroupBox {{ background:#F8FAFD; border:1px solid {BORDER}; border-radius:8px; margin:0; padding:7px; }}
QGroupBox QLabel {{ font-size:10px; color:{MUTED}; }}
QGroupBox QSlider {{ background:transparent; }}
QPushButton#channelToggle {{ text-align:left; padding:6px 7px; font-size:11px; font-weight:800; }}
QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit {{ background:#FFFFFF; border:1px solid #D1DBE9; border-radius:6px; padding:5px; min-height:18px; selection-background-color:#D7E5FF; selection-color:{TEXT}; }}
QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus, QLineEdit:focus {{ border:1px solid {ACCENT}; }}
QComboBox:disabled, QDoubleSpinBox:disabled {{ color:#9CAAC1; border-color:#E7ECF3; background:#F5F7FA; }}
QComboBox QAbstractItemView {{ background:#FFFFFF; color:{TEXT}; selection-background-color:#DDE9FF; selection-color:{TEXT}; }}
QSlider::groove:horizontal {{ height:5px; background:#D6DFEC; border-radius:2px; }}
QSlider {{ min-height:18px; }}
QSlider::handle:horizontal {{ background:#4E74CE; width:12px; margin:-4px 0; border-radius:6px; }}
QSlider::handle:horizontal:disabled {{ background:#AEB9CA; }}
QFrame#metric {{ background:#F8FAFE; border:1px solid {BORDER}; border-radius:7px; }}
QLabel#reading {{ font-family:{mono}; font-size:13px; font-weight:750; color:{TEXT}; }}
QLabel#cursor {{ background:#FFFFFF; border:1px solid {BORDER}; border-radius:7px; font-family:{mono}; font-size:11px; padding:9px; }}
QLabel#notice {{ color:{MUTED}; font-family:{mono}; font-size:10px; padding:2px; }}
QLabel#errorBadge {{ background:#FCE6E9; color:{ERROR}; border:1px solid #F3B7BF; border-radius:6px; padding:4px 8px; font-weight:800; }}
QCheckBox {{ background:transparent; spacing:7px; }}
QCheckBox::indicator {{ width:14px; height:14px; border:1px solid #BAC8DC; border-radius:3px; background:#FFFFFF; }}
QCheckBox::indicator:checked {{ background:{ACCENT}; border-color:{ACCENT}; }}
QToolTip {{ background:#FFFFFF; color:{TEXT}; border:1px solid {BORDER}; padding:6px; }}
'''


def channel_style(color):
    return (f'QPushButton#channelToggle {{ color:{color}; border-color:{color}; background:#FFFFFF; }} '
            f'QPushButton#channelToggle:!checked, QPushButton#channelToggle:disabled '
            f'{{ color:#98A5B7; border-color:{BORDER}; background:#F1F4F8; }}')
