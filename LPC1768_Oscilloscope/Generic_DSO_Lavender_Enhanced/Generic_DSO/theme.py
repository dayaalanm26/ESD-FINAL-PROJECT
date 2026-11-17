"""Single palette and stylesheet for the classic DSO layout."""
BG = '#1E1B2E'
PANEL = '#2A2540'
CARD = '#342E4F'
BORDER = '#4A4170'
ACCENT = '#B8A4F4'
HOVER = '#C9B8FF'
PRESSED = '#9C86E0'
ACCENT_TEXT = BG
SOFT = '#E6DEFF'
SOFT_TEXT = '#2B2350'
TEXT = '#EEEAF8'
MUTED = '#A59CC4'
SCREEN = '#0E0C17'
GRID = '#3A3358'
AXES = '#5A4F86'
COLORS = ['#FFD84A', '#3EE6FF', '#FF6FB5', '#6EF0A0']
TRIGGER = '#FF9E57'
CURSOR = '#FFFFFF'
OK = '#6EF0A0'
WARNING = '#FFC857'
ERROR = '#FF6B81'
UI_FONTS = ['Segoe UI', 'Inter', 'Helvetica Neue', 'DejaVu Sans', 'sans-serif']
MONO_FONTS = ['Consolas', 'JetBrains Mono', 'DejaVu Sans Mono', 'monospace']


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
QLabel#title {{ font-size:23px; font-weight:700; padding-right:16px; }}
QLabel#muted, QLabel#small {{ color:{MUTED}; }}
QLabel#small {{ font-size:9px; font-weight:600; letter-spacing:1px; }}
QLabel#state {{ color:{MUTED}; background:{CARD}; border:1px solid {BORDER}; border-radius:6px; padding:7px 12px; font-weight:700; }}
QLabel#state[state="running"], QLabel#state[state="ready"] {{ color:{OK}; }}
QLabel#state[state="stopped"] {{ color:{ERROR}; }}
QLabel#state[state="armed"] {{ color:{TRIGGER}; }}
QFrame#instrument {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:6px; }}
QFrame#readoutBar {{ background:{SCREEN}; border:1px solid {BORDER}; border-radius:6px; }}
QLabel#scopeReadout {{ font-family:{mono}; font-size:10px; color:{TEXT}; }}
QFrame#softPanel {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:6px; }}
QWidget#menuPage, QScrollArea, QScrollArea > QWidget > QWidget {{ background:{PANEL}; border:0; }}
QLabel#menuTitle {{ color:{ACCENT}; font-size:11px; font-weight:700; letter-spacing:2px; padding:8px 2px; }}
QLabel#softNote {{ color:{MUTED}; font-size:11px; }}
QPushButton {{ background:{CARD}; border:1px solid {BORDER}; border-radius:6px; padding:6px 9px; font-weight:600; }}
QPushButton:hover {{ border-color:{ACCENT}; background:{BORDER}; }}
QPushButton:pressed {{ background:{PRESSED}; color:{ACCENT_TEXT}; }}
QPushButton:disabled {{ color:{MUTED}; background:{PANEL}; border-color:{CARD}; }}
QPushButton#primary {{ background:{ACCENT}; color:{ACCENT_TEXT}; border-color:{ACCENT}; }}
QPushButton#primary:hover {{ background:{HOVER}; }}
QPushButton#primary:pressed {{ background:{PRESSED}; }}
QPushButton#run {{ background:{ERROR}; color:{ACCENT_TEXT}; border:0; padding:14px; font-size:16px; font-weight:800; letter-spacing:2px; }}
QPushButton#run[running="true"] {{ background:{OK}; }}
QPushButton#run:disabled {{ background:{CARD}; color:{MUTED}; }}
QPushButton#softKey {{ background:{CARD}; color:{MUTED}; padding:8px 2px; font-size:10px; }}
QPushButton#softKey[selected="true"] {{ background:{SOFT}; color:{SOFT_TEXT}; border-color:{ACCENT}; }}
QPushButton#segment {{ padding:4px 2px; font-size:10px; color:{MUTED}; }}
QPushButton#segment:checked {{ background:{SOFT}; color:{SOFT_TEXT}; border-color:{ACCENT}; }}
QGroupBox {{ background:{CARD}; border:1px solid {BORDER}; border-radius:6px; margin:0; padding:7px; }}
QGroupBox QLabel {{ font-size:9px; color:{MUTED}; }}
QGroupBox QSlider {{ background:transparent; }}
QPushButton#channelToggle {{ text-align:left; padding:4px; font-size:11px; }}
QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit {{ background:{CARD}; border:1px solid {BORDER}; border-radius:6px; padding:4px; min-height:18px; selection-background-color:{ACCENT}; selection-color:{BG}; }}
QComboBox:disabled, QDoubleSpinBox:disabled {{ color:{MUTED}; border-color:{PANEL}; }}
QComboBox QAbstractItemView {{ background:{PANEL}; selection-background-color:{ACCENT}; selection-color:{BG}; }}
QSlider::groove:horizontal {{ height:3px; background:{BORDER}; border-radius:1px; }}
QSlider {{ min-height:16px; }}
QPushButton#segment:disabled {{ background:{CARD}; color:{MUTED}; border-color:{BORDER}; }}
QSlider::handle:horizontal {{ background:{SOFT}; width:10px; margin:-4px 0; border-radius:5px; }}
QSlider::handle:horizontal:disabled {{ background:{MUTED}; }}
QFrame#metric {{ background:{CARD}; border:1px solid {BORDER}; border-radius:6px; }}
QLabel#reading {{ font-family:{mono}; font-size:13px; font-weight:700; color:{TEXT}; }}
QLabel#cursor {{ background:{SCREEN}; border:1px solid {BORDER}; border-radius:6px; font-family:{mono}; font-size:11px; padding:9px; }}
QLabel#notice {{ color:{MUTED}; font-family:{mono}; font-size:10px; padding:2px; }}
QLabel#errorBadge {{ background:{ERROR}; color:{BG}; border-radius:6px; padding:4px 8px; font-weight:700; }}
QCheckBox {{ background:transparent; spacing:7px; }}
QCheckBox::indicator {{ width:13px; height:13px; border:1px solid {BORDER}; border-radius:3px; background:{CARD}; }}
QCheckBox::indicator:checked {{ background:{ACCENT}; border-color:{ACCENT}; }}
QToolTip {{ background:{SOFT}; color:{SOFT_TEXT}; border:1px solid {BORDER}; padding:5px; }}
'''


def channel_style(color):
    return f'QPushButton#channelToggle {{ color:{color}; border-color:{color}; background:{PANEL}; }} QPushButton#channelToggle:!checked, QPushButton#channelToggle:disabled {{ color:{MUTED}; border-color:{BORDER}; background:{CARD}; }}'
