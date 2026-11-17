"""Light oscilloscope graticule and calibrated display traces."""
import tkinter as tk
from tkinter import font as tk_font

from scope_data import pixel_envelope, time_text

INK = "#172B40"
MUTED = "#51657B"
BORDER = "#D6E3EA"
TEAL = "#006C68"
PANEL = "#FFFFFF"
PLOT = "#F9FCFD"
COLORS = ("#007E78", "#4663C6", "#AA5D0B", "#AA3975")
MONO = "Consolas"
UI = "Segoe UI"


def select_fonts(root):
    global UI, MONO
    families = {name.casefold(): name for name in tk_font.families(root)}
    ui_names = ("Segoe UI", "Inter", "DejaVu Sans", "Helvetica")
    mono_names = ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Nimbus Mono L", "Courier")
    UI = next((families[name.casefold()] for name in ui_names if name.casefold() in families), "TkDefaultFont")
    MONO = next((families[name.casefold()] for name in mono_names if name.casefold() in families), "TkFixedFont")
    return UI, MONO


class ScopePlot(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, bg=PLOT, highlightthickness=0, cursor="crosshair")
        self.cursors = []
        self.frame = None
        self.traces = []
        self.active = 0
        self.span = 5.0
        self.show_steps = False
        self.level = None
        self.caption = ""
        self.cursor_note = None
        self._bounds = (0, 0, 1, 1)
        self.bind("<Configure>", self._configure)
        self.bind("<Button-1>", self._cursor)
        self.bind("<Button-3>", lambda event: self.clear_cursors())

    def _configure(self, _event):
        self.delete("grid")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 140 or h < 100:
            return
        self._bounds = 66, 21, w - 23, h - 40
        x0, y0, x1, y1 = self._bounds
        for i in range(11):
            x = x0 + (x1 - x0) * i / 10
            self.create_line(x, y0, x, y1, fill="#D7E5EC", tags="grid")
        for i in range(9):
            y = y0 + (y1 - y0) * i / 8
            self.create_line(x0, y, x1, y, fill="#B8CBD7" if i == 4 else "#D7E5EC",
                             dash=(4, 3) if i == 4 else (), tags="grid")
        self.create_rectangle(x0, y0, x1, y1, outline="#AFC5D1", tags="grid")
        self.render()

    def update_trace(self, frame, traces, active, span, steps, level, caption):
        self.frame, self.traces, self.active = frame, traces, active
        self.span, self.show_steps, self.level, self.caption = span, steps, level, caption
        self.render()

    def _cursor(self, event):
        x0, y0, x1, y1 = self._bounds
        if self.frame is None or not (x0 <= event.x <= x1 and y0 <= event.y <= y1):
            return
        if len(self.cursors) == 2:
            self.cursors.clear()
        self.cursors.append((event.x - x0) / (x1 - x0))
        self.render()

    def clear_cursors(self):
        self.cursors.clear()
        self.render()

    def render(self):
        self.delete("trace")
        x0, y0, x1, y1 = self._bounds
        if x1 <= 1:
            return
        height, width = y1 - y0, x1 - x0
        frame = self.frame
        channel = self.traces[self.active] if self.traces else (False, 1.0, 1.65, 1.0, 0.0)

        def ycoord(volts, scale, center):
            return max(y0 - 30000, min(y1 + 30000, y0 + height * (0.5 - (volts - center) / (8 * scale))))

        if frame:
            for index, (visible, scale, center, gain, offset) in enumerate(self.traces):
                if not visible or index >= len(frame.values[0]):
                    continue
                values = [row[index] * gain + offset for row in frame.values]
                points = pixel_envelope(frame.times, values, frame.start, frame.span, width)
                coords = []
                for time, value in points:
                    x = x0 + (time - frame.start) / frame.span * width
                    y = ycoord(value, scale, center)
                    if self.show_steps and coords:
                        middle = (x + coords[-2]) / 2
                        coords.extend((middle, coords[-1], middle, y))
                    coords.extend((x, y))
                if len(coords) >= 4:
                    self.create_line(coords, fill=COLORS[index], width=2.0, tags="trace")
        # Cover the portions of a trace outside the eight-division display.
        w, h = self.winfo_width(), self.winfo_height()
        for bounds in ((0, 0, w, y0 - 1), (0, y1 + 1, w, h),
                       (0, y0, x0 - 1, y1), (x1 + 1, y0, w, y1)):
            self.create_rectangle(*bounds, fill=PLOT, outline="", tags="trace")
        for i in range(0, 9, 2):
            _, scale, center, _, _ = channel
            value = center + (4 - i) * scale
            y = y0 + height * i / 8
            self.create_text(x0 - 9, y, text=f"{value:.3g}", fill=MUTED,
                             anchor="e", font=(MONO, 9), tags="trace")
        # Triggered and rolling captures have different time origins.
        for i in range(0, 11, 2):
            offset = i / 10 - (0.2 if frame and frame.trigger is not None else 1.0 if frame else 0.0)
            x = x0 + width * i / 10
            self.create_text(x, y1 + 18, text=time_text(offset * self.span), fill=MUTED,
                             font=(MONO, 9), tags="trace")

        if frame and frame.trigger is not None:
            x = x0 + 0.2 * width
            self.create_line(x, y0, x, y1, fill="#4C948B", dash=(4, 4), tags="trace")
            self.create_polygon(x - 6, y0, x + 6, y0, x, y0 + 8,
                                fill=TEAL, outline="", tags="trace")
        if frame and self.level is not None:
            y = ycoord(self.level, channel[1], channel[2])
            if y0 <= y <= y1:
                self.create_polygon(x0, y, x0 - 7, y - 4, x0 - 7, y + 4,
                                    fill=COLORS[self.active], outline="", tags="trace")
        if frame is None:
            mid = (x0 + x1) / 2, (y0 + y1) / 2
            badge_w = min(218, width * 0.38)
            self.create_rectangle(mid[0] - badge_w, mid[1] - 49, mid[0] + badge_w,
                                  mid[1] + 49, fill=PANEL, outline=BORDER, tags="trace")
            self.create_text(mid[0], mid[1] - 14, text="No capture", fill=INK,
                             font=(UI, 16, "bold"), tags="trace")
            self.create_text(mid[0], mid[1] + 17, text=self.caption, fill=MUTED,
                             font=(UI, 10), width=int(badge_w * 1.8), tags="trace")
        for index, fraction in enumerate(self.cursors if frame else []):
            x = x0 + fraction * width
            self.create_line(x, y0, x, y1, dash=(3, 4), fill="#824E91", tags="trace")
            self.create_text(x, y0 + 13, text="AB"[index], fill="#824E91",
                             font=(MONO, 10, "bold"), tags="trace")
        if self.cursor_note is not None:
            text = "Click the plot to place A/B cursors"
            if frame and len(self.cursors) == 2:
                text = f"Δt  {time_text(abs(self.cursors[1] - self.cursors[0]) * frame.span)}"
            self.cursor_note.set(text)
