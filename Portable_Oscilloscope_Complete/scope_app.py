"""Standalone desktop interface for the Portable Oscilloscope."""
import argparse
import csv
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from dataclasses import dataclass
import math
from time import monotonic
import webbrowser

from demo import Demo
from scope_data import CaptureBuffer, measure, sample_rate, time_text
from scope_plot import BORDER, COLORS, INK, MONO, MUTED, PANEL, TEAL, UI, ScopePlot, select_fonts
from transport import Reader, serial_ports

BG = "#EDF4F7"
SOFT = "#F2F7FB"
DARK_TEAL = "#005552"
WHITE = "#FFFFFF"
SOURCES = ("USB serial", "TCP", "Demo: sensors", "Demo: PWM")
TIMES = (0.0001, 0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1,
         0.2, 0.5, 1, 2, 5, 10)
VOLTAGES = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50)
METRICS = (("Last", "last"), ("Peak to peak", "vpp"), ("Mean", "mean"),
           ("RMS", "rms"), ("Frequency", "frequency"), ("Duty cycle", "duty"))


@dataclass
class Channel:
    visible: bool = True
    scale: float = 1.0
    center: float = 1.65
    gain: float = 1.0
    offset: float = 0.0

    def adjusted(self, value):
        return value * self.gain + self.offset

    def display_arguments(self):
        return self.visible, self.scale, self.center, self.gain, self.offset


class ScopeApp:
    def __init__(self, root, demo=False):
        global UI, MONO
        UI, MONO = select_fonts(root)
        self.root = root
        root.title("Portable Oscilloscope")
        root.geometry("1320x830")
        root.minsize(1100, 690)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.reader = None
        self.simulated = None
        self.device = None
        self.buffer = CaptureBuffer()
        self.channels = [Channel() for _ in range(4)]
        self.frame = None
        self.running = False
        self.single_since = None
        self.single_time = 0.0
        self.changed = False
        self.last_sample = None
        self.connected = None
        self.invalid = self.dropped = 0
        self.dirty = True
        self.metrics_at = 0.0
        self.active = tk.StringVar(value="CH1")
        self.trigger = tk.StringVar(value="CH1")
        self.mode = tk.StringVar(value="Roll")
        self.slope = tk.StringVar(value="Rising")
        self.level = tk.StringVar(value="1.65")
        self.division = tk.StringVar(value=time_text(0.5))
        self.interpolation = tk.StringVar(value="Lines")
        self.vscale = tk.StringVar(value="1")
        self.center = tk.StringVar(value="1.65")
        self.source = tk.StringVar(value=SOURCES[2] if demo else SOURCES[0])
        self.address = tk.StringVar()
        self.baud = tk.StringVar(value="921600")
        self.status_text = tk.StringVar(value="Offline")
        self.device_text = tk.StringVar(value="No device connected")
        self.plot_hint = tk.StringVar(value="500 ms/div  ·  Rolling display")
        self.capture_tag = tk.StringVar(value="IDLE")
        self.cursor_text = tk.StringVar(value="Click the plot to place A/B cursors")
        self.footer_text = tk.StringVar(value="Choose a USB port or try a Demo source.")
        self.stats = {}
        self.channel_checks = []
        self.addresses = {SOURCES[0]: "", SOURCES[1]: "127.0.0.1"}
        self.rates = {SOURCES[0]: "921600", SOURCES[1]: "8765"}
        self.previous_source = self.source.get()
        self.demo_level = tk.DoubleVar(value=0.5)
        self.demo_level_text = tk.StringVar(value="50%")

        self._style()
        self._build()
        self._source_changed()
        self.refresh_ports()
        self.draw()
        if demo:
            self.toggle_connection()
        self.root.after(16, self.tick)

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TCombobox", foreground=INK, fieldbackground=PANEL, background=SOFT,
                        arrowcolor=TEAL, bordercolor=BORDER, padding=(6, 5))
        style.map("TCombobox", fieldbackground=[("readonly", PANEL), ("disabled", BG)],
                  foreground=[("disabled", MUTED)])
        style.configure("Scope.Horizontal.TScale", troughcolor="#DBE9EE", background=TEAL,
                        sliderlength=17, bordercolor=SOFT)

    @staticmethod
    def label(parent, text="", *, variable=None, bg=PANEL, fg=INK, size=10,
              bold=False, mono=False, **kwargs):
        return tk.Label(parent, text=text, textvariable=variable, bg=bg, fg=fg,
                        font=(MONO if mono else UI, size, "bold" if bold else "normal"), **kwargs)

    @staticmethod
    def button(parent, text, command, *, primary=False):
        bg, fg = (TEAL, WHITE) if primary else (SOFT, INK)
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                         activebackground=DARK_TEAL if primary else "#DBEBF0",
                         activeforeground=WHITE if primary else INK,
                         disabledforeground="#8293A5", font=(UI, 9, "bold"),
                         padx=12, pady=6, bd=0, relief="flat", cursor="hand2",
                         highlightthickness=0, takefocus=True)

    @staticmethod
    def card(parent):
        return tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)

    def combo(self, parent, variable, choices, width=10, event=None, editable=False):
        box = ttk.Combobox(parent, textvariable=variable, values=choices, width=width,
                           state="normal" if editable else "readonly", font=(UI, 10))
        if event:
            box.bind("<<ComboboxSelected>>", event)
        return box

    def _build(self):
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=18, pady=(15, 10))
        mark = tk.Canvas(header, width=44, height=44, bg=TEAL, highlightthickness=0)
        mark.create_line(8, 22, 14, 22, 14, 13, 22, 13, 22, 30, 29, 30, 29, 20, 36, 20,
                         fill=WHITE, width=2.5, capstyle="round", joinstyle="round")
        mark.pack(side="left", padx=(0, 11))
        identity = tk.Frame(header, bg=BG)
        identity.pack(side="left")
        self.label(identity, "Portable Oscilloscope", bg=BG, size=21, bold=True).pack(anchor="w")
        self.label(identity, variable=self.device_text, bg=BG, fg=MUTED, size=9).pack(anchor="w")
        self.badge = self.label(header, variable=self.status_text, bg="#DCE8ED", fg=INK,
                                size=10, bold=True, padx=12, pady=6)
        self.badge.pack(side="right", padx=(9, 0))
        self.button(header, "Wiring guide", self.open_guide).pack(side="right")

        tool = self.card(self.root)
        tool.pack(fill="x", padx=18, pady=(0, 10))
        fields = tk.Frame(tool, bg=PANEL)
        fields.pack(side="left", padx=11, pady=8)
        self._field(fields, "Source", 0)
        self.combo(fields, self.source, SOURCES, 16, self._source_changed).grid(row=1, column=0, padx=(0, 9))
        self.address_label = self._field(fields, "COM port", 1)
        self.port_box = self.combo(fields, self.address, (), 13, editable=True)
        self.port_box.grid(row=1, column=1, padx=(0, 9))
        self.rate_label = self._field(fields, "Baud", 2)
        self.rate_box = self.combo(fields, self.baud, ("921600", "115200", "460800"), 9, editable=True)
        self.rate_box.grid(row=1, column=2, padx=(0, 9))
        self.scan_btn = self.button(fields, "Scan", self.refresh_ports)
        self.scan_btn.grid(row=1, column=3, padx=(0, 8))
        self.connect_btn = self.button(fields, "Connect", self.toggle_connection, primary=True)
        self.connect_btn.grid(row=1, column=4)
        actions = tk.Frame(tool, bg=PANEL)
        actions.pack(side="right", padx=11, pady=(20, 6))
        self.save_btn = self.button(actions, "Export CSV", self.export)
        self.save_btn.pack(side="right")
        self.single_btn = self.button(actions, "Single", self.single)
        self.single_btn.pack(side="right", padx=(0, 6))
        self.run_btn = self.button(actions, "Run", self.toggle_run, primary=True)
        self.run_btn.pack(side="right", padx=(0, 6))

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=18)
        sidebar = self.card(body)
        sidebar.pack(side="right", fill="y", padx=(10, 0))
        chart = self.card(body)
        chart.pack(side="left", fill="both", expand=True)
        plothead = tk.Frame(chart, bg=PANEL)
        plothead.pack(fill="x", padx=13, pady=(9, 8))
        self.label(plothead, "Waveform", size=15, bold=True).pack(side="left")
        self.label(plothead, variable=self.plot_hint, fg=MUTED, mono=True, size=10).pack(side="left", padx=13)
        self.capture_label = self.label(plothead, variable=self.capture_tag, bg=SOFT, fg=TEAL,
                                        bold=True, size=9, padx=8, pady=3)
        self.capture_label.pack(side="right")
        self.plot = ScopePlot(chart)
        self.plot.cursor_note = self.cursor_text
        self.plot.pack(fill="both", expand=True, padx=11)
        graph_info = tk.Frame(chart, bg=PANEL)
        graph_info.pack(fill="x", padx=14, pady=(6, 8))
        self.label(graph_info, variable=self.cursor_text, fg=MUTED, size=9).pack(side="left")
        self.button(graph_info, "Clear cursors", self.plot.clear_cursors).pack(side="right")

        stats = tk.Frame(chart, bg=BG)
        stats.pack(fill="x", padx=10, pady=(0, 10))
        for column, (title, key) in enumerate(METRICS):
            stats.columnconfigure(column, weight=1, uniform="measure")
            shade = tk.Frame(stats, bg=SOFT, highlightbackground=BORDER, highlightthickness=1)
            shade.grid(row=0, column=column, sticky="nsew", padx=2)
            self.label(shade, title, bg=SOFT, fg=MUTED, size=8).pack(anchor="w", padx=8, pady=(6, 0))
            value = tk.StringVar(value="-")
            self.stats[key] = value
            item = self.label(shade, variable=value, bg=SOFT, size=11, bold=True, mono=True)
            item.pack(anchor="w", padx=8, pady=(0, 6))

        self._build_sidebar(sidebar)
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=18, pady=7)
        self.label(footer, variable=self.footer_text, bg=BG, fg=MUTED, size=9).pack(side="left")

    def _field(self, frame, title, col):
        widget = self.label(frame, title, fg=MUTED, size=8)
        widget.grid(row=0, column=col, sticky="w", pady=(0, 2))
        return widget

    def _divider(self, parent):
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=12, pady=6)

    def _section(self, parent, title):
        section = tk.Frame(parent, bg=PANEL)
        section.pack(fill="x", padx=12, pady=(2, 2))
        self.label(section, title, size=11, bold=True).pack(anchor="w", pady=(0, 4))
        return section

    def _controls(self, parent):
        inner = tk.Frame(parent, bg=PANEL)
        inner.pack(fill="x", pady=(0, 4))
        return inner

    def _entry(self, parent, variable, width, callback):
        entry = tk.Entry(parent, textvariable=variable, width=width, fg=INK, bg=PANEL,
                         highlightbackground=BORDER, highlightcolor=TEAL,
                         highlightthickness=1, borderwidth=0, font=(UI, 10))
        entry.bind("<Return>", callback)
        entry.bind("<FocusOut>", callback)
        return entry

    def _build_sidebar(self, parent):
        preset = self._section(parent, "Capture setup")
        row = self._controls(preset)
        self.button(row, "Sensors", lambda: self.preset(False)).pack(side="left", padx=(0, 4))
        self.button(row, "PWM 100 Hz", lambda: self.preset(True)).pack(side="left")
        row = self._controls(preset)
        self._field(row, "Time / div", 0)
        self._field(row, "Trace", 1)
        self.combo(row, self.division, [time_text(v) for v in TIMES], 11, self.change).grid(row=1, column=0, padx=(0, 5))
        self.combo(row, self.interpolation, ("Lines", "PWM steps"), 12, self.change).grid(row=1, column=1)

        self._divider(parent)
        channels = self._section(parent, "Channels")
        self.channel_names = []
        for i, color in enumerate(COLORS):
            enabled = tk.BooleanVar(value=False)
            self.channel_checks.append(enabled)
            row = tk.Frame(channels, bg=PANEL)
            row.pack(fill="x")
            check = tk.Checkbutton(row, text=f"CH{i + 1}", variable=enabled, fg=color, bg=PANEL,
                                   disabledforeground=MUTED, activebackground=PANEL, activeforeground=color,
                                   selectcolor=PANEL, highlightthickness=0, state="disabled",
                                   padx=0, pady=0, font=(MONO, 9, "bold"),
                                   command=lambda i=i: self.toggle_channel(i))
            check.pack(side="left")
            name = self.label(row, "-", fg=MUTED, size=9, width=21, anchor="w")
            name.pack(side="left", padx=(5, 0))
            self.channel_names.append((check, name))
        row = self._controls(channels)
        self._field(row, "Measure", 0)
        self._field(row, "V / div", 1)
        self._field(row, "Center", 2)
        self.active_box = self.combo(row, self.active, ("CH1",), 5, self.select_channel)
        self.active_box.grid(row=1, column=0, padx=(0, 4))
        self.combo(row, self.vscale, tuple(str(v) for v in VOLTAGES), 5, self.apply_vertical).grid(
            row=1, column=1, padx=(0, 4))
        self._entry(row, self.center, 6, self.apply_vertical).grid(row=1, column=2, ipady=4)
        row = self._controls(channels)
        self.button(row, "Fit channel", self.fit).pack(side="left", padx=(0, 4))
        self.button(row, "Calibrate", self.calibrate).pack(side="left")

        self._divider(parent)
        trigger = self._section(parent, "Trigger")
        row = self._controls(trigger)
        self._field(row, "Mode", 0)
        self._field(row, "Source", 1)
        self._field(row, "Edge", 2)
        self.combo(row, self.mode, ("Roll", "Auto", "Normal"), 7, self.change).grid(row=1, column=0, padx=(0, 4))
        self.trigger_box = self.combo(row, self.trigger, ("CH1",), 5, self.change)
        self.trigger_box.grid(row=1, column=1, padx=(0, 4))
        self.combo(row, self.slope, ("Rising", "Falling"), 7, self.change).grid(row=1, column=2)
        row = self._controls(trigger)
        self.label(row, "Level (V)", fg=MUTED, size=9).pack(side="left", padx=(0, 6))
        self._entry(row, self.level, 7, self.change).pack(side="left", ipady=4)

        self.demo_card = tk.Frame(parent, bg=SOFT, highlightthickness=1, highlightbackground=BORDER)
        self.demo_title = self.label(self.demo_card, "Simulated inputs", bg=SOFT, fg=TEAL, bold=True, size=9)
        self.demo_title.pack(anchor="w", padx=8, pady=(7, 1))
        demo_row = tk.Frame(self.demo_card, bg=SOFT)
        demo_row.pack(fill="x", padx=8, pady=(0, 5))
        self.demo_slider_label = self.label(demo_row, "Light", bg=SOFT, fg=MUTED, size=9)
        self.demo_slider_label.pack(side="left")
        ttk.Scale(demo_row, from_=0, to=1, variable=self.demo_level, command=self.demo_adjust,
                  style="Scope.Horizontal.TScale", length=97).pack(side="left", padx=5)
        self.label(demo_row, variable=self.demo_level_text, bg=SOFT, size=9, width=5).pack(side="left")
        self.demo_buttons = tk.Frame(self.demo_card, bg=SOFT)
        self.demo_buttons.pack(fill="x", padx=7, pady=(0, 4))
        self.button(self.demo_buttons, "Clap", self.demo_clap).pack(side="left", padx=(0, 4))
        self.demo_ir_btn = self.button(self.demo_buttons, "Object away", self.demo_ir)
        self.demo_ir_btn.pack(side="left")

    @property
    def active_index(self):
        return int(self.active.get()[-1]) - 1

    @property
    def seconds_per_div(self):
        return next(value for value in TIMES if time_text(value) == self.division.get())

    @property
    def trigger_level(self):
        return self._float(self.level, 1.65)

    @staticmethod
    def _float(variable, fallback):
        try:
            result = float(variable.get())
            return result if math.isfinite(result) and abs(result) <= 1e12 else fallback
        except (ValueError, tk.TclError):
            return fallback

    def _source_changed(self, _event=None):
        source = self.source.get()
        prior = self.previous_source
        if prior in self.addresses and self.address.get():
            self.addresses[prior] = self.address.get()
            self.rates[prior] = self.baud.get()
        if self.reader or self.simulated:
            self.disconnect()
        self.previous_source = source
        is_usb = source == SOURCES[0]
        is_demo = source.startswith("Demo:")
        self.address.set(self.addresses.get(source, ""))
        self.baud.set(self.rates.get(source, ""))
        self.address_label.configure(text="COM port" if is_usb else "Host")
        self.rate_label.configure(text="Baud" if is_usb else "TCP port")
        fields = (self.address_label, self.port_box, self.rate_label, self.rate_box, self.scan_btn)
        for field in fields:
            if is_demo:
                field.grid_remove()
            else:
                field.grid()
        self.port_box.configure(state="disabled" if is_demo else "normal")
        self.rate_box.configure(state="disabled" if is_demo else "normal",
                                values=("921600", "115200", "460800") if is_usb else ())
        self.scan_btn.configure(state="normal" if is_usb else "disabled")
        self.connect_btn.configure(text="Start demo" if is_demo else "Connect")
        if is_usb:
            self.refresh_ports()
        else:
            self.port_box.configure(values=())
        self.dirty = True

    def refresh_ports(self):
        if self.source.get() != SOURCES[0]:
            return
        try:
            ports = serial_ports()
        except OSError:
            ports = []
        self.port_box.configure(values=ports)
        if ports and not self.address.get():
            self.address.set(ports[0])

    def disconnect(self, reason=None):
        if self.reader:
            self.reader.close()
        self.reader = self.simulated = None
        self.device = self.connected = self.last_sample = None
        self.running = False
        self.single_since = None
        self.frame = None
        self.buffer = CaptureBuffer()
        self.plot.clear_cursors()
        self.demo_card.pack_forget()
        self.invalid = self.dropped = 0
        self.device_text.set("No device connected")
        self.footer_text.set(reason or "Choose a USB port or try a Demo source.")
        self.connect_btn.configure(text="Start demo" if self.source.get().startswith("Demo:") else "Connect")
        self._channel_controls()
        self.dirty = True

    def toggle_connection(self):
        if self.reader or self.simulated:
            self.disconnect()
            return
        source = self.source.get()
        if source.startswith("Demo:"):
            pwm = source == SOURCES[3]
            self.simulated = Demo(pwm)
            self.demo_level.set(0.5)
            self.demo_adjust(0.5)
            self.demo_slider_label.configure(text="Duty" if pwm else "Light")
            self.demo_ir_btn.configure(text="Object away")
            if pwm:
                self.demo_buttons.pack_forget()
            else:
                self.demo_buttons.pack(fill="x", padx=7, pady=(0, 4))
            self.demo_card.pack(fill="x", padx=12, pady=(1, 10))
            self.accept_device(self.simulated.device)
            self.connect_btn.configure(text="End demo")
            return
        address = self.address.get().strip()
        try:
            rate = int(self.baud.get())
            if not address or not 1 <= rate <= (65535 if source == SOURCES[1] else 5_000_000):
                raise ValueError
        except ValueError:
            self.footer_text.set("Enter the device address and a valid baud rate or TCP port.")
            return
        self.reader = Reader(source, address, rate)
        self.reader.start()
        self.connect_btn.configure(text="Disconnect")
        self.footer_text.set(f"Opening {address}…")
        self.dirty = True

    def accept_device(self, device):
        if self.device == device:
            return
        self.buffer = CaptureBuffer()
        self.frame = None
        self.plot.clear_cursors()
        self.device = device
        self.channels = [Channel() for _ in range(4)]
        self.running = True
        self.single_since = None
        self.last_sample = None
        self.device_text.set(device.name)
        self.active.set("CH1")
        self.trigger.set("CH1")
        self._channel_controls()
        self.preset("pwm" in device.name.lower())
        self.footer_text.set("Waiting for sample packets.")

    def _channel_controls(self):
        count = len(self.device.channels) if self.device else 0
        choices = tuple(f"CH{i + 1}" for i in range(max(1, count)))
        self.active_box.configure(values=choices)
        self.trigger_box.configure(values=choices)
        if not count:
            self.active.set("CH1")
            self.trigger.set("CH1")
        for i, (check, name) in enumerate(self.channel_names):
            check.configure(state="normal" if i < count else "disabled")
            self.channel_checks[i].set(i < count and self.channels[i].visible)
            if i < count:
                label = self.device.channels[i][0]
                name.configure(text=label[:23] + ("…" if len(label) > 23 else ""))
            else:
                name.configure(text="-")
        self.select_channel()

    def preset(self, pwm):
        self.division.set(time_text(0.005 if pwm else 0.5))
        self.interpolation.set("PWM steps" if pwm else "Lines")
        self.mode.set("Auto" if pwm else "Roll")
        self.slope.set("Rising")
        self.level.set("1.65")
        for channel in self.channels:
            channel.scale = 2.0 if pwm else 0.5
            channel.center = 1.65
        self.select_channel()
        self.change()

    def toggle_channel(self, index):
        self.channels[index].visible = self.channel_checks[index].get()
        self.dirty = True

    def select_channel(self, _event=None):
        config = self.channels[self.active_index]
        self.vscale.set(f"{config.scale:g}")
        self.center.set(f"{config.center:.6g}")
        self.dirty = True
        self.metrics_at = 0.0

    def change(self, _event=None):
        self.level.set(f"{self.trigger_level:g}")
        self.changed = True
        self.dirty = True

    def apply_vertical(self, _event=None):
        config = self.channels[self.active_index]
        config.scale = max(0.001, self._float(self.vscale, config.scale))
        config.center = self._float(self.center, config.center)
        self.select_channel()

    def fit(self):
        if not self.frame:
            return
        config = self.channels[self.active_index]
        volts = [config.adjusted(row[self.active_index]) for row in self.frame.values]
        lo, hi = min(volts), max(volts)
        config.center = (hi + lo) / 2
        needed = max((hi - lo) / 6, 0.05)
        config.scale = next((v for v in VOLTAGES if v >= needed), VOLTAGES[-1])
        self.select_channel()

    def calibrate(self):
        if self.device is None:
            return
        channel = self.channels[self.active_index]
        dialog = tk.Toplevel(self.root)
        dialog.title(f"{self.active.get()} voltage calibration")
        dialog.configure(bg=PANEL)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        body = tk.Frame(dialog, bg=PANEL)
        body.pack(padx=16, pady=14)
        self.label(body, "Two-point calibration", size=14, bold=True).grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.label(body, "Enter two raw readings and their independently measured references.",
                   fg=MUTED, wraplength=370, justify="left").grid(row=1, column=0, columnspan=2, pady=(0, 10))
        values = [tk.StringVar() for _ in range(4)]
        for row, (name, value) in enumerate(zip(
                ("Raw low (V)", "Reference low (V)", "Raw high (V)", "Reference high (V)"), values), 2):
            self.label(body, name).grid(row=row, column=0, sticky="w", pady=3)
            self._entry(body, value, 13, lambda event: None).grid(row=row, column=1, padx=(19, 0), ipady=4)
        self.label(body, f"Active gain ×{channel.gain:.5g}   Offset {channel.offset:.5g} V",
                   fg=MUTED, size=9).grid(row=6, column=0, columnspan=2, sticky="w", pady=6)

        def apply():
            raw_lo, ref_lo, raw_hi, ref_hi = (self._float(v, math.nan) for v in values)
            if not raw_hi > raw_lo or not ref_hi > ref_lo:
                messagebox.showerror("Check readings", "Enter two increasing pairs of measured values.", parent=dialog)
                return
            gain = (ref_hi - ref_lo) / (raw_hi - raw_lo)
            offset = ref_lo - raw_lo * gain
            if not 0.2 <= gain <= 5 or not math.isfinite(offset):
                messagebox.showerror("Check wiring and units", "This correction is outside the ×0.2 to ×5 range.",
                                     parent=dialog)
                return
            channel.gain, channel.offset = gain, offset
            self.metrics_at = 0
            self.change()
            dialog.destroy()

        def reset():
            channel.gain, channel.offset = 1.0, 0.0
            self.metrics_at = 0
            self.change()
            dialog.destroy()

        controls = tk.Frame(body, bg=PANEL)
        controls.grid(row=7, column=0, columnspan=2, sticky="e")
        self.button(controls, "Reset ×1", reset).pack(side="left", padx=4)
        self.button(controls, "Apply", apply, primary=True).pack(side="left")

    def demo_adjust(self, level):
        value = float(level)
        self.demo_level_text.set(f"{value:.0%}")
        if self.simulated:
            if self.simulated.pwm:
                self.simulated.duty = value
            else:
                self.simulated.light = value

    def demo_clap(self):
        if self.simulated:
            self.simulated.clap()

    def demo_ir(self):
        if self.simulated:
            self.simulated.object_near = not self.simulated.object_near
            self.demo_ir_btn.configure(text="Object near" if self.simulated.object_near else "Object away")

    def toggle_run(self):
        if not self.device:
            return
        self.single_since = None
        self.running = not self.running
        if self.running:
            self.changed = True
        self.dirty = True

    def single(self):
        if not self.device:
            return
        self.single_since = self.buffer.latest
        if self.single_since is None:
            self.single_since = -math.inf
        self.single_time = monotonic()
        self.running = True
        self.dirty = True

    def _append(self, packet, now):
        if not self.device:
            return
        previous = self.buffer.latest
        self.buffer.append(packet)
        if (self.single_since is not None and previous is not None and packet.times[0] <= previous):
            self.single_since = -math.inf
        self.last_sample = now
        self.changed = True

    def _advance_capture(self, now):
        if not self.running or not self.changed:
            return
        self.changed = False
        ch = int(self.trigger.get()[-1]) - 1
        channel = self.channels[ch]
        span = self.seconds_per_div * 10
        rolling = self.mode.get() == "Roll"
        auto = self.mode.get() == "Auto"
        level = (self.trigger_level - channel.offset) / channel.gain
        after = self.single_since
        frame = self.buffer.capture(span, ch, level, self.slope.get() == "Rising",
                                    rolling or (auto and (after is None or now - self.single_time >= max(0.8, span))),
                                    after, rolling=rolling)
        if frame and rolling and after is not None and frame.times[-1] < after + span:
            return
        if frame:
            self.frame = frame
            if after is not None:
                self.single_since = None
                self.running = False
            self.dirty = True

    def _badge(self, label, fg, bg):
        if self.status_text.get() != label:
            self.status_text.set(label)
            self.badge.configure(fg=fg, bg=bg)

    def tick(self):
        now = monotonic()
        if self.reader:
            events, self.invalid, self.dropped = self.reader.take()
            for event, item in events:
                if event == "opened":
                    self.connected = now
                elif event == "device":
                    self.accept_device(item)
                elif event == "samples":
                    self._append(item, now)
                elif event == "error":
                    self.disconnect(item)
                    break
        if self.simulated:
            packet = self.simulated.next()
            if packet:
                self._append(packet, now)
        self._advance_capture(now)
        if self.dirty:
            self.draw()
        if now - self.metrics_at >= 0.15:
            self.update_readouts(now)
        self.root.after(16, self.tick)

    def draw(self):
        self.dirty = False
        active = self.active_index
        index = int(self.trigger.get()[-1]) - 1
        if self.frame:
            span = self.frame.span
            detail = "Edge trigger" if self.frame.trigger is not None else "Rolling display"
        else:
            span = self.seconds_per_div * 10
            detail = "Waiting for trigger" if self.device and self.mode.get() == "Normal" else "No data"
        unit = self.device.channels[active][1] if self.device else "V"
        config = self.channels[active]
        self.plot_hint.set(f"{time_text(span / 10)}/div  ·  {self.active.get()} {config.scale:g} {unit}/div  ·  {detail}")
        self.plot.update_trace(
            self.frame, [ch.display_arguments() for ch in self.channels], active, span,
            self.interpolation.get() == "PWM steps", self.trigger_level if active == index and self.mode.get() != "Roll" else None,
            "Select an input and connect to begin." if not self.device else "Waiting for the next complete capture.")
        self.run_btn.configure(text="Hold" if self.running else "Run",
                               state="normal" if self.device else "disabled")
        self.single_btn.configure(state="normal" if self.device else "disabled")
        self.save_btn.configure(state="normal" if self.frame else "disabled")
        tag = "SINGLE ARMED" if self.single_since is not None else "RUNNING" if self.running else "HOLD" if self.frame else "IDLE"
        self.capture_tag.set(tag)

    def update_readouts(self, now):
        self.metrics_at = now
        if self.simulated:
            self._badge("SIMULATED DEMO", "#62420A", "#FFF0CD")
        elif self.reader and not self.device:
            self._badge("Waiting for device", "#62420A", "#FFF0CD")
        elif self.device and self.last_sample is not None and now - self.last_sample < 2:
            self._badge("Live hardware", DARK_TEAL, "#D2F2E8")
        elif self.device:
            self._badge("Waiting for samples", "#62420A", "#FFF0CD")
        else:
            self._badge("Offline", INK, "#DCE8ED")
        if self.reader and not self.device and self.connected is not None:
            detail = "Port open. Waiting for the ESP32 identification packet."
            if now - self.connected > 3:
                detail = "Check baud, close Serial Monitor, then press ESP32 EN once after checking wiring."
            self.footer_text.set(f"{detail}   Invalid packets {self.invalid}")
        if self.device and (self.reason_no_samples(now)):
            self.footer_text.set("Device identified. Waiting for new samples. Check ADC wiring and firmware.")
        frame = self.frame
        if not frame or not self.device:
            for var in self.stats.values():
                var.set("-")
            return
        idx = self.active_index
        unit = self.device.channels[idx][1]
        config = self.channels[idx]
        values = [config.adjusted(row[idx]) for row in frame.values]
        result = measure(frame.times, values)
        result["last"] = values[-1]
        units = {"frequency": "Hz", "duty": "%"}
        for key, var in self.stats.items():
            reading = result.get(key)
            var.set("-" if reading is None else f"{reading:.4g} {units.get(key, unit)}")
        if self.last_sample is not None and now - self.last_sample < 2:
            rate = sample_rate(frame.times)
            rate_text = f"{rate:.0f} Sa/s per channel" if rate else "- Sa/s"
            source = "Simulated input" if self.simulated else "Measured input"
            self.footer_text.set(f"{source}   |   {rate_text}   |   {len(frame.times)} samples in view"
                                 f"   |   Gaps {self.buffer.gaps}   |   Invalid {self.invalid}   |   Dropped {self.dropped}")

    def reason_no_samples(self, now):
        if self.last_sample is not None:
            return now - self.last_sample >= 2
        return self.connected is not None and now - self.connected >= 2

    def export(self):
        if not self.frame or not self.device:
            return
        frame = self.frame
        channels = self.device.channels
        corrections = tuple((channel.gain, channel.offset) for channel in self.channels)
        source = "simulated" if self.simulated else "measured"
        path = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=".csv", filetypes=[("CSV file", "*.csv")],
            initialfile=f"{source}_capture.csv")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as output:
                writer = csv.writer(output)
                writer.writerow(["source", "time_s"] + [f"CH{i + 1} ({unit})" for i, (_, unit)
                                                        in enumerate(channels)])
                origin = frame.start + frame.span if frame.trigger is None else frame.trigger
                for time, row in zip(frame.times, frame.values):
                    values = [f"{value * gain + offset:.9g}"
                              for value, (gain, offset) in zip(row, corrections)]
                    writer.writerow([source, f"{time - origin:.9f}", *values])
        except OSError as exc:
            messagebox.showerror("Export CSV", str(exc), parent=self.root)

    def open_guide(self):
        webbrowser.open((Path(__file__).parent / "docs" / "Wiring_and_Setup.html").resolve().as_uri())

    def close(self):
        if self.reader:
            self.reader.close()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="Portable Oscilloscope")
    parser.add_argument("--demo", action="store_true", help="open simulated sensor inputs")
    args = parser.parse_args()
    root = tk.Tk()
    ScopeApp(root, demo=args.demo)
    root.mainloop()


if __name__ == "__main__":
    main()
