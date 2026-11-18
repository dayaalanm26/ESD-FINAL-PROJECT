"""!
@file Generic_DSO_Single_File.py
@brief Four-channel Generic Digital Storage Oscilloscope (DSO) for real hardware.
@details Provides a PySide6 + pyqtgraph desktop oscilloscope interface supporting
         Serial and TCP JSON data acquisition, real-time edge/roll triggering,
         acquisition averaging/Hi-Res modes, two-point calibration, automated
         measurements, and CSV/PNG export.
@note Dependencies: numpy, PySide6, pyqtgraph, and pyserial.
"""

from dataclasses import dataclass
import json
import math
from collections import deque
import queue
import socket
import threading
import time
import csv
from pathlib import Path
import sys
import traceback

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QPointF
from PySide6.QtGui import QColor, QFont, QPainter, QPicture, QFontMetrics
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit,
    QGroupBox, QCheckBox, QFrame, QFileDialog, QMessageBox, QDialog, QDialogButtonBox,
    QFormLayout, QScrollArea, QSlider, QStackedWidget, QSizePolicy, QButtonGroup)
import pyqtgraph as pg

# Serial protocol, capture engine, and measurements

## Maximum number of sample points retained in the capture ring buffer.
MAX_POINTS = 65536
## Maximum allowed incoming JSON message size in bytes (2 MB).
MAX_LINE = 2_000_000


class ProtocolError(ValueError):
    """!
    @brief Exception raised when incoming hardware serial/TCP JSON messages violate protocol v1.
    """
    pass


@dataclass
class Metadata:
    """!
    @brief Device identification and channel specifications parsed from a 'hello' packet.
    """
    device: str      ##< Human-readable hardware device name.
    channels: list   ##< List of channel specification dictionaries (name, unit, min, max, etc.).


@dataclass(frozen=True)
class DeviceStatus:
    """!
    @brief Immutable status notification emitted by the connected acquisition device.
    """
    code: str        ##< Short status identifier string (e.g., 'ready', 'ads1115_missing').


@dataclass
class Frame:
    """!
    @brief Decoded block of multi-channel time-series samples from a single 'samples' packet.
    """
    t: np.ndarray          ##< 1D array of sample timestamps in seconds.
    y: np.ndarray          ##< 2D array of sample values with shape (N, num_channels).
    seq: int               ##< Monotonically increasing packet sequence number.
    gap: bool = False      ##< True if a timing or sequence discontinuity precedes this frame.
    clipped: bool = False  ##< True if any sample reached hardware ADC limits.


@dataclass
class Capture:
    """!
    @brief Triggered or auto-rolled waveform snapshot ready for processing and display.
    """
    t: np.ndarray       ##< Trigger-relative timestamps in seconds.
    y: np.ndarray       ##< Calibrated 2D sample array of shape (N, num_channels).
    origin: float       ##< Absolute device timestamp (s) corresponding to t = 0.
    triggered: bool     ##< True if captured via a valid edge trigger crossing.
    clipped: bool       ##< True if any sample within the capture window clipped.
    roll: bool = False  ##< True if captured in continuous Roll mode.


def number(x):
    """!
    @brief Validates and converts a JSON numeric value to a finite Python float.
    @param x Input value to validate (rejects booleans, NaNs, and infinities).
    @return Finite float value.
    @throws ProtocolError If @p x is not a finite int or float.
    """
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ProtocolError('Expected a finite numeric value')
    return float(x)


class Decoder:
    """!
    @brief Stateful JSON-line protocol v1 parser and continuity validator.
    @details Validates 'hello', 'status', and 'samples' packets, enforces monotonic
             sequence numbers and timestamps, and detects sample dropouts/gaps.
    """

    def __init__(self):
        """!
        @brief Initializes an empty Decoder state.
        """
        self.metadata = None
        self.seq = None
        self.last_t = None
        self.last_dt = None
        self.force_gap = False

    def decode(self, line):
        """!
        @brief Decodes a single newline-delimited JSON payload into a protocol object.
        @param line Raw bytes or UTF-8 string containing one JSON object.
        @return Metadata, DeviceStatus, or Frame instance.
        @throws ProtocolError If JSON syntax or schema validation fails.
        """
        try:
            return self._decode(line)
        except (ValueError, TypeError, KeyError, OverflowError, IndexError) as exc:
            self.force_gap = True
            if isinstance(exc, ProtocolError):
                raise
            raise ProtocolError(str(exc)) from exc

    def _decode(self, line):
        """!
        @brief Internal parser implementation for JSON protocol messages.
        @param line Raw line buffer to parse.
        @return Parsed Metadata, DeviceStatus, or Frame object.
        """
        if len(line) > MAX_LINE:
            raise ProtocolError('Frame exceeds 2 MB limit')
        obj = json.loads(line)
        if not isinstance(obj, dict):
            raise ProtocolError('Expected JSON object')
        if obj.get('type') == 'hello':
            if obj.get('protocol') != 1:
                raise ProtocolError('Expected protocol version 1')
            channels = obj['channels']
            if not isinstance(channels, list) or not 1 <= len(channels) <= 4:
                raise ProtocolError('Device must define 1–4 channels')
            clean = []
            for ch in channels:
                if not isinstance(ch, dict):
                    raise ProtocolError('Invalid channel metadata')
                name, unit = ch.get('name'), ch.get('unit')
                if not isinstance(name, str) or not name or len(name) > 40:
                    raise ProtocolError('Channel name is required, maximum 40 characters')
                if not isinstance(unit, str) or len(unit) > 16:
                    raise ProtocolError('Channel unit is required, maximum 16 characters')
                low = number(ch['min']) if 'min' in ch else None
                high = number(ch['max']) if 'max' in ch else None
                if (low is None) != (high is None) or (low is not None and low >= high):
                    raise ProtocolError('Channel range requires min < max')
                nullable = ch.get('nullable', False)
                if type(nullable) is not bool:
                    raise ProtocolError('Channel nullable must be boolean')
                spec = dict(name=name, unit=unit, min=low, max=high)
                if nullable:
                    spec['nullable'] = True
                if 'active_low' in ch:
                    if unit != 'logic' or type(ch['active_low']) is not bool:
                        raise ProtocolError('Channel active_low requires a logic channel and a boolean value')
                    spec['active_low'] = ch['active_low']
                clean.append(spec)
            device = obj.get('device', 'Generic device')
            if not isinstance(device, str) or len(device) > 80:
                raise ProtocolError('Invalid device name')
            meta = Metadata(device, clean)
            # Repeated identical metadata is a keepalive, not a new sequence epoch.
            if meta != self.metadata:
                self.seq = self.last_t = self.last_dt = None
            self.metadata = meta
            return meta
        if obj.get('type') == 'status':
            code = obj.get('code')
            if not isinstance(code, str) or not code or len(code) > 40:
                raise ProtocolError('Invalid device status code')
            return DeviceStatus(code)
        if obj.get('type') != 'samples':
            raise ProtocolError('Unknown message type')
        if self.metadata is None:
            raise ProtocolError('Waiting for device hello metadata')
        seq = obj['seq']
        if type(seq) is not int or seq < 0:
            raise ProtocolError('seq must be a nonnegative integer')
        discontinuity = obj.get('discontinuity', False)
        if type(discontinuity) is not bool:
            raise ProtocolError('discontinuity must be boolean')
        restarting = seq == 0 and discontinuity
        last_seq = None if restarting else self.seq
        last_time = None if restarting else self.last_t
        last_dt = None if restarting else self.last_dt
        if last_seq is not None and seq <= last_seq:
            raise ProtocolError('Duplicate/out-of-order seq; reconnect after device restart')
        values = obj['values']
        if not isinstance(values, list) or not 1 <= len(values) <= 4096:
            raise ProtocolError('Frames require 1–4096 sample rows')
        width = len(self.metadata.channels)
        for row in values:
            if not isinstance(row, list) or len(row) != width:
                raise ProtocolError('Sample row does not match channel count')
            for v, channel in zip(row, self.metadata.channels):
                if v is None and channel.get('nullable'):
                    continue
                number(v)
        y = np.asarray(values, dtype=float)
        if 'times' in obj:
            if 'fs' in obj or 't0' in obj:
                raise ProtocolError('Use times OR t0/fs')
            times = obj['times']
            if not isinstance(times, list) or len(times) != len(y):
                raise ProtocolError('Timing length mismatch')
            t = np.asarray([number(v) for v in times])
            dt = None
        else:
            fs, t0 = number(obj['fs']), number(obj['t0'])
            if not 0 < fs <= 1e10:
                raise ProtocolError('Invalid sample rate')
            dt = 1 / fs
            t = t0 + np.arange(len(y)) * dt
        if t[0] < 0 or not np.all(np.isfinite(t)) or np.any(np.diff(t) <= 0):
            raise ProtocolError('Sample times must increase and be nonnegative')
        if last_time is not None and t[0] <= last_time:
            raise ProtocolError('Sample times overlap or run backwards')
        gap = discontinuity or self.force_gap or (last_seq is not None and seq != last_seq + 1)
        if last_time is not None and dt is not None and last_dt is not None:
            gap |= not math.isclose(t[0] - last_time, dt, rel_tol=.05, abs_tol=1e-9)
            gap |= not math.isclose(dt, last_dt, rel_tol=.001)
        if 'times' in obj:
            steps = np.diff(t)
            cadence = float(np.median(steps)) if len(steps) else last_dt
            if last_dt is not None and last_time is not None:
                gap |= t[0] - last_time > 10 * last_dt + 1e-9
            if cadence is not None:
                breaks = np.flatnonzero(steps > 10 * cadence + 1e-9)
                if len(breaks):
                    cut = int(breaks[-1]) + 1
                    t, y = t[cut:], y[cut:]
                    gap = True
                    cadence = float(np.median(np.diff(t))) if len(t) > 1 else cadence
            if cadence is None and last_time is not None:
                cadence = float(t[0] - last_time)
            dt = cadence
        clipped = obj.get('clipped', False)
        if type(clipped) is not bool:
            raise ProtocolError('clipped must be boolean')
        for i, ch in enumerate(self.metadata.channels):
            if ch['min'] is not None:
                clipped |= bool(np.any((y[:, i] <= ch['min']) | (y[:, i] >= ch['max'])))
        self.last_t, self.last_dt, self.seq = float(t[-1]), dt, seq
        self.force_gap = False
        return Frame(t, y, seq, bool(gap), clipped)


class CaptureEngine:
    """!
    @brief Rolling sample buffer and sub-sample interpolated trigger engine.
    """

    def __init__(self):
        """!
        @brief Initializes the capture buffer arrays.
        """
        self.clear()

    def clear(self):
        """!
        @brief Flushes all buffered sample history and resets trigger timestamps.
        """
        self.t = np.empty(0)
        self.y = None
        self.clipping = np.empty(0, dtype=bool)
        self.last_trigger = -math.inf
        self.last_auto = -math.inf

    def append(self, frame, keep_seconds=None):
        """!
        @brief Appends a new @ref Frame to the circular history buffer.
        @param frame Decoded @ref Frame containing calibrated sample values.
        @param keep_seconds Optional retention window duration in seconds (used in Roll mode).
        """
        if frame.gap:
            self.clear()
        old_start = new_start = 0
        if keep_seconds is not None:
            left = frame.t[-1] - keep_seconds
            old_start = int(np.searchsorted(self.t, left))
            new_start = int(np.searchsorted(frame.t, left))
        self.t = np.concatenate((self.t[old_start:], frame.t[new_start:]))[-MAX_POINTS:]
        new_y = frame.y[new_start:]
        self.y = new_y.copy() if self.y is None else np.concatenate((self.y[old_start:], new_y))[-MAX_POINTS:]
        self.clipping = np.concatenate((self.clipping[old_start:],
                                        np.full(len(new_y), frame.clipped)))[-MAX_POINTS:]

    def capture(self, span, channel, level, slope, mode, allow_auto, prefer_newest=True):
        """!
        @brief Searches the buffer for a trigger crossing or produces an Auto/Roll capture.
        @param span Total horizontal window width in seconds (10 divisions).
        @param channel Zero-based trigger source channel index.
        @param level Trigger threshold voltage in calibrated channel units.
        @param slope Trigger edge polarity ('Rising' or 'Falling').
        @param mode Trigger mode ('Auto', 'Normal', or 'Roll').
        @param allow_auto Permits untriggered fallback capture when True.
        @param prefer_newest Scans backward from newest samples if True; forward if False (Single mode).
        @return @ref Capture object if window criteria are satisfied, else None.
        """
        if len(self.t) < 2 or self.y is None or channel >= self.y.shape[1]:
            return None
        y = self.y[:, channel]
        edges = []
        if mode != 'Roll':
            mask = (y[:-1] < level) & (y[1:] >= level) if slope == 'Rising' else (y[:-1] > level) & (y[1:] <= level)
            edges = np.flatnonzero(mask)
        if prefer_newest:
            edges = edges[::-1]
        for edge in edges:
            frac = (level - y[edge]) / (y[edge + 1] - y[edge])
            at = float(self.t[edge] + frac * (self.t[edge + 1] - self.t[edge]))
            if at <= self.last_trigger:
                continue
            start, end = at - span * .2, at + span * .8
            if start < self.t[0]:
                continue
            if end > self.t[-1]:
                continue
            lo, hi = np.searchsorted(self.t, [start, end], side='left')
            if hi - lo < 2:
                continue
            self.last_trigger = at
            return Capture(self.t[lo:hi].copy() - at, self.y[lo:hi].copy(), at, True, bool(np.any(self.clipping[lo:hi])))
        if mode in ('Auto', 'Roll') and allow_auto and self.t[-1] > self.last_auto:
            lo = min(len(self.t) - 2, max(0, int(np.searchsorted(self.t, self.t[-1] - span))))
            self.last_auto = self.t[-1]
            rolling = mode == 'Roll'
            origin = float(self.t[-1] if rolling else self.t[-1] - span * .8)
            return Capture(self.t[lo:].copy() - origin, self.y[lo:].copy(), origin,
                           False, bool(np.any(self.clipping[lo:])), roll=rolling)
        return None


def measurements(t, y):
    """!
    @brief Computes standard oscilloscope waveform metrics (Vpp, Mean, RMS, Min, Max, Freq, Period, Duty).
    @param t 1D array of sample timestamps in seconds.
    @param y 1D array of sample amplitudes.
    @return Dictionary mapping metric keys to float values (or None if unmeasurable).
    """
    y, t = np.asarray(y), np.asarray(t)
    finite = np.isfinite(y)
    valid = y[finite]
    if not len(valid):
        return dict.fromkeys(('vpp', 'mean', 'rms', 'minimum', 'maximum', 'frequency', 'period', 'duty'))
    low, high = float(np.min(valid)), float(np.max(valid))
    result = dict(vpp=high - low, mean=float(np.mean(valid)), rms=float(np.sqrt(np.mean(valid * valid))),
                  minimum=low, maximum=high, frequency=None, period=None, duty=None)
    if not finite.all() or len(y) < 8 or high - low <= max(1e-12, abs(result['mean']) * 1e-9):
        return result
    mid, hysteresis = (high + low) * .5, (high - low) * .1
    armed = y[0] < mid - hysteresis
    crossings = []
    for i in range(1, len(y)):
        if y[i] < mid - hysteresis:
            armed = True
        if armed and y[i - 1] < mid <= y[i]:
            crossings.append(t[i - 1] + (mid - y[i - 1]) * (t[i] - t[i - 1]) / (y[i] - y[i - 1]))
            armed = False
    if len(crossings) >= 3:
        periods = np.diff(crossings)
        period = float(np.median(periods))
        if period > 0 and np.max(np.abs(periods - period)) / period < .15:
            # Require >= 10 samples per estimated cycle; no precision claim on undersampled data.
            if np.median(np.diff(t)) <= period / 10:
                result['period'], result['frequency'] = period, 1 / period
                result['duty'] = float(np.mean(y >= mid) * 100.0)
    return result

# Serial and TCP acquisition worker


class RealtimeEventQueue:
    """!
    @brief Thread-safe queue that never allows old samples to block live acquisition.
    @details When full, discards the oldest @ref Frame rather than metadata or error events,
             and marks the subsequent retained frame with `gap = True`.
    """

    def __init__(self, maxsize=128):
        """!
        @brief Initializes the bounded event deque and condition lock.
        @param maxsize Maximum number of queued event tuples.
        """
        self.maxsize = maxsize
        self._events = deque()
        self._cv = threading.Condition()
        self.dropped = 0

    def put(self, item, timeout=None):
        """!
        @brief Enqueues an event tuple, dropping the oldest data frame if full.
        @param item Tuple of `(kind, value)`.
        @param timeout Unused compatibility parameter.
        @throws queue.Full If the queue is completely saturated with non-Frame control events.
        """
        with self._cv:
            if len(self._events) >= self.maxsize:
                index = next((i for i, (_, value) in enumerate(self._events)
                              if isinstance(value, Frame)), None)
                if index is None:
                    raise queue.Full('Queue contains only critical connection events')
                del self._events[index]
                self.dropped += 1
                # A retained frame after a discarded one must not look
                # temporally continuous to the capture engine.
                for event_kind, event_value in self._events:
                    if isinstance(event_value, Frame):
                        event_value.gap = True
                        break
            self._events.append(item)
            self._cv.notify()

    def get_nowait(self):
        """!
        @brief Retrieves the oldest queued event without blocking.
        @return Tuple of `(kind, value)`.
        @throws queue.Empty If no events are queued.
        """
        return self.get(block=False)

    def get(self, block=True, timeout=None):
        """!
        @brief Pops the oldest event from the queue.
        @param block Whether to wait for an item if empty.
        @param timeout Maximum wait time in seconds when @p block is True.
        @return Tuple of `(kind, value)`.
        @throws queue.Empty If queue remains empty.
        """
        with self._cv:
            if not block and not self._events:
                raise queue.Empty
            if block and not self._events:
                self._cv.wait_for(lambda: bool(self._events), timeout=timeout)
            if not self._events:
                raise queue.Empty
            return self._events.popleft()

    def qsize(self):
        """!
        @brief Returns the current number of items in the queue.
        @return Integer queue length.
        """
        with self._cv:
            return len(self._events)


class Reader(threading.Thread):
    """!
    @brief Background daemon thread reading newline-delimited JSON packets over Serial or TCP.
    """

    def __init__(self, transport, address, rate):
        """!
        @brief Configures the background transport reader.
        @param transport Transport type string (`'Serial'` or `'TCP'`).
        @param address Serial port path (e.g., `'COM3'`) or IP/hostname.
        @param rate Serial baud rate or TCP port number.
        """
        super().__init__(daemon=True)
        self.transport, self.address, self.rate = transport, address, rate
        self.events = RealtimeEventQueue(maxsize=128)
        self.stop_event = threading.Event()
        self.invalid = self.dropped = 0
        self.last_error = ''
        self.link = None

    def post(self, kind, value):
        """!
        @brief Posts a decoded item or status event to the GUI event queue.
        @param kind Event category (`'connected'`, `'data'`, or `'error'`).
        @param value Event payload object or message string.
        """
        try:
            self.events.put((kind, value))
            self.dropped = self.events.dropped
        except queue.Full:
            if isinstance(value, Frame):
                self.dropped += 1
            else:
                raise RuntimeError('Receive queue filled with connection events')

    def close(self):
        """!
        @brief Signals the reader thread to terminate and closes the underlying socket/port.
        """
        self.stop_event.set()
        if self.link is not None:
            try:
                self.link.close()
            except (OSError, AttributeError):
                pass

    def run(self):
        """!
        @brief Main thread loop: opens transport, reads chunks, splits lines, and decodes JSON frames.
        """
        decoder, buffer = Decoder(), bytearray()
        last_dropped = 0
        try:
            if self.transport == 'Serial':
                import serial
                # A short timeout lets the reader respond to stop requests
                # without holding up the GUI or the stream.
                self.link = serial.Serial(self.address, self.rate, timeout=.08)
                read = lambda: self.link.read(min(max(self.link.in_waiting, 1), 65536))
            else:
                self.link = socket.create_connection((self.address, self.rate), timeout=3)
                self.link.settimeout(.08)
                read = lambda: self.link.recv(65536)
            self.post('connected', self.address)
            while not self.stop_event.is_set():
                try:
                    chunk = read()
                except socket.timeout:
                    continue
                if not chunk:
                    if self.transport == 'TCP':
                        raise ConnectionError('Device closed the connection')
                    continue
                buffer.extend(chunk)
                while b'\n' in buffer:
                    line, _, buffer = buffer.partition(b'\n')
                    if not line.strip():
                        continue
                    try:
                        item = decoder.decode(line)
                    except (ProtocolError, UnicodeError) as exc:
                        self.invalid += 1
                        self.last_error = str(exc)
                        continue
                    if isinstance(item, Frame) and self.dropped != last_dropped:
                        item.gap = True
                        last_dropped = self.dropped
                    self.post('data', item)
                if len(buffer) > MAX_LINE:
                    raise ProtocolError('Unterminated/oversized message; maximum 2 MB')
        except Exception as exc:
            if not self.stop_event.is_set():
                self.last_error = str(exc)
                try:
                    self.post('error', str(exc))
                except RuntimeError:
                    pass
        finally:
            self.close()

# Acquisition processing and display interpolation


def display_interpolate(t, y, pixels, method='Sin(x)/x'):
    """!
    @brief Applies windowed sinc interpolation on a display copy for sparse, uniformly timed data.
    @details Retains all original sample points so extrema and spikes are never lost.
    @param t 1D array of sample timestamps.
    @param y 1D array of sample amplitudes (in screen divisions).
    @param pixels Horizontal viewport width in pixels.
    @param method Interpolation mode (`'Sin(x)/x'` or `'Linear'`).
    @return Tuple `(times, values)` of interpolated trace coordinates.
    """
    t, y = np.asarray(t), np.asarray(y)
    if method != 'Sin(x)/x' or len(t) < 3 or len(t) >= 4 * max(1, pixels) or not np.all(np.isfinite(y)):
        return t, y
    dt = np.diff(t)
    step = float(np.median(dt))
    if step <= 0 or np.max(np.abs(dt - step)) > step * .01:
        return t, y
    count = min(16000, max(len(t), int(pixels) * 2))
    targets = np.linspace(t[0], t[-1], count)
    u = (targets - t[0]) / step
    base = np.floor(u).astype(int)
    indices = base[:, None] + np.arange(-7, 9)[None, :]
    delta = u[:, None] - indices
    valid = (indices >= 0) & (indices < len(y)) & (np.abs(delta) < 8)
    weights = np.sinc(delta) * np.sinc(delta / 8) * valid
    denom = weights.sum(axis=1)
    values = (weights * y[np.clip(indices, 0, len(y) - 1)]).sum(axis=1) / np.where(abs(denom) > 1e-12, denom, 1)
    # Keep exact sample values, even at isolated spikes.
    times = np.concatenate((targets, t))
    vals = np.concatenate((values, y))
    order = np.argsort(times, kind='stable')
    return times[order], vals[order]


class AcquisitionProcessor:
    """!
    @brief Applies Sample, multi-trigger Average, or Hi-Res boxcar filtering to captures.
    """

    def __init__(self):
        """!
        @brief Initializes the acquisition processor state.
        """
        self.reset()

    def reset(self):
        """!
        @brief Clears accumulated averaging history.
        """
        self.records = deque()
        self.signature = None
        self.count = 0

    def process(self, capture, mode='Sample', average_n=16, hires_width=4):
        """!
        @brief Processes a @ref Capture according to the active acquisition mode.
        @param capture Input @ref Capture object.
        @param mode Acquisition mode (`'Sample'`, `'Average'`, or `'Hi-Res'`).
        @param average_n Number of triggered records to average when @p mode is `'Average'`.
        @param hires_width Boxcar moving-average kernel width when @p mode is `'Hi-Res'`.
        @return Processed 2D sample array matching `capture.y` shape.
        """
        raw = capture.y
        if mode == 'Sample' or len(raw) == 0:
            self.reset()
            return raw.copy()
        if mode == 'Hi-Res':
            self.reset()
            width = min(max(2, int(hires_width)), len(raw))
            if width < 2:
                return raw.copy()
            before = (width - 1) // 2
            after = width - 1 - before
            padded = np.pad(raw, ((before, after), (0, 0)), mode='edge')
            return np.column_stack([np.convolve(padded[:, i], np.ones(width) / width, mode='valid') for i in range(raw.shape[1])])
        # Never combine an automatic free-run record with triggered acquisitions.
        if not capture.triggered:
            self.reset()
            return raw.copy()
        dt = np.diff(capture.t)
        step = float(np.median(dt)) if len(dt) else 0.
        if step <= 0 or np.max(abs(dt - step)) > step * .01:
            self.reset()
            return raw.copy()
        signature = (raw.shape, round(step, 12), int(average_n))
        if signature != self.signature:
            self.reset()
            self.signature = signature
        # Resample only the processing copy onto the newest trigger-relative grid
        # to account for the sub-sample phase of an interpolated trigger crossing.
        self.records.append((capture.t.copy(), raw.copy()))
        while len(self.records) > average_n:
            self.records.popleft()
        self.count = len(self.records)
        aligned = [np.column_stack([np.interp(capture.t, t, y[:, i]) for i in range(raw.shape[1])]) for t, y in self.records]
        return np.mean(aligned, axis=0)

# Step rendering for digital and PWM signals


def looks_like_pwm(y):
    """!
    @brief Heuristic check determining whether a trace resembles a two-level PWM/digital signal.
    @param y 1D array of trace values (in divisions).
    @return True if >= 86% of samples cluster near the min and max plateaus with span >= 0.28 div.
    """
    y = np.asarray(y, dtype=float).reshape(-1)
    if len(y) < 6 or not np.all(np.isfinite(y)):
        return False
    low = float(np.min(y))
    high = float(np.max(y))
    span = high - low
    if span < 0.28:  # avoid amplifying DC noise into imaginary digital edges
        return False
    low_group = np.abs(y - low) < span * .16
    high_group = np.abs(y - high) < span * .16
    if not low_group.any() or not high_group.any():
        return False
    # Need mostly two stable voltage levels, not a sinusoid or ramp.
    return float(np.mean(low_group | high_group)) >= .86


def pwm_hold_trace(t, y):
    """!
    @brief Constructs sample-and-hold step vertices with vertical transitions at sample midpoints.
    @param t 1D strictly increasing timestamp array.
    @param y 1D sample amplitude array.
    @return Tuple `(tx, vy)` of expanded 2N vertex arrays for step rendering.
    @throws ValueError If lengths differ or timestamps do not strictly increase.
    """
    t = np.asarray(t, dtype=float).reshape(-1)
    y = np.asarray(y, dtype=float).reshape(-1)
    if len(t) != len(y):
        raise ValueError('Time and voltage arrays must have equal length')
    if len(t) < 2:
        return t.copy(), y.copy()
    if np.any(np.diff(t) <= 0):
        raise ValueError('Sample times must strictly increase')
    # A sample-and-hold step plot, with transitions halfway between samples.
    # 2n vertices and no staircase interpolation/overshoot.
    transitions = (t[:-1] + t[1:]) * .5
    tx = np.empty(2 * len(t), dtype=float)
    vy = np.empty(2 * len(y), dtype=float)
    tx[0] = t[0]
    vy[0] = y[0]
    tx[1:-1:2] = transitions
    tx[2::2] = transitions
    vy[1:-1:2] = y[:-1]
    vy[2::2] = y[1:]
    tx[-1] = t[-1]
    vy[-1] = y[-1]
    return tx, vy

# Two-point calibration


@dataclass(frozen=True)
class CalibrationResult:
    """!
    @brief Outcome of a two-point linear gain and offset calibration calculation.
    """
    gain: float             ##< Newly computed multiplicative gain factor.
    offset: float           ##< Newly computed additive offset in channel units.
    measured_low: float     ##< Median observed low plateau in raw uncalibrated units.
    measured_high: float    ##< Median observed high plateau in raw uncalibrated units.
    measured_span: float    ##< Observed peak-to-peak plateau span (`measured_high - measured_low`).
    gain_multiplier: float  ##< Ratio of new gain to previous gain.
    suspicious: bool        ##< True if gain change falls outside the safe [0.2x, 5.0x] band.


def estimate_plateaus(samples):
    """!
    @brief Estimates stable low and high plateau levels of a square wave using interquartile medians.
    @param samples 1D array of uncalibrated raw samples (minimum 25 samples).
    @return Tuple `(low_median, high_median)`.
    @throws ValueError If sample count is insufficient or waveform is flat.
    """
    y = np.asarray(samples, dtype=float)
    if y.ndim != 1 or y.size < 25 or not np.all(np.isfinite(y)):
        raise ValueError('A clean capture containing at least 25 samples is required.')
    low, high = np.percentile(y, [10., 90.])
    if high - low < 1e-8:
        raise ValueError('The input signal appears flat; check the wiring and acquisition.')
    lower = y[y <= np.percentile(y, 25.)]
    upper = y[y >= np.percentile(y, 75.)]
    return float(np.median(lower)), float(np.median(upper))


def fit_two_point(samples, current_gain, current_offset, reference_low, reference_high):
    """!
    @brief Computes new linear calibration coefficients (`raw * gain + offset`) from a known square wave.
    @param samples 1D array of already-calibrated captured samples.
    @param current_gain Existing channel gain multiplier.
    @param current_offset Existing channel additive offset.
    @param reference_low Independently verified physical LOW voltage.
    @param reference_high Independently verified physical HIGH voltage.
    @return @ref CalibrationResult instance containing updated coefficients and safety flags.
    @throws ValueError If inputs are non-finite, zero gain, inverted references, or flat signal.
    """
    for v in (current_gain, current_offset, reference_low, reference_high):
        if not math.isfinite(v):
            raise ValueError('Calibration values must be finite.')
    if abs(current_gain) < 1e-15:
        raise ValueError('Existing gain must be non-zero.')
    if reference_high <= reference_low:
        raise ValueError('Reference HIGH must exceed LOW.')
    already_calibrated = np.asarray(samples, dtype=float)
    original = (already_calibrated - current_offset) / current_gain
    observed_low, observed_high = estimate_plateaus(original)
    difference = observed_high - observed_low
    if abs(difference) < 1e-8:
        raise ValueError('Measured low and high levels are indistinguishable.')
    gain = (reference_high - reference_low) / difference
    offset = reference_low - gain * observed_low
    if not (math.isfinite(gain) and math.isfinite(offset)):
        raise ValueError('Calibration overflow.')
    multiplier = gain / current_gain
    # >5x is suspect; a tiny voltage with nominal 3.3V input is usually a wiring
    # or transmission-unit fault, not ordinary calibration error.
    suspicious = abs(multiplier) > 5 or abs(multiplier) < .2
    return CalibrationResult(float(gain), float(offset), observed_low, observed_high,
                             difference, float(multiplier), suspicious)

# Vertical scaling and range checks


def fit_voltage_scale(values, scale_steps, divisions=8):
    """!
    @brief Selects a 1-2-5 vertical scale step and center offset leaving >= 1 division margin.
    @param values Array of sample values to fit vertically.
    @param scale_steps Sorted sequence of allowed scale values per division.
    @param divisions Total vertical graticule divisions (default 8).
    @return Tuple `(volts_per_division, vertical_midpoint)` or None if no finite data exists.
    """
    y = np.asarray(values, dtype=float)
    y = y[np.isfinite(y)]
    if y.size == 0:
        return None
    lo, hi = float(np.min(y)), float(np.max(y))
    span = hi - lo
    required = max(span / max(1, divisions - 2), float(scale_steps[0]))
    scale = next((float(v) for v in scale_steps if v >= required), float(scale_steps[-1]))
    center = (lo + hi) / 2
    return scale, center


def display_range_health(values, volts_per_division, position, divisions=8):
    """!
    @brief Evaluates what fraction of a trace extends outside the vertical graticule bounds.
    @param values Sample values in display units.
    @param volts_per_division Vertical scale setting per division.
    @param position Vertical center offset in display units.
    @param divisions Total vertical graticule divisions (default 8).
    @return Tuple `(outside_fraction, is_clipped)`.
    """
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0 or not math.isfinite(volts_per_division) or volts_per_division <= 0:
        return (0.0, False)
    yy = (values - position) / volts_per_division
    limit = divisions / 2
    outside = float(np.mean((yy < -limit) | (yy > limit)))
    return outside, bool(outside > 0)


def raw_span_from_calibrated(calibrated_values, gain):
    """!
    @brief Recovers the uncalibrated input peak-to-peak span by dividing out @p gain.
    @param calibrated_values Array of calibrated sample values.
    @param gain Applied linear channel gain.
    @return Peak-to-peak raw input span, or None if invalid.
    """
    if not math.isfinite(gain) or abs(gain) <= 1e-15:
        return None
    values = np.asarray(calibrated_values, dtype=float)
    if values.size == 0 or not np.isfinite(values).all():
        return None
    return float(np.ptp(values) / abs(gain))


def suspicious_gain(gain):
    """!
    @brief Checks whether a software channel gain lies outside the normal [0.2, 5.0] range.
    @param gain Channel gain value.
    @return True if non-finite, non-positive, > 5, or < 0.2.
    """
    return not math.isfinite(gain) or gain <= 0 or gain > 5 or gain < .2

# Colors, fonts, and Qt stylesheets


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
    """!
    @brief Forces Qt to re-evaluate dynamic property QSS rules on a widget.
    @param widget QWidget whose style needs refreshing.
    """
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def stylesheet():
    """!
    @brief Builds the complete Qt Style Sheet (QSS) for the instrument UI.
    @return QSS string.
    """
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
QLabel#measurementSource {{ color:#194866; background:#E4EEF6; border-radius:4px;
                             font-family:{mono}; font-size:10px; font-weight:750; padding:2px 6px; }}
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
    """!
    @brief Generates per-channel toggle button QSS with trace color accents.
    @param color Hex color string for the active channel trace.
    @return QSS snippet string.
    """
    return (f'QPushButton#channelToggle {{ color:{color}; border-color:{color}; background:#FFFFFF; }} '
            f'QPushButton#channelToggle:!checked, QPushButton#channelToggle:disabled '
            f'{{ color:#98A5B7; border-color:{BORDER}; background:#F1F4F8; }}')

# Oscilloscope interface

## Standard 1-2-5 vertical scale steps in base units/division.
SCALES = [1e-6, 2e-6, 5e-6, 1e-5, 2e-5, 5e-5, .0001, .0002, .0005, .001, .002, .005, .01, .02, .05, .1, .2, .5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]
## Standard 1-2-5 horizontal timebase steps in seconds/division.
TIME_SCALES = [1e-6, 2e-6, 5e-6, 1e-5, 2e-5, 5e-5, .0001, .0002, .0005, .001, .002, .005, .01, .02, .05, .1, .2, .5, 1, 2, 5, 10, 20, 50, 100]
## Mapping of physical sensor presets to default engineering unit symbols.
SENSOR_UNITS = {'Device units': None, 'Voltage': 'V', 'Temperature': '°C', 'Current': 'A',
                'Pressure': 'Pa', 'Acceleration': 'm/s²', 'Light': 'lx', 'Humidity': '%RH',
                'Speed': 'RPM', 'Position': 'mm', 'Digital level': 'logic', 'Custom': None}


def fmt(value, unit=''):
    """!
    @brief Formats a numeric value using SI engineering prefixes (n, µ, m, k, M, G).
    @param value Numeric quantity to format (or None/NaN).
    @param unit Physical unit suffix string.
    @return Formatted string (e.g., `'3.3 V'`, `'500 ms'`, `'—'`).
    """
    if value is None or not math.isfinite(float(value)):
        return '—'
    if unit in ('logic', '°C'):
        return f'{value:.4g} {unit}'
    if value == 0:
        return ('0 ' + unit).strip()
    for factor, prefix in [(1e9, 'G'), (1e6, 'M'), (1e3, 'k'), (1, ''), (1e-3, 'm'), (1e-6, 'µ'), (1e-9, 'n')]:
        if abs(value) >= factor:
            return f'{value/factor:.4g} {prefix}{unit}'.strip()
    return f'{value:.4g} {unit}'.strip()


def combo(items):
    """!
    @brief Helper factory creating a populated QComboBox.
    @param items Iterable of string labels.
    @return Configured QComboBox instance.
    """
    w = QComboBox()
    w.addItems(items)
    return w


def spin(value=0, low=-1e9, high=1e9, decimals=6):
    """!
    @brief Helper factory creating a QDoubleSpinBox with keyboard tracking disabled.
    @param value Initial value.
    @param low Minimum allowed value.
    @param high Maximum allowed value.
    @param decimals Decimal precision digits.
    @return Configured QDoubleSpinBox instance.
    """
    w = QDoubleSpinBox()
    w.setDecimals(decimals)
    w.setRange(low, high)
    w.setValue(value)
    w.setKeyboardTracking(False)
    return w


class FittedReading(QLabel):
    """!
    @brief QLabel subclass that dynamically shrinks font size before eliding long measurement strings.
    """

    def fitted_font(self):
        """!
        @brief Computes the largest pixel font size between 9 px and 13 px that fits the label width.
        @return Adjusted QFont instance.
        """
        font = QFont(self.font())
        font.setPixelSize(13)
        while font.pixelSize() > 9 and QFontMetrics(font).horizontalAdvance(self.text()) > self.contentsRect().width():
            font.setPixelSize(font.pixelSize() - 1)
        return font

    def paintEvent(self, event):
        """!
        @brief Renders the fitted measurement reading with right-elision fallback.
        @param event Qt QPaintEvent.
        """
        painter = QPainter(self)
        font = self.fitted_font()
        painter.setFont(font)
        painter.setPen(self.palette().color(self.foregroundRole()))
        text = QFontMetrics(font).elidedText(self.text(), Qt.ElideRight, self.contentsRect().width())
        painter.drawText(self.contentsRect(), Qt.AlignLeft | Qt.AlignVCenter, text)


class SegmentedControl(QWidget):
    """!
    @brief Mutually exclusive horizontal button bar used for channel coupling selection (DC/AC/GND).
    """

    ## Signal emitted when the selected segment label changes.
    currentTextChanged = Signal(str)

    def __init__(self, items):
        """!
        @brief Builds the segmented button group.
        @param items Sequence of button labels.
        """
        super().__init__()
        self.buttons = {}
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        for text in items:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setObjectName('segment')
            self.group.addButton(button)
            self.buttons[text] = button
            row.addWidget(button)
            button.clicked.connect(lambda _, text=text: self.currentTextChanged.emit(text))
        self.buttons[items[0]].setChecked(True)

    def currentText(self):
        """!
        @brief Returns the currently checked button label.
        @return Active segment string.
        """
        return next(text for text, button in self.buttons.items() if button.isChecked())

    def setCurrentText(self, text):
        """!
        @brief Programmatically selects a segment by label and emits `currentTextChanged`.
        @param text Target segment label.
        """
        if text in self.buttons and text != self.currentText():
            self.buttons[text].setChecked(True)
            self.currentTextChanged.emit(text)


class Graticule(pg.GraphicsObject):
    """!
    @brief Custom 10 x 8 oscilloscope graticule with dotted major lines and center-axis tick marks.
    """

    def __init__(self):
        """!
        @brief Initializes the cached QPicture graticule object.
        """
        super().__init__()
        self.picture = QPicture()
        self.rect = QRectF(0, -4, 1, 8)
        self.dark = False

    def set_dark(self, enabled):
        """!
        @brief Toggles between light and Tektronix-inspired dark screen graticule palettes.
        @param enabled True for dark screen styling.
        """
        self.dark = bool(enabled)
        self.set_window(self.rect.left(), self.rect.right())

    def set_window(self, low, high):
        """!
        @brief Redraws the cached graticule QPicture for a new horizontal X range.
        @param low Left X coordinate bound.
        @param high Right X coordinate bound.
        """
        self.prepareGeometryChange()
        self.rect = QRectF(low, -4, high - low, 8)
        self.picture = QPicture()
        painter = QPainter(self.picture)
        major = QColor('#3A5570' if self.dark else GRID)
        major.setAlphaF(.82)
        painter.setPen(pg.mkPen(major, width=1, style=Qt.DotLine, cosmetic=True))
        span = high - low
        center = (low + high) / 2
        for n in range(11):
            x = low + n * span / 10
            painter.drawLine(QPointF(x, -4), QPointF(x, 4))
        for n in range(-4, 5):
            painter.drawLine(QPointF(low, n), QPointF(high, n))
        painter.setPen(pg.mkPen('#728CA7' if self.dark else AXES, width=1, cosmetic=True))
        painter.drawLine(QPointF(low, 0), QPointF(high, 0))
        painter.drawLine(QPointF(center, -4), QPointF(center, 4))
        for n in range(51):
            x = low + n * span / 50
            painter.drawLine(QPointF(x, -.055), QPointF(x, .055))
        for n in range(41):
            y = -4 + n / 5
            painter.drawLine(QPointF(center - span * .005, y), QPointF(center + span * .005, y))
        painter.end()
        self.update()

    def paint(self, painter, option, widget=None):
        """!
        @brief Paints the pre-recorded graticule QPicture onto the plot canvas.
        """
        painter.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        """!
        @brief Returns the bounding rectangle of the graticule in plot coordinates.
        @return QRectF bounding box.
        """
        return self.rect


class Channel:
    """!
    @brief Stores user-configured metadata, linear calibration, and sensor profile for one DSO channel.
    """

    def __init__(self, i):
        """!
        @brief Initializes default channel parameters (`gain=1.0`, `offset=0.0`).
        @param i Zero-based channel index (0..3).
        """
        self.name = f'CH{i+1}'
        self.unit = 'V'
        self.gain = 1.
        self.offset = 0.
        self.sensor = 'Device units'


class ProfileDialog(QDialog):
    """!
    @brief Modal dialog allowing users to configure board profiles, sensor types, names, units, gain, and offset.
    """

    def __init__(self, parent):
        """!
        @brief Constructs the device & sensor configuration dialog from parent window state.
        @param parent Parent @ref ScopeWindow instance.
        """
        super().__init__(parent)
        self.setWindowTitle('Device & sensor configuration')
        self.resize(790, 500)
        layout = QVBoxLayout(self)
        note = QLabel('Value = raw × gain + offset')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.board = combo(['Generic / Custom', 'LPC family', 'Arduino family', 'ESP32 / ESP8266', 'RP2040 / RP2350', 'STM32 family', 'Raspberry Pi / Linux', 'Other microprocessor'])
        self.board.setCurrentText(parent.board_profile)
        form.addRow('Board profile', self.board)
        layout.addLayout(form)
        grid = QGridLayout()
        layout.addLayout(grid)
        for j, s in enumerate(['Channel', 'Sensor / quantity', 'Name', 'Unit', 'Gain', 'Offset']):
            grid.addWidget(QLabel(s), 0, j)
        self.fields = []
        for i, ch in enumerate(parent.channels):
            sensor = combo(list(SENSOR_UNITS))
            sensor.setCurrentText(ch.sensor)
            name = QLineEdit(ch.name)
            name.setMaxLength(40)
            unit = QLineEdit(ch.unit)
            unit.setMaxLength(16)
            unit.setMaximumWidth(75)
            gain = spin(ch.gain, decimals=9)
            offset = spin(ch.offset, decimals=9)
            sensor.currentTextChanged.connect(lambda s, u=unit: u.setText(SENSOR_UNITS[s]) if SENSOR_UNITS[s] is not None else None)
            for j, w in enumerate([QLabel(f'CH{i+1}'), sensor, name, unit, gain, offset]):
                grid.addWidget(w, i + 1, j)
            self.fields.append((sensor, name, unit, gain, offset))
        layout.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ScopeWindow(QMainWindow):
    """!
    @brief Main application window implementing the 4-channel Digital Storage Oscilloscope UI and control loop.
    """

    def __init__(self):
        """!
        @brief Initializes oscilloscope state, loads saved profiles, constructs UI, and starts the 60 Hz poll timer.
        """
        super().__init__()
        self.setWindowTitle('Generic DSO')
        self.resize(1500, 970)
        self.setMinimumSize(1150, 740)
        self.channels = [Channel(i) for i in range(4)]
        self.board_profile = 'Generic / Custom'
        self.legacy_channels = None
        self.device_status = ''
        self.reader = None
        self.metadata = None
        self.engine = CaptureEngine()
        self.current = None
        self.running = False
        self.single_pending = False
        self.link_open = False
        self.pending_run = False
        self.connected_since = 0.
        self.reference_mode = "tek"
        self.last_rx = 0.
        self.last_capture = 0.
        self.gaps = 0
        self.frames = 0
        self.invalid = 0
        self.syncing = False
        self.initial_scale = True
        self.initial_fit_pending = True
        self.reference_active = False
        self.calibration_note = ''
        self.last_display_clip = False
        self.processor = AcquisitionProcessor()
        self.processed_y = None
        self._processed_capture = None
        self._probe_factors = [1., 1., 1., 1.]
        self._capture_ac_reference = None
        self._cursor_adjusting = False
        self._last_draw = 0.
        self._last_measure_update = 0.
        self._last_notice_update = 0.
        self._ui_fps = 0.
        self._display_frequency = None
        self._measure_channel = 0
        self._last_device_samples = 0
        self._device_samples = 0
        self._last_fresh_capture = None
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setTimerType(Qt.PreciseTimer)
        self._redraw_timer.timeout.connect(self.draw_capture)
        self.config_path = Path.home() / '.generic_dso_profiles.json'
        self.load_profiles()
        self.build_ui()
        self.theme()
        self.refresh_ports()
        self.controls_enabled(False)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.poll)
        self.timer.start(16)
        self.update_axes()
        self.update_cursor()
        self.update_measurements()

    def load_profiles(self):
        """!
        @brief Loads persisted board and channel calibration profiles from `~/.generic_dso_profiles.json`.
        """
        try:
            obj = json.loads(self.config_path.read_text())
            self.board_profile = obj.get('board', 'Generic / Custom')
            for ch, raw in zip(self.channels, obj.get('channels', [])):
                for key in ['name', 'unit', 'sensor']:
                    if isinstance(raw.get(key), str):
                        setattr(ch, key, raw[key][:40])
                for key in ['gain', 'offset']:
                    val = float(raw.get(key, getattr(ch, key)))
                    if math.isfinite(val) and abs(val) <= 1e9:
                        setattr(ch, key, val)
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save_profiles(self):
        """!
        @brief Persists current board and channel calibration settings to disk.
        """
        if self.legacy_channels is not None:
            return
        try:
            self.config_path.write_text(json.dumps({'board': self.board_profile, 'channels': [vars(c) for c in self.channels]}, indent=2))
        except OSError as exc:
            QMessageBox.warning(self, 'Settings', str(exc))

    def build_ui(self):
        """!
        @brief Constructs and lays out all Qt widgets, pyqtgraph plot items, toolbars, and side menus.
        """
        # Scroll the complete panel on laptop-height screens instead of cropping
        # CH1–CH4 probe controls below the bottom edge.
        self.root_scroll = QScrollArea()
        self.root_scroll.setWidgetResizable(True)
        self.root_scroll.setFrameShape(QFrame.NoFrame)
        self.root_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.root_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        root = QWidget()
        root.setObjectName('rootFrame')
        root.setMinimumSize(1120, 880)
        self.root_scroll.setWidget(root)
        self.setCentralWidget(self.root_scroll)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 12, 16, 10)
        outer.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(8)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        title = QLabel('Generic DSO')
        title.setObjectName('title')
        brand.addWidget(title)
        caption = QLabel('PRECISION SIGNAL WORKBENCH')
        caption.setObjectName('subtitle')
        brand.addWidget(caption)
        header.addLayout(brand)
        header.addSpacing(9)
        self.transport = combo(['Serial', 'TCP'])
        self.transport.currentTextChanged.connect(self.transport_changed)
        self.port = QComboBox()
        self.port.setEditable(True)
        self.port.setMinimumWidth(150)
        self.port.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.rate = combo(['921600', '115200', '230400', '460800', '1000000', '2000000'])
        self.rate.setEditable(True)
        self.rate.setToolTip('ESP32 potentiometer PWM firmware uses 921600 baud. Older 1 Hz firmware may use 115200.')
        self.rate_label = QLabel('Baud')
        self.rate_label.setObjectName('small')
        self.refresh = QPushButton('↻')
        self.refresh.setToolTip('Refresh serial ports')
        self.refresh.clicked.connect(self.refresh_ports)
        self.connect_button = QPushButton('Connect')
        self.connect_button.setObjectName('primary')
        self.connect_button.clicked.connect(self.toggle_connection)
        for w in [self.transport, self.port, self.rate_label, self.rate, self.refresh, self.connect_button]:
            header.addWidget(w)
        header.addStretch(1)
        self.preset_button = QPushButton('TEK PRESET')
        self.preset_button.setObjectName('preset')
        self.preset_button.setToolTip('Use the supplied Tektronix TBS1062 reference: CH1 1 V/div and 500 ms/div.')
        self.preset_button.clicked.connect(self.apply_tek_preset)
        header.addWidget(self.preset_button)
        self.pwm_preset_button = QPushButton('PWM 100 Hz')
        self.pwm_preset_button.setObjectName('preset')
        self.pwm_preset_button.setToolTip('GPIO25 PWM + GPIO34 ADC: 5 ms/div, CH1 1 V/div, rising trigger 1.65 V.')
        self.pwm_preset_button.clicked.connect(self.apply_pwm_preset)
        header.addWidget(self.pwm_preset_button)
        self.calibration_button = QPushButton('CALIBRATE')
        self.calibration_button.setObjectName('preset')
        self.calibration_button.setToolTip('Two-point voltage calibration using a KNOWN signal. Does not repair wiring or ADC saturation.')
        self.calibration_button.clicked.connect(self.calibrate_channel)
        header.addWidget(self.calibration_button)
        self.reset_gain_button = QPushButton('RESET CH1 GAIN')
        self.reset_gain_button.setObjectName('resetGain')
        self.reset_gain_button.setToolTip('Restore CH1 to direct firmware-reported volts (gain 1, offset 0). Also clears the old unsafe ×686 calibration.')
        self.reset_gain_button.clicked.connect(self.reset_ch1_gain)
        self.state = QLabel('●  Disconnected')
        self.state.setObjectName('state')
        self.state.setMinimumWidth(125)
        header.addWidget(self.state)
        self.profile_button = QPushButton('⚙')
        self.profile_button.setToolTip('Device & sensors')
        self.profile_button.clicked.connect(self.configure)
        header.addWidget(self.profile_button)
        self.device_label = QLabel('No acquisition device')
        self.device_label.hide()
        outer.addLayout(header)
        self.connection_hint = QLabel('')
        self.connection_hint.setObjectName('connectionHint')
        self.connection_hint.setWordWrap(True)
        self.connection_hint.hide()
        outer.addWidget(self.connection_hint)
        reference = QFrame()
        reference.setObjectName('referenceBar')
        reference_row = QHBoxLayout(reference)
        reference_row.setContentsMargins(11, 6, 11, 6)
        reference_row.setSpacing(9)
        self.reference_label = QLabel('TEK TBS1062 REFERENCE  ·  CH1 1 V/div  ·  500 ms/div  ·  1 Hz square-wave test')
        self.reference_label.setObjectName('referenceLabel')
        reference_row.addWidget(self.reference_label, 1)
        self.reference_status = QLabel('Select TEK PRESET to compare live CH1 readings')
        self.reference_status.setObjectName('referenceLabel')
        self.reference_status.setAlignment(Qt.AlignRight)
        self.reference_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        reference_row.addWidget(self.reference_status, 1)
        outer.addWidget(reference)
        self.scale_audit = QFrame()
        self.scale_audit.setObjectName('auditBar')
        audit_row = QHBoxLayout(self.scale_audit)
        audit_row.setContentsMargins(12, 6, 12, 6)
        self.scale_audit_label = QLabel('SCALE CHECK  ·  Waiting for hardware capture')
        self.scale_audit_label.setObjectName('auditText')
        self.scale_audit_label.setWordWrap(True)
        audit_row.addWidget(self.scale_audit_label, 1)
        audit_row.addWidget(self.reset_gain_button)
        outer.addWidget(self.scale_audit)

        instrument = QFrame()
        instrument.setObjectName('instrument')
        outer.addWidget(instrument, 1)
        body = QHBoxLayout(instrument)
        body.setContentsMargins(10, 10, 10, 10)
        body.setSpacing(10)
        screen = QVBoxLayout()
        screen.setSpacing(8)
        body.addLayout(screen, 1)
        top = QFrame()
        top.setObjectName('readoutBar')
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(10, 7, 10, 7)
        self.top_status = QLabel('WAITING')
        self.top_status.setObjectName('scopeReadout')
        top_l.addWidget(self.top_status, 1)
        self.plot_view = QComboBox()
        self.plot_view.setMinimumWidth(160)
        self.plot_view.addItem('All overlaid', 'overlay')
        self.plot_view.addItem('All stacked', 'stacked')
        self.plot_view.setToolTip('Select one channel or show the checked channels together.')
        self.plot_view.currentIndexChanged.connect(self.plot_view_changed)
        top_l.addWidget(QLabel('Plot view'))
        top_l.addWidget(self.plot_view)
        self.capture_label = QLabel('WAITING')
        self.capture_label.setObjectName('scopeReadout')
        top_l.addWidget(self.capture_label)
        screen.addWidget(top)

        pg.setConfigOptions(antialias=True, background=SCREEN, foreground=MUTED)
        self.plot = pg.PlotWidget()
        self.plot.setMinimumHeight(300)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.setBackground(SCREEN)
        self.plot.hideAxis('left')
        self.roll_axis = self.plot.getAxis('bottom')
        self.roll_axis.setStyle(tickLength=4, tickTextOffset=7, autoExpandTextSpace=False)
        self.roll_axis.setHeight(31)
        self.plot.hideAxis('bottom')
        self.plot.getViewBox().setDefaultPadding(0)
        self.plot.setYRange(-4, 4, padding=0)
        self.graticule = Graticule()
        self.plot.addItem(self.graticule)
        self.graticule.setZValue(-20)

        def trace_pen(color, width, alpha=1.):
            c = QColor(color)
            c.setAlphaF(alpha)
            pen = pg.mkPen(c, width=width, cosmetic=True)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            return pen

        self.glows = [self.plot.plot([], [], pen=trace_pen(c, 6, .10)) for c in COLORS]
        self.curves = [self.plot.plot([], [], pen=trace_pen(c, 2.5)) for c in COLORS]
        self.sample_dots = [self.plot.plot([], [], pen=None, symbol='o', symbolSize=4, symbolBrush=c, symbolPen=None) for c in COLORS]
        for curve in self.glows + self.curves:
            curve.setClipToView(True)
            curve.setDownsampling(auto=True, method='peak')
            curve.setSkipFiniteCheck(True)
        self.lane_lines = [pg.InfiniteLine(pos, angle=0, movable=False,
                           pen=pg.mkPen(AXES, width=1.3, cosmetic=True)) for pos in (2, 0, -2)]
        for line in self.lane_lines:
            self.plot.addItem(line, ignoreBounds=True)
            line.setZValue(-10)
            line.hide()
        self.lane_labels = [pg.TextItem('', color=c, anchor=(0, 0)) for c in COLORS]
        self.lane_captions = [None] * 4
        for label in self.lane_labels:
            self.plot.addItem(label, ignoreBounds=True)
            label.setZValue(60)
            label.hide()
        self.trigger_line = pg.InfiniteLine(angle=0, movable=True, pen=pg.mkPen(TRIGGER, width=1, style=Qt.DashLine))
        self.trigger_line.sigPositionChangeFinished.connect(self.trigger_dragged)
        self.plot.addItem(self.trigger_line)
        self.trigger_line.hide()
        self.trigger_time = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(TRIGGER, width=1, style=Qt.DotLine))
        self.plot.addItem(self.trigger_time)
        self.trigger_time.hide()
        self.ground_markers = [pg.TextItem(f'{i+1}▸', color=c, anchor=(0, .5)) for i, c in enumerate(COLORS)]
        self.level_marker = pg.TextItem('◂', color=TRIGGER, anchor=(1, .5))
        self.time_marker = pg.TextItem('T', color=TRIGGER, anchor=(.5, 0))
        for marker in self.ground_markers + [self.level_marker, self.time_marker]:
            marker.setZValue(50)
            self.plot.addItem(marker)
            marker.hide()
        self.cursors = []
        cc = QColor(CURSOR)
        cc.setAlphaF(.75)
        for pos, angle in [(0, 90), (.003, 90), (-1, 0), (1, 0)]:
            line = pg.InfiniteLine(pos=pos, angle=angle, movable=True, pen=pg.mkPen(cc, width=1, style=Qt.DashLine), hoverPen=pg.mkPen(CURSOR, width=2))
            line.sigPositionChanged.connect(self.update_cursor)
            self.plot.addItem(line)
            line.hide()
            self.cursors.append(line)
        self.empty = pg.TextItem('No signal', color=MUTED, anchor=(.5, .5))
        self.empty.setFont(QFont(UI_FONTS[0], 16))
        self.plot.addItem(self.empty)
        screen.addWidget(self.plot, 1)
        bottom = QFrame()
        bottom.setObjectName('readoutBar')
        bottom_l = QHBoxLayout(bottom)
        bottom_l.setContentsMargins(10, 7, 10, 7)
        self.bottom_status = QLabel('—')
        self.bottom_status.setObjectName('scopeReadout')
        self.bottom_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bottom_l.addWidget(self.bottom_status, 1)
        self.measure_caption = QLabel('MEAS CH1')
        self.measure_caption.setObjectName('measurementSource')
        bottom_l.addWidget(self.measure_caption)
        self.sample_label = QLabel('—')
        self.sample_label.setObjectName('scopeReadout')
        bottom_l.addWidget(self.sample_label)
        screen.addWidget(bottom)

        metric_row = QHBoxLayout()
        metric_row.setSpacing(5)
        self.metrics = {}
        self.metric_cards = {}
        metric_defs = [('frequency', 'FREQUENCY'), ('period', 'PERIOD'), ('vpp', 'PEAK–PEAK'), ('rms', 'RMS'), ('mean', 'MEAN'), ('minimum', 'MIN'), ('maximum', 'MAX'), ('duty', 'DUTY')]
        for key, title_text in metric_defs:
            card = QFrame()
            card.setObjectName('metric')
            card.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            v = QVBoxLayout(card)
            v.setContentsMargins(7, 6, 7, 6)
            v.setSpacing(4)
            label = QLabel(title_text)
            label.setObjectName('small')
            value = FittedReading('—')
            value.setObjectName('reading')
            v.addWidget(label)
            v.addWidget(value)
            self.metrics[key] = value
            self.metric_cards[key] = card
            metric_row.addWidget(card, 1)
        screen.addLayout(metric_row)

        channel_row = QHBoxLayout()
        channel_row.setSpacing(7)
        self.channel_checks = []
        self.vdivs = []
        self.vdiv_sliders = []
        self.positions = []
        self.position_sliders = []
        self.couplings = []
        self.probes = []
        self.channel_boxes = []
        for i in range(4):
            box = QGroupBox()
            box.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            self.channel_boxes.append(box)
            form = QVBoxLayout(box)
            form.setContentsMargins(7, 7, 7, 7)
            form.setSpacing(4)
            check = QPushButton(f'CH{i+1} (V)')
            check.setObjectName('channelToggle')
            check.setCheckable(True)
            check.setChecked(i == 0)
            check.setStyleSheet(channel_style(COLORS[i]))
            check.toggled.connect(self.channel_toggled)
            form.addWidget(check)
            self.channel_checks.append(check)
            scale = QComboBox()
            scale.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            for val in SCALES:
                scale.addItem(fmt(val, 'V') + '/div', val)
            scale.setCurrentIndex(SCALES.index(1))
            self.vdivs.append(scale)
            scale_slider = QSlider(Qt.Horizontal)
            scale_slider.setRange(0, len(SCALES) - 1)
            scale_slider.setValue(scale.currentIndex())
            self.vdiv_sliders.append(scale_slider)
            scale.currentIndexChanged.connect(lambda idx, i=i: self.scale_combo_changed(i, idx))
            scale_slider.valueChanged.connect(lambda value, i=i: self.vdivs[i].setCurrentIndex(value))
            row = QHBoxLayout()
            row.addWidget(QLabel('SCALE'))
            row.addWidget(scale, 1)
            form.addLayout(row)
            form.addWidget(scale_slider)
            position = spin(0, decimals=5)
            position.setSuffix(' V')
            position.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            self.positions.append(position)
            pslider = QSlider(Qt.Horizontal)
            pslider.setRange(-400, 400)
            self.position_sliders.append(pslider)
            pslider.setToolTip('Vertical position in divisions')
            position.valueChanged.connect(lambda _, i=i: self.position_spin_changed(i))
            pslider.valueChanged.connect(lambda value, i=i: self.position_slider_changed(i, value))
            row = QHBoxLayout()
            row.addWidget(QLabel('POS'))
            row.addWidget(position, 1)
            form.addLayout(row)
            form.addWidget(pslider)
            coupling = SegmentedControl(['DC', 'AC', 'GND'])
            self.couplings.append(coupling)
            coupling.currentTextChanged.connect(lambda _, i=i: self.channel_display_changed(i))
            form.addWidget(coupling)
            probe = combo(['1X', '10X', '100X'])
            self.probes.append(probe)
            probe.setToolTip('Dimensionless probe attenuation. Scale, position and trigger level follow the selected factor.')
            probe.currentTextChanged.connect(lambda _, i=i: self.probe_changed(i))
            row = QHBoxLayout()
            row.addWidget(QLabel('PROBE'))
            row.addWidget(probe, 1)
            form.addLayout(row)
            channel_row.addWidget(box, 1)
        screen.addLayout(channel_row)

        right = QFrame()
        right.setObjectName('softPanel')
        right.setMinimumWidth(268)
        right.setMaximumWidth(292)
        body.addWidget(right)
        rlayout = QVBoxLayout(right)
        rlayout.setContentsMargins(10, 10, 10, 10)
        rlayout.setSpacing(8)
        self.run_button = QPushButton('RUN')
        self.run_button.setObjectName('run')
        self.run_button.clicked.connect(self.toggle_run)
        rlayout.addWidget(self.run_button)
        row = QHBoxLayout()
        self.single_button = QPushButton('SINGLE')
        self.single_button.clicked.connect(self.single)
        self.auto_button = QPushButton('AUTOSET')
        self.auto_button.setObjectName('primary')
        self.auto_button.clicked.connect(self.autoset)
        row.addWidget(self.single_button)
        row.addWidget(self.auto_button)
        rlayout.addLayout(row)
        nav = QGridLayout()
        nav.setSpacing(4)
        self.menu_buttons = {}
        for n, name in enumerate(['ACQUIRE', 'HORIZONTAL', 'TRIGGER', 'CURSOR', 'MEASURE', 'SAVE']):
            button = QPushButton(name.title())
            button.setObjectName('softKey')
            button.clicked.connect(lambda _, name=name: self.show_menu(name))
            nav.addWidget(button, n // 3, n % 3)
            self.menu_buttons[name] = button
        rlayout.addLayout(nav)
        self.menu_title = QLabel('ACQUIRE')
        self.menu_title.setObjectName('menuTitle')
        rlayout.addWidget(self.menu_title)
        self.menu_stack = QStackedWidget()
        self.menu_pages = {}
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.menu_stack)
        rlayout.addWidget(scroll, 1)

        def page(name):
            w = QWidget()
            w.setObjectName('menuPage')
            lay = QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(8)
            self.menu_stack.addWidget(w)
            self.menu_pages[name] = w
            return lay

        lay = page('ACQUIRE')
        self.acquisition_mode = combo(['Sample', 'Average', 'Hi-Res'])
        self.average_n = combo(['2', '4', '8', '16', '32', '64'])
        self.average_n.setCurrentText('16')
        self.hires_width = combo(['2', '4', '8', '16'])
        self.hires_width.setCurrentText('4')
        self.interpolation = combo(['PWM steps (measured)', 'Linear', 'Sin(x)/x'])
        for label, w in [('Acquisition mode', self.acquisition_mode), ('Average records', self.average_n), ('Hi-Res samples', self.hires_width), ('Display interpolation', self.interpolation)]:
            lay.addWidget(QLabel(label))
            lay.addWidget(w)
        self.acquisition_mode.currentTextChanged.connect(self.acquisition_changed)
        self.average_n.currentTextChanged.connect(self.acquisition_changed)
        self.hires_width.currentTextChanged.connect(self.acquisition_changed)
        self.interpolation.currentTextChanged.connect(self.draw_capture)
        self.tek_screen_check = QCheckBox('Tektronix dark signal screen')
        self.tek_screen_check.setToolTip('Change the graph presentation only; does not change hardware readings or calibration.')
        self.tek_screen_check.setChecked(False)
        self.tek_screen_check.toggled.connect(self.update_scope_screen)
        lay.addWidget(self.tek_screen_check)
        self.autorange_box = QCheckBox('Continuous autoscale')
        lay.addWidget(self.autorange_box)
        self.initial_autofit = QCheckBox('Fit first hardware capture to screen')
        self.initial_autofit.setChecked(True)
        self.initial_autofit.setToolTip('Automatically centers/scales the first real captured waveform. Does not change measurements.')
        lay.addWidget(self.initial_autofit)
        self.processing_note = QLabel('PWM steps represent measured discrete samples; they do not improve ADC time resolution.')
        self.processing_note.setObjectName('softNote')
        self.processing_note.setWordWrap(True)
        lay.addWidget(self.processing_note)
        lay.addStretch()
        lay = page('HORIZONTAL')
        self.tdiv = QComboBox()
        for val in TIME_SCALES:
            self.tdiv.addItem(fmt(val, 's') + '/div', val)
        self.tdiv.setCurrentIndex(TIME_SCALES.index(.5))
        self.time_slider = QSlider(Qt.Horizontal)
        self.time_slider.setRange(0, len(TIME_SCALES) - 1)
        self.time_slider.setValue(self.tdiv.currentIndex())
        self.tdiv.currentIndexChanged.connect(self.time_combo_changed)
        self.time_slider.valueChanged.connect(self.tdiv.setCurrentIndex)
        self.pan = spin(0, decimals=6)
        self.pan.setSuffix(' s')
        self.pan_slider = QSlider(Qt.Horizontal)
        self.pan_slider.setRange(-400, 400)
        self.pan.valueChanged.connect(self.pan_spin_changed)
        self.pan_slider.valueChanged.connect(self.pan_slider_changed)
        for w in [QLabel('Time / division'), self.tdiv, self.time_slider, QLabel('Horizontal position'), self.pan, self.pan_slider]:
            lay.addWidget(w)
        lay.addStretch()
        lay = page('TRIGGER')
        self.mode = combo(['Auto', 'Normal', 'Roll'])
        self.source = combo(['CH1', 'CH2', 'CH3', 'CH4'])
        self.slope = combo(['Rising', 'Falling'])
        self.level = spin(0)
        self.level.setSuffix(' V')
        self.trigger_slider = QSlider(Qt.Horizontal)
        self.trigger_slider.setRange(-400, 400)
        self.mode.currentTextChanged.connect(self.scan_mode_changed)
        self.source.currentIndexChanged.connect(self.trigger_changed)
        self.slope.currentTextChanged.connect(self.trigger_changed)
        self.level.valueChanged.connect(self.trigger_changed)
        self.trigger_slider.valueChanged.connect(self.trigger_slider_changed)
        for label, w in [('Mode', self.mode), ('Source', self.source), ('Slope', self.slope), ('Level', self.level)]:
            lay.addWidget(QLabel(label))
            lay.addWidget(w)
        self.mode.setToolTip('Roll moves continuously to the right. Edge triggers use 20% pre-trigger.')
        lay.addWidget(self.trigger_slider)
        lay.addStretch()
        lay = page('CURSOR')
        self.cursor_mode = combo(['Off', 'Time', 'Value', 'Time + Value'])
        self.cursor_mode.currentTextChanged.connect(self.cursor_mode_changed)
        self.reset_cursor_button = QPushButton('Center cursors')
        self.reset_cursor_button.clicked.connect(self.reset_cursors)
        self.cursor_readout = QLabel('—')
        self.cursor_readout.setObjectName('cursor')
        self.cursor_readout.setWordWrap(True)
        for w in [QLabel('Type'), self.cursor_mode, self.reset_cursor_button, self.cursor_readout]:
            lay.addWidget(w)
        lay.addStretch()
        lay = page('MEASURE')
        self.measure_source = combo(['CH1', 'CH2', 'CH3', 'CH4'])
        self.measure_source.currentIndexChanged.connect(self.measure_changed)
        lay.addWidget(QLabel('Source'))
        lay.addWidget(self.measure_source)
        lay.addWidget(QLabel('Visible measurements'))
        self.measure_checks = {}
        for key, label in metric_defs:
            check = QCheckBox(label.title())
            check.setChecked(True)
            check.toggled.connect(self.metric_cards[key].setVisible)
            self.measure_checks[key] = check
            lay.addWidget(check)
        lay.addStretch()
        lay = page('SAVE')
        self.csv_button = QPushButton('Export CSV')
        self.csv_button.clicked.connect(self.export_csv)
        self.png_button = QPushButton('Screenshot')
        self.png_button.clicked.connect(self.export_png)
        self.help_button = QPushButton('Connection guide')
        self.help_button.clicked.connect(self.help)
        self.csv_button.setToolTip('Exports raw calibrated capture samples, before acquisition averaging, Hi-Res, coupling, probe scaling or display interpolation.')
        for w in [self.csv_button, self.png_button, self.help_button]:
            lay.addWidget(w)
        lay.addStretch()
        footer = QHBoxLayout()
        self.notice = QLabel('UI — fps · Frames 0 · Gaps 0 · Invalid 0 · Drops 0')
        self.notice.setObjectName('notice')
        footer.addWidget(self.notice, 1)
        self.error_badge = QLabel()
        self.error_badge.setObjectName('errorBadge')
        self.error_badge.hide()
        footer.addWidget(self.error_badge)
        outer.addLayout(footer)
        self.show_menu('ACQUIRE')
        self.set_run_state(False)
        self.acquisition_changed()

    def update_scope_screen(self, *_):
        """!
        @brief Switches the plot canvas between light theme and Tektronix dark screen colors.
        """
        dark = bool(self.tek_screen_check.isChecked())
        self.plot.setBackground('#0B1728' if dark else SCREEN)
        self.graticule.set_dark(dark)
        self.roll_axis.setTextPen('#BDCEDD' if dark else '#334B63')
        self.roll_axis.setPen('#627E97' if dark else AXES)
        palette = ['#FFE569', '#65E4F0', '#E98BFA', '#79EFA9'] if dark else COLORS
        for i, color in enumerate(palette):
            self.curves[i].setPen(pg.mkPen(color, width=2.3, cosmetic=True))
            glow = QColor(color)
            glow.setAlphaF(.16 if dark else .10)
            self.glows[i].setPen(pg.mkPen(glow, width=5.5, cosmetic=True))
            self.ground_markers[i].setColor(color)
            self.lane_labels[i].setColor(color)
        self.empty.setColor('#A9C2D8' if dark else MUTED)
        self.draw_capture()

    def theme(self):
        """!
        @brief Applies the global application stylesheet.
        """
        self.setStyleSheet(stylesheet())

    def show_menu(self, name):
        """!
        @brief Switches the right-hand softkey menu page and highlights the active tab button.
        @param name Menu page key (`'ACQUIRE'`, `'HORIZONTAL'`, `'TRIGGER'`, `'CURSOR'`, `'MEASURE'`, `'SAVE'`).
        """
        if name not in self.menu_pages:
            return
        self.menu_title.setText(name)
        self.menu_stack.setCurrentWidget(self.menu_pages[name])
        for key, button in self.menu_buttons.items():
            button.setProperty('selected', key == name)
            repolish(button)

    def probe_factor(self, i):
        """!
        @brief Returns the numeric probe attenuation multiplier (`1.0`, `10.0`, or `100.0`) for channel @p i.
        @param i Zero-based channel index.
        @return Attenuation multiplier float.
        """
        if i >= len(self.probes):
            return 1.0
        text = self.probes[i].currentText().upper().rstrip('X')
        try:
            return float(text)
        except ValueError:
            return 1.0

    def displayed_channel(self, i):
        """!
        @brief Returns the processed, coupled, and probe-scaled trace array for channel @p i.
        @param i Zero-based channel index.
        @return 1D numpy float array, or None if no capture is active.
        """
        if self.current is None or i >= self.current.y.shape[1]:
            return None
        data = self.processed_y if self._processed_capture is self.current and self.processed_y is not None else self.current.y
        y = data[:, i].astype(float, copy=True)
        coupling = self.couplings[i].currentText()
        if coupling == 'AC' and len(y):
            y -= self.ac_reference(i)
        elif coupling == 'GND':
            y[np.isfinite(y)] = 0.
        return y * self.probe_factor(i)

    def ac_reference(self, ch):
        """!
        @brief Retrieves the DC baseline subtracted during AC coupling for channel @p ch.
        @param ch Zero-based channel index.
        @return Baseline offset float.
        """
        # During capture the exact reference passed to the trigger engine is held
        # with that record. Held captures must not drift with later buffer data.
        if self._processed_capture is self.current and self._capture_ac_reference is not None and ch < len(self._capture_ac_reference):
            return float(self._capture_ac_reference[ch])
        return self.latest_reference(ch)

    def latest_reference(self, ch):
        """!
        @brief Computes the mean DC level over the latest 10-division buffer span for AC coupling.
        @param ch Zero-based channel index.
        @return Mean voltage float.
        """
        if self.engine.y is not None and len(self.engine.t) and ch < self.engine.y.shape[1]:
            low = self.engine.t[-1] - 10 * self.tdiv.currentData()
            first = np.searchsorted(self.engine.t, low, side='left')
            values = self.engine.y[first:, ch]
            valid = values[np.isfinite(values)]
            return float(np.mean(valid)) if len(valid) else 0.
        if self.current is not None and len(self.current.y) and ch < self.current.y.shape[1]:
            values = self.current.y[:, ch]
            valid = values[np.isfinite(values)]
            return float(np.mean(valid)) if len(valid) else 0.
        return 0.

    def accept_capture(self, capture, references=None):
        """!
        @brief Stores a new @ref Capture record, runs acquisition processing, and updates axes if needed.
        @param capture Newly triggered or rolled @ref Capture instance.
        @param references Optional per-channel AC baseline array captured at trigger time.
        """
        previous_layout = self.uses_roll_axis()
        self.current = capture
        self._capture_ac_reference = np.array(references if references is not None else [self.latest_reference(i) for i in range(capture.y.shape[1])])
        self.processed_y = self.processor.process(capture, self.acquisition_mode.currentText(), int(self.average_n.currentText()), int(self.hires_width.currentText()))
        self._processed_capture = capture
        if previous_layout != self.uses_roll_axis():
            self.update_axes()

    def reset_processing(self):
        """!
        @brief Resets multi-record averaging state when acquisition or vertical controls change.
        """
        self.processor.reset()
        # Settings invalidate averaging history, not the record held on screen.
        if self.current is None:
            self.processed_y = None
            self._processed_capture = None
            self._capture_ac_reference = None

    def acquisition_changed(self, *_):
        """!
        @brief Slot triggered when acquisition mode, average count, or Hi-Res width changes.
        """
        self.reset_processing()
        mode = self.acquisition_mode.currentText()
        self.average_n.setEnabled(mode == 'Average')
        self.hires_width.setEnabled(mode == 'Hi-Res')
        if self.current is not None:
            self.accept_capture(self.current, self._capture_ac_reference)
        self.draw_capture()
        self.update_measurements()

    def channel_toggled(self, *_):
        """!
        @brief Slot triggered when a channel visibility button is toggled.
        """
        self.controls_enabled(self.link_open and self.metadata is not None)
        self.draw_capture()

    def probe_changed(self, i):
        """!
        @brief Rescales vertical scale, offset, and trigger level proportionally when probe attenuation changes.
        @param i Zero-based channel index.
        """
        old = self._probe_factors[i]
        new = self.probe_factor(i)
        ratio = new / old
        self._probe_factors[i] = new
        target = self.vdivs[i].currentData() * ratio
        idx = min(range(len(SCALES)), key=lambda j: abs(math.log(SCALES[j] / target)))
        self.vdivs[i].blockSignals(True)
        self.vdivs[i].setCurrentIndex(idx)
        self.vdivs[i].blockSignals(False)
        self.vdiv_sliders[i].blockSignals(True)
        self.vdiv_sliders[i].setValue(idx)
        self.vdiv_sliders[i].blockSignals(False)
        self.positions[i].blockSignals(True)
        self.positions[i].setValue(self.positions[i].value() * ratio)
        self.positions[i].blockSignals(False)
        if self.source.currentIndex() == i:
            self.level.blockSignals(True)
            self.level.setValue(self.level.value() * ratio)
            self.level.blockSignals(False)
        self.reset_processing()
        self.position_spin_changed(i)
        self.update_measurements()
        self.update_trigger_slider()

    def set_run_state(self, running, single=False):
        """!
        @brief Updates the RUN/STOP button styling and top status badge.
        @param running True if acquisition is actively capturing.
        @param single True if armed for a single-shot trigger.
        """
        self.running = bool(running)
        self.single_pending = bool(single and running)
        self.run_button.setText('STOP' if running else 'RUN')
        self.run_button.setProperty('running', bool(running))
        repolish(self.run_button)
        if running:
            status = 'Armed' if single else 'Running'
        elif self.metadata is None or not self.link_open:
            status = 'Disconnected'
        elif self.current is None:
            status = 'Ready'
        else:
            status = 'Stopped'
        self.state.setText('●  ' + status)
        self.state.setProperty('state', status.lower())
        repolish(self.state)

    def scale_combo_changed(self, i, idx):
        """!
        @brief Synchronizes vertical scale slider and redraws when V/div dropdown changes.
        @param i Zero-based channel index.
        @param idx Selected index in @ref SCALES.
        """
        self.reset_processing()
        if i < len(self.vdiv_sliders):
            self.vdiv_sliders[i].blockSignals(True)
            self.vdiv_sliders[i].setValue(idx)
            self.vdiv_sliders[i].blockSignals(False)
            self.position_spin_changed(i)
            self.update_trigger_slider()
        self.draw_capture()

    def position_slider_changed(self, i, value):
        """!
        @brief Updates vertical position spinbox from the division slider (`value / 100.0` divs).
        @param i Zero-based channel index.
        @param value Integer slider value in [-400, 400].
        """
        if i >= len(self.positions):
            return
        target = (value / 100.0) * self.vdivs[i].currentData()
        self.positions[i].blockSignals(True)
        self.positions[i].setValue(target)
        self.positions[i].blockSignals(False)
        self.draw_capture()
        self.update_trigger_slider()

    def position_spin_changed(self, i):
        """!
        @brief Synchronizes vertical position slider when the position spinbox changes.
        @param i Zero-based channel index.
        """
        if i >= len(self.position_sliders):
            return
        scale = self.vdivs[i].currentData() or 1.0
        slider = int(round(100 * self.positions[i].value() / scale))
        slider = max(-400, min(400, slider))
        self.position_sliders[i].blockSignals(True)
        self.position_sliders[i].setValue(slider)
        self.position_sliders[i].blockSignals(False)
        self.draw_capture()
        self.update_trigger_slider()

    def channel_display_changed(self, i):
        """!
        @brief Handles channel coupling changes by resetting averaging and updating measurements.
        @param i Zero-based channel index.
        """
        self.reset_processing()
        self.draw_capture()
        self.update_measurements()
        self.update_trigger_slider()

    def time_combo_changed(self, idx):
        """!
        @brief Synchronizes timebase slider and updates horizontal axes when s/div changes.
        @param idx Selected index in @ref TIME_SCALES.
        """
        if hasattr(self, 'time_slider'):
            self.time_slider.blockSignals(True)
            self.time_slider.setValue(idx)
            self.time_slider.blockSignals(False)
        self.time_changed()
        self.sync_pan_slider()
        self.update_status_readouts()

    def pan_slider_changed(self, value):
        """!
        @brief Updates horizontal pan offset spinbox from the horizontal slider.
        @param value Integer slider position in [-400, 400].
        """
        target = (value / 100.0) * self.tdiv.currentData()
        self.pan.blockSignals(True)
        self.pan.setValue(target)
        self.pan.blockSignals(False)
        self.update_axes()

    def pan_spin_changed(self, _value=None):
        """!
        @brief Synchronizes horizontal pan slider and updates plot viewport range.
        """
        self.sync_pan_slider()
        self.update_axes()

    def sync_pan_slider(self):
        """!
        @brief Aligns the horizontal pan slider thumb with the current `pan` spinbox value.
        """
        if not hasattr(self, 'pan_slider'):
            return
        scale = self.tdiv.currentData() or 1.0
        value = int(round(100 * self.pan.value() / scale))
        value = max(-400, min(400, value))
        self.pan_slider.blockSignals(True)
        self.pan_slider.setValue(value)
        self.pan_slider.blockSignals(False)

    def trigger_slider_changed(self, value):
        """!
        @brief Updates trigger level spinbox from the trigger slider relative to source channel position.
        @param value Integer slider position in [-400, 400].
        """
        if not hasattr(self, 'source') or not self.vdivs:
            return
        ch = max(0, self.source.currentIndex())
        ch = min(ch, len(self.vdivs) - 1)
        level = self.positions[ch].value() + (value / 100.0) * self.vdivs[ch].currentData()
        self.level.blockSignals(True)
        self.level.setValue(level)
        self.level.blockSignals(False)
        self.trigger_changed()

    def update_trigger_slider(self):
        """!
        @brief Synchronizes the trigger slider thumb with the current trigger voltage level.
        """
        if not hasattr(self, 'trigger_slider') or not hasattr(self, 'source'):
            return
        ch = max(0, self.source.currentIndex())
        ch = min(ch, len(self.vdivs) - 1)
        scale = self.vdivs[ch].currentData() or 1.0
        value = int(round(100 * (self.level.value() - self.positions[ch].value()) / scale))
        value = max(-400, min(400, value))
        self.trigger_slider.blockSignals(True)
        self.trigger_slider.setValue(value)
        self.trigger_slider.blockSignals(False)

    def trigger_engine_level(self):
        """!
        @brief Converts the probe-scaled UI trigger voltage into raw calibrated channel units for @ref CaptureEngine.
        @return Raw trigger threshold float (or `inf` if source channel is GND-coupled).
        """
        ch = max(0, self.source.currentIndex())
        factor = self.probe_factor(ch)
        if self.couplings[ch].currentText() == 'GND':
            return float('inf')
        raw = self.level.value() / factor
        if self.couplings[ch].currentText() == 'AC':
            raw += self.latest_reference(ch)
        return raw

    def update_status_readouts(self):
        """!
        @brief Refreshes the top and bottom scope telemetry bars with trigger, timebase, and scale readouts.
        """
        if not hasattr(self, 'tdiv') or not hasattr(self, 'level'):
            return
        if self.single_pending:
            trig = 'ARMED'
        elif self.current is None:
            trig = 'READY' if self.metadata else 'WAITING'
        elif not self.running:
            trig = 'HOLD'
        else:
            trig = "TRIG'D" if self.current.triggered else 'ROLL →' if self.current.roll else 'AUTO'
        acquisition = self.acquisition_mode.currentText()
        if acquisition == 'Average':
            acquisition += f' {self.processor.count}/{self.average_n.currentText()}'
        if self.uses_roll_axis():
            status = f'{trig}  ·  Newest at left  ·  {fmt(self.tdiv.currentData(),"s")}/div  ·  {acquisition}'
        else:
            status = f'{trig}    H {fmt(self.pan.value(),"s")}    {self.mode.currentText()} · {acquisition}'
        if self.top_status.text() != status:
            self.top_status.setText(status)
        ch = max(0, self.source.currentIndex())
        unit = self.channels[ch].unit
        freq = self._display_frequency if ch == self._measure_channel else None
        channels = '  '.join(f'CH{i+1}: {fmt(self.vdivs[i].currentData(),self.channels[i].unit)}/div'
                             for i in range(len(self.metadata.channels) if self.metadata else 4)
                             if self.channel_is_plotted(i))
        text = f'{channels}   {fmt(self.tdiv.currentData(),"s")}/div'
        if not self.uses_roll_axis():
            text += f'   T: CH{ch+1} {fmt(self.level.value(),unit)}   {fmt(freq,"Hz")}'
        if self.bottom_status.text() != text:
            self.bottom_status.setText(text)
            self.bottom_status.setToolTip(text)

    def transport_changed(self):
        """!
        @brief Switches connection fields between Serial COM ports/baud rates and TCP host/port defaults.
        """
        tcp = self.transport.currentText() == 'TCP'
        self.refresh.setEnabled(not tcp)
        self.port.clear()
        self.rate.clear()
        if tcp:
            self.port.addItem('127.0.0.1')
            self.rate.addItem('9000')
        else:
            self.rate.addItems(['921600', '115200', '230400', '460800', '1000000', '2000000'])
            self.refresh_ports()
        self.rate_label.setText('Port' if tcp else 'Baud')

    def refresh_ports(self):
        """!
        @brief Enumerates available system serial ports using `pyserial` and populates the port dropdown.
        """
        old = self.port.currentText()
        self.port.clear()
        try:
            from serial.tools.list_ports import comports
            self.port.addItems([p.device for p in comports()])
        except ImportError:
            pass
        if old:
            self.port.setCurrentText(old)
        self.port.setPlaceholderText('Select COM port')

    def controls_enabled(self, ready):
        """!
        @brief Enables or disables channel, trigger, and export controls based on link and capture readiness.
        @param ready True when device metadata has been received and link is open.
        """
        if not hasattr(self, 'run_button'):
            return
        # RUN is available after the serial port opens, even before HELLO arrives.
        # Pressing it arms capture pending device metadata; no synthetic samples.
        self.run_button.setEnabled(ready or (self.link_open and self.reader is not None))
        self.single_button.setEnabled(ready)
        for i in range(4):
            present = self.metadata is not None and i < len(self.metadata.channels)
            self.channel_checks[i].setEnabled(present)
            active = present and self.channel_checks[i].isChecked()
            for w in [self.vdivs[i], self.vdiv_sliders[i], self.positions[i], self.position_sliders[i], self.couplings[i], self.probes[i]]:
                w.setEnabled(active)
        has = self.current is not None and len(self.current.t) > 0
        for w in [self.auto_button, self.csv_button, self.cursor_mode, self.reset_cursor_button, self.calibration_button]:
            w.setEnabled(has)

    def toggle_connection(self):
        """!
        @brief Connects to or disconnects from the configured Serial or TCP acquisition target.
        """
        if self.reader is not None:
            self.disconnect()
            return
        try:
            address = self.port.currentText().strip()
            rate = int(self.rate.currentText())
            if not address or rate <= 0 or (self.transport.currentText() == 'TCP' and rate > 65535):
                raise ValueError()
        except ValueError:
            QMessageBox.warning(self, 'Connection', 'Select a port/address and a valid baud rate or TCP port.')
            return
        self.clear_capture()
        self.engine.clear()
        self.metadata = None
        self.gaps = self.frames = 0
        self.last_rx = 0
        self._device_samples = 0
        self._ui_fps = 0.
        self._last_measure_update = 0.
        self._last_notice_update = 0.
        self.calibration_note = ''
        self.device_status = ''
        self.pending_run = True
        self.connected_since = time.monotonic()
        self.connection_hint.hide()
        self.initial_fit_pending = True
        self.reader = Reader(self.transport.currentText(), address, rate)
        self.reader.start()
        self.connect_button.setText('Disconnect')
        self.state.setText('●  CONNECTING')
        for w in [self.port, self.rate, self.transport, self.refresh]:
            w.setEnabled(False)
        self.controls_enabled(False)

    def disconnect(self, error=None):
        """!
        @brief Stops the background @ref Reader thread and resets UI controls to disconnected state.
        @param error Optional error message string to display on the footer error badge.
        """
        if self.reader:
            self.reader.close()
        self.reader = None
        self.link_open = False
        self.pending_run = False
        self.connected_since = 0.
        self.device_status = ''
        self.connection_hint.hide()
        self.set_run_state(False)
        self.connect_button.setText('Connect')
        self.capture_label.setText('HOLD' if self.current else 'WAITING')
        self.error_badge.setText('ERROR' if error else '')
        self.error_badge.setToolTip(error or '')
        self.error_badge.setVisible(bool(error))
        for w in [self.port, self.rate, self.transport]:
            w.setEnabled(True)
        self.refresh.setEnabled(self.transport.currentText() == 'Serial')
        self.controls_enabled(False)
        self.update_status_readouts()

    def accept_metadata(self, meta):
        """!
        @brief Configures channel names, units, presets, and auto-arming upon receiving a device @ref Metadata packet.
        @param meta Validated @ref Metadata object from the hardware 'hello' handshake.
        """
        if meta == self.metadata:
            return
        auto_arm = self.pending_run or (self.running and self.link_open)
        sensor_device = meta.device == 'ESP32 Four Sensors'
        new_sensor_device = sensor_device and (self.metadata is None or self.metadata.device != meta.device)
        if sensor_device:
            previous = {}
            if self.legacy_channels is None:
                self.legacy_channels = self.channels
            elif self.metadata and self.metadata.device == meta.device:
                previous = {raw['name']: channel for raw, channel in zip(self.metadata.channels, self.channels)}
            self.channels = [Channel(i) for i in range(4)]
            for i, raw in enumerate(meta.channels):
                self.channels[i] = previous.get(raw['name'], self.channels[i])
        elif self.legacy_channels is not None:
            self.channels, self.legacy_channels = self.legacy_channels, None
            self.reference_mode = 'tek'
            self.reference_active = False
            self.reference_label.setText('TEK REFERENCE · 1 Hz')
        self.pending_run = False
        self.connection_hint.hide()
        self.last_rx = 0.
        self.metadata = meta
        self.engine.clear()
        self.clear_capture()
        self.running = False
        self.single_pending = False
        # Both shipped PWM sketches transmit volts at GPIO25 after reconstructing
        # the GPIO34 1k/1k divider. A legacy 686x gain would falsify voltage.
        known_pwm = meta.device.upper() in (
            'ESP32 POT TO PWM SCOPE', 'ESP32 POTENTIOMETER PWM DSO',
            'ESP32 POTENTIOMETER PWM 100 HZ',
        )
        if known_pwm and suspicious_gain(self.channels[0].gain):
            previous_gain = self.channels[0].gain
            self.channels[0].gain = 1.0
            self.channels[0].offset = 0.0
            self.calibration_note = f'Reset obsolete CH1 gain ×{previous_gain:.5g}; firmware sends volts.'
            self.save_profiles()
        self.initial_scale = True
        self.initial_fit_pending = True
        self.device_label.setText(meta.device)
        self.profile_button.setToolTip('Device & sensors · ' + meta.device)
        self.capture_label.setText('READY')
        self.link_open = True
        self.set_run_state(False)
        for i, raw in enumerate(meta.channels):
            ch = self.channels[i]
            if ch.sensor == 'Device units':
                ch.name, ch.unit = raw['name'], raw['unit']
        self.source.clear()
        self.measure_source.clear()
        self.source.addItems([f'CH{i+1}' for i in range(len(meta.channels))])
        self.measure_source.addItems([f'CH{i+1}' for i in range(len(meta.channels))])
        for i, check in enumerate(self.channel_checks):
            check.setChecked(i < len(meta.channels))
        self.channel_labels()
        self.controls_enabled(True)
        self.update_status_readouts()
        if self.calibration_note:
            self.connection_hint.setText(self.calibration_note)
            self.connection_hint.show()
        if sensor_device:
            self.apply_sensors_preset()
            if new_sensor_device:
                self.plot_view.setCurrentIndex(self.plot_view.findData('stacked'))
        elif 'PWM' in meta.device.upper():
            self.apply_pwm_preset()
        if auto_arm:
            self.arm(False)

    def channel_labels(self):
        """!
        @brief Updates channel toggle labels, scale dropdown unit strings, and Plot View options.
        """
        for i, ch in enumerate(self.channels):
            ident = f'CH{i+1}'
            label = ident if ch.name.strip().upper() == ident else f'{ident} · {ch.name}'
            self.channel_checks[i].setText(f'{label} ({ch.unit})')
            self.channel_checks[i].setToolTip(f'{label} ({ch.unit})')
            for j, v in enumerate(SCALES):
                self.vdivs[i].setItemText(j, fmt(v, ch.unit) + '/div')
            self.positions[i].setSuffix(' ' + ch.unit)
        self.level.setSuffix(' ' + self.channels[max(0, self.source.currentIndex())].unit)
        selection = self.plot_view.currentData()
        self.plot_view.blockSignals(True)
        self.plot_view.clear()
        self.plot_view.addItem('All overlaid', 'overlay')
        self.plot_view.addItem('All stacked', 'stacked')
        for i in range(len(self.metadata.channels) if self.metadata else 0):
            self.plot_view.addItem(f'CH{i+1} · {self.channels[i].name}', i)
        self.plot_view.setCurrentIndex(max(0, self.plot_view.findData(selection)))
        self.plot_view.blockSignals(False)
        self.draw_capture()

    def plot_view_changed(self, *_):
        """!
        @brief Handles Plot View selection changes (All overlaid, All stacked, or single channel).
        """
        selection = self.plot_view.currentData()
        if type(selection) is int:
            self.measure_source.setCurrentIndex(selection)
        self.draw_capture()
        self.update_cursor()

    def channel_is_plotted(self, index):
        """!
        @brief Checks whether channel @p index should currently be rendered on the plot.
        @param index Zero-based channel index.
        @return True if visible in the active Plot View mode.
        """
        selection = self.plot_view.currentData()
        return index == selection if type(selection) is int else self.channel_checks[index].isChecked()

    def poll(self):
        """!
        @brief Periodic 16 ms GUI timer callback draining the reader queue, triggering captures, and updating diagnostics.
        """
        reader = self.reader
        if reader is None:
            return
        for _ in range(256):
            try:
                kind, item = reader.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'error':
                self.disconnect(item)
                QMessageBox.warning(self, 'Hardware connection', item)
                return
            if kind == 'connected':
                self.connected_since = time.monotonic()
                self.link_open = True
                self.state.setText('●  WAITING FOR ESP32')
                self.capture_label.setText('WAITING')
                self.run_button.setText('CANCEL RUN' if self.pending_run else 'RUN')
                self.controls_enabled(False)
            if kind == 'data':
                if isinstance(item, Metadata):
                    self.accept_metadata(item)
                elif isinstance(item, DeviceStatus):
                    self.device_status = item.code
                elif isinstance(item, Frame):
                    self.last_rx = time.monotonic()
                    self.frames += 1
                    self._device_samples += len(item.t)
                    if item.gap:
                        self.gaps += 1
                        self.reset_processing()
                    if self.running:
                        y = item.y.copy()
                        for i in range(y.shape[1]):
                            y[:, i] = y[:, i] * self.channels[i].gain + self.channels[i].offset
                        if not np.isfinite(y[np.isfinite(item.y)]).all():
                            self.disconnect('Calibration overflow; reduce gain/offset')
                            return
                        keep = 20 * self.tdiv.currentData() if self.mode.currentText() == 'Roll' and not self.single_pending else None
                        self.engine.append(Frame(item.t, y, item.seq, item.gap, item.clipped), keep_seconds=keep)
        now = time.monotonic()
        if self.running and self.metadata:
            if self.initial_scale and len(self.engine.t) >= 2:
                dt = float(np.median(np.diff(self.engine.t)))
                if self.tdiv.currentData() < dt * 2:
                    choice = next((v for v in TIME_SCALES if v >= dt * 2), TIME_SCALES[-1])
                    self.tdiv.blockSignals(True)
                    self.tdiv.setCurrentIndex(TIME_SCALES.index(choice))
                    self.tdiv.blockSignals(False)
                    self.time_slider.blockSignals(True)
                    self.time_slider.setValue(self.tdiv.currentIndex())
                    self.time_slider.blockSignals(False)
                    self.update_axes()
                self.initial_scale = False
            auto = now - self.last_capture >= (1 / 60 if self.mode.currentText() == 'Roll' else .18)
            references = [self.latest_reference(i) if self.couplings[i].currentText() == 'AC' else 0.
                          for i in range(len(self.metadata.channels))]
            capture = self.engine.capture(10 * self.tdiv.currentData(), max(0, self.source.currentIndex()), self.trigger_engine_level(), self.slope.currentText(), 'Normal' if self.single_pending else self.mode.currentText(), auto, prefer_newest=not self.single_pending)
            if capture is not None:
                first_capture = self.current is None
                self.accept_capture(capture, references)
                self.last_capture = now
                if self.initial_fit_pending:
                    self.initial_fit_pending = False
                    if self.initial_autofit.isChecked():
                        self.fit_first_capture()
                elif self.autorange_box.isChecked():
                    self.autorange_capture()
                self.draw_capture()
                if now - self._last_measure_update >= 0.095 or self.single_pending:
                    self.update_measurements()
                    self._last_measure_update = now
                if first_capture or self.single_pending:
                    self.controls_enabled(True)
                if self.single_pending:
                    self.set_run_state(False)
                    self.capture_label.setText('STOP')
                else:
                    self.capture_label.setText("TRIG'D" if capture.triggered else
                                                 'ROLL' if self.mode.currentText() == 'Roll' else 'AUTO')
            elif now - self.last_capture > 1:
                self.capture_label.setText('ARMED' if self.single_pending or self.mode.currentText() == 'Normal' else 'WAITING')
        # Show actionable connection diagnostics instead of an unexplained grey RUN.
        if self.link_open and self.metadata is None and now - self.connected_since > 1.5:
            selected = self.rate.currentText()
            if reader.invalid:
                msg = (f'No valid ESP32 HELLO received at {selected} baud (invalid messages: {reader.invalid}). '
                       'For the included four-sensor or PWM sketch select 921600, then Disconnect / Connect. '
                       'Ensure Arduino Serial Monitor is closed.')
            else:
                msg = (f'COM port opened at {selected} baud, but device identification has not arrived. '
                       'Upload the four-sensor sketch and select 921600 baud. Press ESP32 EN once; '
                       'check firmware/upload and USB cable if still waiting.')
            self.connection_hint.setText(msg)
            self.connection_hint.show()
        elif self.metadata is not None:
            messages = {
                'dht11_waiting': 'CH2 DHT11 pending · Check DATA on GPIO27, power and pull-up. CH1, CH3 and CH4 continue acquiring.',
                'ads1115_missing': 'ADS1115 unavailable · Check SDA GPIO21, SCL GPIO22 and ADDR to GND, or upload with USE_ADS1115 = false for direct wiring.',
                'pwm_unavailable': 'LED PWM could not start on GPIO25. CH2 voltage capture is active.',
            }
            hint = messages.get(self.device_status) or self.calibration_note
            if hint:
                self.connection_hint.setText(hint)
            self.connection_hint.setVisible(bool(hint))
        stale_after = 2.5 if self.reference_mode == 'sensors' else max(3., 20 * self.tdiv.currentData())
        stale = not self.last_rx or now - self.last_rx > stale_after
        if now - self._last_notice_update >= .24:
            rx_age = f'{(now-self.last_rx)*1000:.0f} ms' if self.last_rx else '—'
            self.notice.setText(
                f'UI {self._ui_fps:.0f} fps · Last processed {rx_age} ago · '
                f'Frames {self.frames} · Gaps {self.gaps} · Invalid {reader.invalid} · Drops {reader.dropped}')
            self._last_notice_update = now
        errors = []
        if self.current is not None and self.current.clipped:
            errors.append('ADC LIMIT')
        if self.last_display_clip:
            errors.append('TRACE OFF-SCREEN')
        if suspicious_gain(self.channels[0].gain):
            errors.append('UNVERIFIED GAIN')
        if reader.last_error:
            errors.append('ERROR')
        if reader.invalid:
            errors.append('INVALID')
        if reader.dropped:
            errors.append('DROPS')
        self.error_badge.setText(' · '.join(errors))
        self.error_badge.setToolTip(reader.last_error or 'Acquisition range or data quality warning')
        self.error_badge.setVisible(bool(errors))
        if stale and self.running:
            self.capture_label.setText('WAITING')
        self.update_status_readouts()

    def toggle_run(self):
        """!
        @brief Toggles continuous acquisition between RUN and STOP (or arms pending run before HELLO).
        """
        if not self.link_open:
            return
        if self.metadata is None:
            self.pending_run = not self.pending_run
            self.run_button.setText('CANCEL RUN' if self.pending_run else 'RUN')
            self.state.setText('●  Armed — waiting for device' if self.pending_run else '●  WAITING FOR DEVICE')
            return
        if self.running:
            self.set_run_state(False)
            self.capture_label.setText('STOP')
            self.update_status_readouts()
        else:
            self.arm(False)

    def single(self):
        """!
        @brief Arms the oscilloscope for a single triggered capture event.
        """
        if self.link_open and self.metadata is not None:
            self.arm(True)

    def arm(self, single):
        """!
        @brief Clears old buffer data and starts continuous or single-shot acquisition.
        @param single True for single-shot trigger mode; False for continuous run.
        """
        self.engine.clear()
        self.reset_processing()
        self.last_capture = time.monotonic()
        self.set_run_state(True, single)
        self.capture_label.setText('ARMED' if single else 'RUN')
        self.update_status_readouts()

    def clear_capture(self):
        """!
        @brief Clears the currently displayed waveform and hides trace markers.
        """
        self.current = None
        self.last_display_clip = False
        self.reset_processing()
        self._capture_ac_reference = None
        for curve in self.curves + self.glows + self.sample_dots:
            curve.setData([], [])
        self.empty.show()
        self.trigger_time.hide()
        for marker in self.ground_markers + [self.level_marker, self.time_marker]:
            marker.hide()
        self.update_measurements()
        self.update_cursor()
        self.update_status_readouts()

    def time_changed(self):
        """!
        @brief Resets capture buffer and updates plot axes when time/division changes.
        """
        self.initial_scale = False
        self.engine.clear()
        self.reset_processing()
        self.update_axes()
        self.ensure_cursors_visible()

    def uses_roll_axis(self):
        """!
        @brief Determines whether the horizontal axis should render in right-to-left Roll coordinates.
        @return True when displaying or configured in Roll mode.
        """
        if self.current is not None:
            return self.current.roll
        return self.mode.currentText() == 'Roll' and not self.single_pending

    def scan_mode_changed(self, *_):
        """!
        @brief Slot triggered when trigger mode switches between Auto, Normal, and Roll.
        """
        self.trigger_changed()
        if self.current is None:
            self.update_axes()

    def update_axes(self):
        """!
        @brief Configures horizontal X range, X-axis inversion (for Roll mode), ticks, and graticule window.
        """
        division = self.tdiv.currentData()
        span = 10 * division
        center = self.pan.value()
        rolling = self.uses_roll_axis()
        low, high = (center - span, center) if rolling else (center - .2 * span, center + .8 * span)
        self.plot.getViewBox().invertX(rolling)
        if rolling:
            self.plot.showAxis('bottom')
            step = 2 * division
            first = math.ceil(low / step - 1e-9)
            last = math.floor(high / step + 1e-9)
            ticks = [(i * step, 'NOW' if not i else f'{fmt(abs(i*step),"s")} {"ago" if i<0 else "ahead"}')
                     for i in range(first, last + 1)]
            self.roll_axis.setTicks([ticks])
        else:
            self.plot.hideAxis('bottom')
        self.plot.setXRange(low, high, padding=0)
        self.plot.setYRange(-4, 4, padding=0)
        self.graticule.set_window(low, high)
        self.empty.setPos((low + high) / 2, 0)
        self.draw_capture()
        self.update_status_readouts()

    def draw_capture(self, *_):
        """!
        @brief Rate-limits waveform rendering to 60 FPS and triggers `_render_capture`.
        """
        if not hasattr(self, 'measure_source'):
            return
        remaining = 1 / 60 - (time.monotonic() - self._last_draw)
        if remaining > 0:
            if not self._redraw_timer.isActive():
                self._redraw_timer.start(max(1, math.ceil(remaining * 1000)))
            return
        self._redraw_timer.stop()
        now = time.monotonic()
        if self._last_draw:
            instant = 1 / max(now - self._last_draw, 1e-3)
            self._ui_fps = instant if not self._ui_fps else .80 * self._ui_fps + .20 * instant
        self._last_draw = now
        self._render_capture()

    def _render_capture(self):
        """!
        @brief Renders all active traces, glow overlays, sample dots, stacked lanes, and trigger markers.
        """
        if not hasattr(self, 'level') or not hasattr(self, 'measure_source'):
            return
        capture = self.current
        low, high = self.plot.viewRange()[0]
        rolling = self.uses_roll_axis()
        screen_left = high if rolling else low
        inset = (high - low) * (-.012 if rolling else .012)
        stacked = self.plot_view.currentData() == 'stacked'
        count = len(self.metadata.channels) if self.metadata else 0
        width = max(1, int(self.plot.getViewBox().width()))
        for i, line in enumerate(self.lane_lines):
            line.setVisible(stacked and i < count - 1)
        if capture is not None and len(capture.t):
            start = max(0, int(np.searchsorted(capture.t, low)) - 8)
            end = min(len(capture.t), int(np.searchsorted(capture.t, high)) + 8)
            raw_times = capture.t[start:end]
        else:
            start = end = 0
            raw_times = np.empty(0)
        self.last_display_clip = False
        shown_data = False
        for i, curve in enumerate(self.curves):
            visible = i < count and self.channel_is_plotted(i)
            marker = self.ground_markers[i]
            label = self.lane_labels[i]
            center = 3. - 2. * i
            marker.setVisible(visible and not stacked)
            marker.setPos(screen_left, max(-3.85, min(3.85, -self.positions[i].value() / self.vdivs[i].currentData())))
            label.setVisible(visible and stacked)
            y = np.empty(0)
            if visible and len(raw_times) and i < capture.y.shape[1]:
                y = self.displayed_channel(i)[start:end]
            finite = bool(len(y) and np.isfinite(y).any())
            if stacked and visible:
                name = f'CH{i+1}  {self.channels[i].name}'
                caption = (f'{name}  ·  {fmt(self.vdivs[i].currentData(),self.channels[i].unit)}/div'
                           if finite else f'{name}  ·  Waiting for samples')
                if caption != self.lane_captions[i]:
                    label.setText(caption)
                    self.lane_captions[i] = caption
                label.setPos(screen_left + inset, center + .92)
            if not finite:
                curve.setData([], [])
                self.glows[i].setData([], [])
                self.sample_dots[i].setData([], [])
                continue
            shown_data = True
            divisions = (y - self.positions[i].value()) / self.vdivs[i].currentData()
            self.last_display_clip |= bool(np.any(np.abs(divisions) > 4))
            mode = self.interpolation.currentText()
            discrete = self.channels[i].unit in ('logic', '°C')
            if (discrete or (mode == 'PWM steps (measured)' and looks_like_pwm(divisions))) and len(raw_times) > 1:
                t, values = pwm_hold_trace(raw_times, divisions)
            else:
                t, values = display_interpolate(raw_times, divisions, width, mode)
            if stacked:
                # Each lane maps eight local divisions to its two screen divisions.
                # Gaps at the lane boundary keep off-scale samples from entering another lane.
                values = np.where(np.abs(values) <= 4, center + values * .24, np.nan)
            finite_draw = np.isfinite(values).all()
            curve.setData(t, values, connect='all' if finite_draw else 'finite', skipFiniteCheck=finite_draw)
            if stacked:
                self.glows[i].setData([], [])
            else:
                self.glows[i].setData(t, values, connect='all' if finite_draw else 'finite', skipFiniteCheck=finite_draw)
            if stacked or len(raw_times) >= 80:
                self.sample_dots[i].setData([], [])
            else:
                good = np.isfinite(divisions)
                self.sample_dots[i].setData(raw_times[good], divisions[good])
        any_enabled = any(self.channel_is_plotted(i) for i in range(count))
        show_empty = not shown_data and (not stacked or not any_enabled)
        self.empty.setVisible(show_empty)
        if show_empty:
            message = 'Waiting for samples on this channel' if any_enabled else 'No channel selected' if count else 'No signal'
            if self.empty.toPlainText() != message:
                self.empty.setText(message)
        ch = max(0, self.source.currentIndex())
        level = (self.level.value() - self.positions[ch].value()) / self.vdivs[ch].currentData()
        show_level = bool(count and not stacked and self.channel_is_plotted(ch) and self.mode.currentText() != 'Roll')
        self.syncing = True
        self.trigger_line.setValue(level)
        self.syncing = False
        self.trigger_line.setVisible(show_level)
        self.level_marker.setPos(high, max(-3.85, min(3.85, level)))
        self.level_marker.setVisible(show_level)
        triggered = capture is not None and capture.triggered
        self.trigger_time.setVisible(triggered)
        self.time_marker.setPos(max(low, min(high, 0)), 4)
        self.time_marker.setVisible(triggered)
        self.update_cursor()
        self.update_status_readouts()

    def trigger_dragged(self):
        """!
        @brief Updates the trigger level spinbox when the user drags the horizontal trigger line on the plot.
        """
        if self.syncing:
            return
        ch = max(0, self.source.currentIndex())
        self.level.setValue(self.trigger_line.value() * self.vdivs[ch].currentData() + self.positions[ch].value())
        self.update_trigger_slider()

    def trigger_changed(self, *_):
        """!
        @brief Resets trigger holdoff and refreshes level suffixes/markers when trigger settings change.
        """
        self.engine.last_trigger = -math.inf
        self.reset_processing()
        if hasattr(self, 'level'):
            self.level.setSuffix(' ' + self.channels[max(0, self.source.currentIndex())].unit)
        self.update_trigger_slider()
        self.draw_capture()
        self.update_measurements()
        self.update_status_readouts()

    def measure_changed(self, *_):
        """!
        @brief Slot triggered when the measurement source channel dropdown changes.
        """
        self.update_measurements()
        self.update_cursor()

    def update_measurements(self):
        """!
        @brief Recomputes waveform metrics for the selected measurement channel and updates metric cards.
        """
        if not hasattr(self, 'measure_source'):
            return
        ch = max(0, self.measure_source.currentIndex())
        self.measure_caption.setText(f'MEAS CH{ch+1}')
        self.measure_caption.setToolTip(self.channels[ch].name)
        self._measure_channel = ch
        if self.current is None or not len(self.current.t) or ch >= self.current.y.shape[1]:
            for value in self.metrics.values():
                value.setText('—')
            self._display_frequency = None
            self.sample_label.setText('—')
            self.update_reference_status()
            return
        y = self.displayed_channel(ch)
        values = measurements(self.current.t, y)
        unit = self.channels[ch].unit
        # At 0%/100% duty, electrical noise can falsely look like a very fast
        # low-amplitude pulse train. Do not report spurious frequency/duty.
        if self.reference_mode == 'pwm' and values['vpp'] is not None and values['vpp'] < 0.4:
            values['frequency'] = values['period'] = values['duty'] = None
        self._display_frequency = values['frequency']
        for key, w in self.metrics.items():
            text = fmt(values[key], 'Hz' if key == 'frequency' else 's' if key == 'period' else '%' if key == 'duty' else unit)
            w.setText(text)
            w.setToolTip(text)
        dt = np.diff(self.current.t)
        if len(dt):
            median = float(np.median(dt))
            jitter = np.max(np.abs(dt - median)) / median if median > 0 else math.inf
            timing = f'{fmt(1/median,"Sa/s")} · {len(self.current.t):,} pts' if median > 0 else f'{len(self.current.t):,} pts'
            if jitter >= .01:
                timing += ' · irregular'
        else:
            timing = '1 pt'
        self.sample_label.setText(timing)
        self.update_status_readouts()
        self.update_reference_status()

    def update_cursor(self, *_):
        """!
        @brief Updates visibility and delta readouts (`ΔT`, `1/|ΔT|`, `ΔY`) for interactive Time/Value cursors.
        """
        if not hasattr(self, 'cursor_mode'):
            return
        mode = self.cursor_mode.currentText()
        has = self.current is not None
        stacked = self.plot_view.currentData() == 'stacked'
        for i, line in enumerate(self.cursors):
            line.setVisible(has and (mode in ('Time', 'Time + Value') if i < 2 else
                                     not stacked and mode in ('Value', 'Time + Value')))
        if not has:
            self.cursor_readout.setText('—')
            return
        if mode == 'Off':
            self.cursor_readout.setText('—')
            return
        ch = max(0, self.measure_source.currentIndex())
        unit = self.channels[ch].unit
        lines = []
        if mode in ('Time', 'Time + Value'):
            a, b = [line.value() for line in self.cursors[:2]]
            delta = b - a
            lines.extend([f'T1  {fmt(a,"s")}    T2  {fmt(b,"s")}', f'ΔT  {fmt(delta,"s")}', f'1/|ΔT|  {fmt(1/abs(delta),"Hz") if abs(delta)>1e-15 else "—"}'])
        if mode in ('Value', 'Time + Value'):
            if stacked:
                lines.append('Select one channel for value cursors')
            else:
                a, b = [line.value() * self.vdivs[ch].currentData() + self.positions[ch].value() for line in self.cursors[2:]]
                lines.extend([f'Y1  {fmt(a,unit)}', f'Y2  {fmt(b,unit)}', f'ΔY  {fmt(b-a,unit)}'])
        self.cursor_readout.setText('\n'.join(lines))

    def reset_cursors(self):
        """!
        @brief Repositions time cursors to 25%/75% of the visible X span and value cursors to ±1 division.
        """
        low, high = self.plot.viewRange()[0]
        for line, value in zip(self.cursors, [low + (high - low) * .25, low + (high - low) * .75, -1, 1]):
            line.setValue(value)

    def ensure_cursors_visible(self):
        """!
        @brief Recenters time cursors if they fall outside the current horizontal viewport bounds.
        """
        if not hasattr(self, 'cursor_mode') or self.cursor_mode.currentText() == 'Off':
            return
        low, high = self.plot.viewRange()[0]
        if any(line.value() < low or line.value() > high for line in self.cursors[:2]):
            self.reset_cursors()

    def cursor_mode_changed(self, *_):
        """!
        @brief Slot triggered when cursor mode switches between Off, Time, Value, and Time + Value.
        """
        self.ensure_cursors_visible()
        self.update_cursor()

    def fit_first_capture(self):
        """!
        @brief Automatically scales and centers the first acquired hardware waveform onto the 8-division grid.
        """
        if self.current is None or len(self.current.t) < 8:
            return
        for i in range(min(self.current.y.shape[1], len(self.vdivs))):
            if not self.channel_checks[i].isChecked():
                continue
            y = self.displayed_channel(i)
            result = fit_voltage_scale(y, SCALES)
            if result is None:
                continue
            scale, center = result
            if scale != self.vdivs[i].currentData():
                self.vdivs[i].setCurrentIndex(SCALES.index(scale))
            self.positions[i].setValue(center)
        ch = min(self.source.currentIndex(), self.current.y.shape[1] - 1)
        y = self.displayed_channel(ch)
        if y is not None:
            y = y[np.isfinite(y)]
        if y is not None and len(y):
            mid = (float(np.min(y)) + float(np.max(y))) / 2
            self.level.setValue(mid)
        self.draw_capture()
        self.update_measurements()

    def autorange_capture(self):
        """!
        @brief Continuously adjusts vertical scale, offset, timebase, and trigger level to track live signals.
        """
        if self.current is None:
            return
        for i in range(self.current.y.shape[1]):
            if not self.channel_checks[i].isChecked():
                continue
            y = self.displayed_channel(i)
            if y is None:
                continue
            y = y[np.isfinite(y)]
            if not len(y):
                continue
            extent = max(float(np.ptp(y)) / 6, 1e-6)
            choice = next((v for v in SCALES if v >= extent), SCALES[-1])
            if self.vdivs[i].currentData() != choice:
                self.vdivs[i].setCurrentIndex(SCALES.index(choice))
            center = float((np.max(y) + np.min(y)) / 2)
            if abs(center - self.positions[i].value()) > .15 * self.vdivs[i].currentData():
                self.positions[i].setValue(center)
        ch = max(0, self.source.currentIndex())
        ch = min(ch, self.current.y.shape[1] - 1)
        y = self.displayed_channel(ch)
        m = measurements(self.current.t, y)
        if m['period'] is not None:
            target = max(m['period'] / 2, 1e-6)
            choice = next((v for v in TIME_SCALES if v >= target), TIME_SCALES[-1])
            if self.tdiv.currentData() != choice:
                self.tdiv.setCurrentIndex(TIME_SCALES.index(choice))
        y = y[np.isfinite(y)]
        if not len(y):
            return
        midpoint = float((np.max(y) + np.min(y)) / 2)
        if abs(midpoint - self.level.value()) > .1 * self.vdivs[ch].currentData():
            self.level.setValue(midpoint)

    def autoset(self):
        """!
        @brief One-shot AUTOSET adjusting vertical scales, offsets, timebase (~2 periods/div), and trigger midpoint.
        """
        if self.current is None:
            return
        for i in range(self.current.y.shape[1]):
            y = self.displayed_channel(i)
            if y is None:
                continue
            y = y[np.isfinite(y)]
            if not len(y):
                continue
            extent = max(float(np.ptp(y)) / 6, 1e-6)
            choice = next((v for v in SCALES if v >= extent), SCALES[-1])
            self.vdivs[i].setCurrentIndex(SCALES.index(choice))
            self.positions[i].setValue(float((np.max(y) + np.min(y)) / 2))
        ch = max(0, self.source.currentIndex())
        ch = min(ch, self.current.y.shape[1] - 1)
        y = self.displayed_channel(ch)
        m = measurements(self.current.t, y)
        if m['period'] is not None:
            target = max(m['period'] / 2, 1e-6)
        elif len(self.current.t) > 1:
            target = max(float(self.current.t[-1] - self.current.t[0]) / 8, 1e-6)
        else:
            target = self.tdiv.currentData()
        choice = next((v for v in TIME_SCALES if v >= target), TIME_SCALES[-1])
        self.tdiv.setCurrentIndex(TIME_SCALES.index(choice))
        self.pan.setValue(0)
        y = y[np.isfinite(y)]
        if len(y):
            self.level.setValue(float((np.max(y) + np.min(y)) / 2))
        self.draw_capture()
        self.reset_cursors()
        self.show_menu('ACQUIRE')

    def apply_tek_preset(self):
        """!
        @brief Configures display controls to match the Tektronix TBS1062 1 Hz square-wave reference (1 V/div, 500 ms/div).
        """
        self.reference_mode = "tek"
        self.reference_active = True
        self.plot_view.setCurrentIndex(self.plot_view.findData('overlay'))
        self.reference_label.setText("TEK TBS1062 REFERENCE  ·  CH1 1 V/div  ·  500 ms/div  ·  1 Hz square-wave test")
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

    def apply_pwm_preset(self):
        """!
        @brief Configures display controls for the 100 Hz GPIO25 PWM reference (2 V/div, 5 ms/div, PWM step rendering).
        """
        self.reference_mode = "pwm"
        self.reference_active = True
        self.plot_view.setCurrentIndex(self.plot_view.findData('overlay'))
        self.reference_label.setText("TEK VIDEO REFERENCE · 100 Hz PWM · CH1 2 V/div · 5 ms/div · measured GPIO34")
        self.tdiv.setCurrentIndex(TIME_SCALES.index(.005))
        self.probes[0].setCurrentText('1X')
        self.vdivs[0].setCurrentIndex(SCALES.index(2.))
        self.positions[0].setValue(1.65)
        self.couplings[0].setCurrentText('DC')
        self.interpolation.setCurrentText('PWM steps (measured)')
        self.tek_screen_check.setChecked(True)
        self.pan.setValue(0.)
        self.source.setCurrentIndex(0)
        self.measure_source.setCurrentIndex(0)
        self.mode.setCurrentText('Auto')
        self.slope.setCurrentText('Rising')
        self.level.setValue(1.65)
        self.autorange_box.setChecked(False)
        # Preserve the requested 2 V/div, 5 ms/div display on first acquisition.
        # Initial auto-fit is useful for unknown devices, but can change a
        # calibrated PWM preset to millivolts/div on a partial first record.
        self.initial_fit_pending = False
        self.update_axes()
        self.update_measurements()
        self.update_reference_status()

    def apply_sensors_preset(self):
        """!
        @brief Configures Roll mode (500 ms/div) and per-channel vertical scales for the ESP32 Four Sensors firmware.
        """
        self.reference_mode = 'sensors'
        self.reference_active = False
        self.reference_label.setText('ESP32 SENSORS · LIVE SERIAL ACQUISITION')
        self.tdiv.setCurrentIndex(TIME_SCALES.index(.5))
        self.tek_screen_check.setChecked(False)
        self.interpolation.setCurrentText('Linear')
        self.acquisition_mode.setCurrentText('Sample')
        self.autorange_box.setChecked(False)
        self.pan.setValue(0.)
        self.mode.setCurrentText('Roll')
        for i, ch in enumerate(self.metadata.channels):
            self.probes[i].setCurrentText('1X')
            scale, center = {'°C': (5., 25.), 'logic': (.2, .5)}.get(ch['unit'], (.5, 1.65))
            self.vdivs[i].setCurrentIndex(SCALES.index(scale))
            self.positions[i].setValue(center)
            self.couplings[i].setCurrentText('DC')
        self.initial_fit_pending = False
        self.update_axes()
        self.update_measurements()

    def update_reference_status(self):
        """!
        @brief Updates the reference comparison banner and scale/gain audit bar with input vs display metrics.
        """
        if not hasattr(self, 'reference_status') or not hasattr(self, 'scale_audit_label'):
            return
        if self.reference_mode == 'sensors':
            ir_state = ''
            if (self.current is not None and self.metadata is not None
                    and self.current.y.shape[1] == len(self.metadata.channels) == 4
                    and self.metadata.channels[3]['unit'] == 'logic'):
                state = self.current.y[-1, 3]
                if np.isfinite(state):
                    active_low = self.metadata.channels[3].get('active_low', False)
                    detected = (state < .5) if active_low else (state >= .5)
                    ir_state = ' · IR DETECTED' if detected else ' · IR CLEAR'
            direction = 'ROLL → · newest at left' if self.uses_roll_axis() else 'Select a trace in Plot view'
            self.reference_status.setText(direction + ir_state)
            labels = '  ·  '.join(f'CH{i+1} {ch.name}' for i, ch in enumerate(self.channels))
            if self.scale_audit_label.text() != labels:
                self.scale_audit_label.setText(labels)
            if self.metadata is not None and len(self.metadata.channels) < 4:
                self.scale_audit_label.setText('Follow the channel labels for this older sensor sketch.')
            if self.scale_audit.property('warning') is not False:
                self.scale_audit.setProperty('warning', False)
                repolish(self.scale_audit)
            if self.reference_status.objectName() != 'referenceLabel':
                self.reference_status.setObjectName('referenceLabel')
                repolish(self.reference_status)
            return
        gain = self.channels[0].gain
        offset = self.channels[0].offset
        if self.current is None or len(self.current.t) < 8 or self.current.y.shape[1] < 1:
            self.scale_audit_label.setText(
                f'SCALE CHECK  ·  CH1 software gain ×{gain:.6g}, offset {offset:+.6g} V  ·  awaiting live samples')
            self.reference_status.setText('PWM reference: 100 Hz · nominal 3.3 Vpp · 5 ms/div' if self.reference_mode == 'pwm' else 'TEK reference: 1 Hz · nominal 3.3 Vpp · 1 V/div')
            self.scale_audit.setProperty('warning', suspicious_gain(gain))
            repolish(self.scale_audit)
            return
        y = self.displayed_channel(0)
        data = measurements(self.current.t, y)
        vpp, freq = data['vpp'], data['frequency']
        raw_pp = raw_span_from_calibrated(self.current.y[:, 0], gain)
        raw_text = fmt(raw_pp, self.channels[0].unit)
        corrected_text = fmt(vpp, self.channels[0].unit)
        _, outside = display_range_health(y, self.vdivs[0].currentData(), self.positions[0].value())
        warning = suspicious_gain(gain) or abs(offset) > 5 or outside
        flags = []
        if suspicious_gain(gain):
            flags.append('UNVERIFIED HIGH GAIN')
        if outside:
            flags.append('DISPLAY OFF-SCREEN — AUTOSET')
        if vpp is not None and raw_pp is not None and raw_pp < .05 and gain > 5:
            flags.append('3.3 V DISPLAY MAY BE SOFTWARE-AMPLIFIED')
        if not flags:
            flags.append('CH1 values are read from the device; not metrology-certified')
        self.scale_audit_label.setText(
            f'CH1 INPUT {raw_text} p-p  →  DISPLAY {corrected_text} p-p '
            f'  |  gain ×{gain:.6g}  offset {fmt(offset,"V")}  |  ' + ' · '.join(flags))
        self.scale_audit.setProperty('warning', warning)
        repolish(self.scale_audit)
        if not self.reference_active:
            self.reference_status.setText('Select PWM 100 Hz for potentiometer capture' if self.reference_mode == 'pwm' else 'Select TEK PRESET to match 1 V/div and 500 ms/div')
        elif suspicious_gain(gain):
            self.reference_status.setText('⚠ 3.3 V on display is NOT verified — reset CH1 gain/check firmware')
        elif vpp is None:
            self.reference_status.setText('Awaiting signal')
        elif 2.6 <= vpp <= 4.0 and (freq is None or abs(freq - (100. if self.reference_mode == "pwm" else 1.)) <= (12. if self.reference_mode == "pwm" else .25)):
            self.reference_status.setText(f'CH1 {fmt(vpp,"V")} p-p · {fmt(freq,"Hz")} · nominal reference')
        else:
            self.reference_status.setText(f'CH1 {fmt(vpp,"V")} p-p · {fmt(freq,"Hz")} · check ADC/units')
        self.reference_status.setToolTip('Hardware waveform and independently verified input are required for actual calibration.')
        self.reference_status.setObjectName('referenceWarning' if warning else 'referenceLabel')
        repolish(self.reference_status)

    def reset_ch1_gain(self):
        """!
        @brief Prompts the user and restores CH1 software calibration to unity (`gain=1.0`, `offset=0.0`).
        """
        ch = self.channels[0]
        if abs(ch.gain - 1.) < 1e-12 and abs(ch.offset) < 1e-12:
            QMessageBox.information(self, 'CH1 scaling', 'CH1 already uses gain 1 and offset 0. The incoming firmware values are displayed as transmitted.')
            return
        message = (f'CH1 is currently using gain ×{ch.gain:.7g} and offset {ch.offset:+.7g} V.\n\n'
                   'Restore gain = 1 and offset = 0? This will reveal what the board actually transmits. '
                   'If the firmware reports millivolts as volts, you must correct the firmware or its wiring.')
        if QMessageBox.question(self, 'Reset software voltage gain', message, QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) != QMessageBox.Yes:
            return
        was_running = self.running
        ch.gain = 1.0
        ch.offset = 0.0
        self.reset_after_scaling_change(was_running)

    def reset_after_scaling_change(self, was_running):
        """!
        @brief Clears stale captures, persists updated channel profiles, and restarts acquisition if previously running.
        @param was_running True if oscilloscope was running prior to the calibration change.
        """
        self.engine.clear()
        self.clear_capture()
        self.save_profiles()
        self.initial_fit_pending = (self.reference_mode != 'pwm')
        if was_running and self.link_open and self.metadata:
            self.arm(False)
        else:
            self.set_run_state(False)
            self.controls_enabled(self.link_open and self.metadata is not None)
        self.update_reference_status()

    def calibrate_channel(self):
        """!
        @brief Opens an interactive two-point voltage calibration dialog with safety bounds checking.
        """
        if self.current is None or not len(self.current.t):
            QMessageBox.information(self, 'Calibration', 'Acquire a hardware waveform before calibrating.')
            return
        index = max(0, self.measure_source.currentIndex())
        if index >= self.current.y.shape[1]:
            return
        channel = self.channels[index]
        if channel.unit in ('°C', 'logic'):
            QMessageBox.information(self, 'Calibration', 'Select a voltage channel for two-point voltage calibration.')
            return
        d = QDialog(self)
        d.setWindowTitle(f'CH{index+1} — two-point voltage calibration')
        d.resize(510, 340)
        layout = QVBoxLayout(d)
        note = QLabel('Enter LOW/HIGH voltages measured independently for the SAME channel quantity transmitted by the firmware (not a different point on the circuit). For the 1k/1k ESP32 divider, the ADC pin is ~0–1.65 V; the supplied firmware reports reconstructed ~0–3.3 V. A major mismatch is a wiring or firmware-unit problem, NOT something this dialog can fix.')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        low = spin(0., -1000, 1000, 6)
        high = spin(3.3, -1000, 1000, 6)
        low.setSuffix(' V')
        high.setSuffix(' V')
        form.addRow('Verified LOW level', low)
        form.addRow('Verified HIGH level', high)
        preview = QLabel()
        preview.setWordWrap(True)
        preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow('Calculated correction', preview)
        layout.addLayout(form)
        confirmation = QCheckBox('I verified the voltage reference and GPIO / GND wiring.')
        layout.addWidget(confirmation)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)
        latest = [None]

        def update_preview():
            try:
                result = fit_two_point(self.current.y[:, index], channel.gain, channel.offset, low.value(), high.value())
                latest[0] = result
                notice = '\n⚠ Large correction: verify the ADC output and units before accepting.' if result.suspicious else ''
                preview.setText(f'ADC low {fmt(result.measured_low,"V")}  |  high {fmt(result.measured_high,"V")}\nGain {result.gain:.8g}   Offset {result.offset:.8g}\nChange ×{result.gain_multiplier:.5g}{notice}')
            except (ValueError, OverflowError, ZeroDivisionError) as exc:
                latest[0] = None
                preview.setText(str(exc))

        low.valueChanged.connect(update_preview)
        high.valueChanged.connect(update_preview)
        update_preview()

        def apply():
            result = latest[0]
            if result is None:
                QMessageBox.warning(d, 'Invalid calibration', 'A valid captured square wave is required.')
                return
            if not confirmation.isChecked():
                QMessageBox.warning(d, 'Reference not verified', 'Check the confirmation box only after verifying the actual signal.')
                return
            if result.suspicious:
                QMessageBox.warning(d, 'Unsafe calibration blocked',
                    f'This calibration requires a {result.gain_multiplier:.6g}× correction.\n\n'
                    'Corrections outside 0.2×–5× are blocked because they can make a '
                    '4.8 mV firmware error look like a valid 3.3 V waveform.\n\n'
                    'Check USB firmware, ADC wiring, V/mV conversion and resistor divider. '
                    'Use RESET CH1 GAIN to discard the old factor. '
                    'Only use Settings → Gain for independently validated unusual sensor conversions.')
                return
            was_running = self.running
            channel.gain = result.gain
            channel.offset = result.offset
            self.reset_after_scaling_change(was_running)
            self.capture_label.setText('RUN' if was_running else 'READY — press RUN')
            d.accept()

        buttons.accepted.connect(apply)
        buttons.rejected.connect(d.reject)
        d.exec()

    def configure(self):
        """!
        @brief Opens @ref ProfileDialog and applies edited board profile and channel parameters.
        """
        dlg = ProfileDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        self.board_profile = dlg.board.currentText()
        for ch, fields in zip(self.channels, dlg.fields):
            sensor, name, unit, gain, offset = fields
            ch.sensor, ch.name, ch.unit, ch.gain, ch.offset = sensor.currentText(), name.text().strip() or 'Input', unit.text().strip(), gain.value(), offset.value()
        self.set_run_state(False)
        self.engine.clear()
        self.clear_capture()
        self.channel_labels()
        self.save_profiles()
        self.initial_fit_pending = True
        self.controls_enabled(self.link_open and self.metadata is not None)
        self.capture_label.setText('READY' if self.metadata else 'WAITING')
        self.set_run_state(False)

    def export_csv(self):
        """!
        @brief Exports the active capture samples to a CSV file alongside a `.metadata.json` sidecar.
        """
        if self.current is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Export measured samples', 'dso_capture.csv', 'CSV (*.csv)')
        if not path:
            return
        try:
            c = self.current
            with open(path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(['device_time_s', 'relative_time_s'] + [f'CH{i+1}_{self.channels[i].name} [{self.channels[i].unit}]' for i in range(c.y.shape[1])])
                w.writerows([float(t + c.origin), float(t), *('' if np.isnan(value) else value for value in row)]
                            for t, row in zip(c.t, c.y))
            Path(path).with_suffix('.metadata.json').write_text(json.dumps({'device': self.metadata.device if self.metadata else '', 'channels': [vars(ch) for ch in self.channels[:c.y.shape[1]]], 'triggered': c.triggered, 'clipped_or_range_limit': c.clipped, 'accuracy': 'unverified', 'filter': 'none'}, indent=2))
        except OSError as exc:
            QMessageBox.warning(self, 'Export', str(exc))

    def export_png(self):
        """!
        @brief Captures and saves a PNG screenshot of the oscilloscope main window.
        """
        path, _ = QFileDialog.getSaveFileName(self, 'Save instrument screen', 'dso_screen.png', 'PNG (*.png)')
        if path and not self.grab().save(path):
            QMessageBox.warning(self, 'Save', 'Could not save the image.')

    def help(self):
        """!
        @brief Displays a quick-start hardware wiring and serial connection guide dialog.
        """
        QMessageBox.information(self, 'Connect real hardware',
            '1. For the four-channel sketch, wire the potentiometer to GPIO35 and the LED to GPIO25 through a resistor.\n'
            '2. Close Arduino Serial Monitor. Choose Serial, the ESP32 COM port, and 921600 baud.\n'
            '3. Click Connect. The four-channel sketch starts in Roll automatically.\n'
            '4. Choose CH2 Potentiometer, All stacked, or All overlaid in Plot view.\n'
            '5. See START_HERE_4_SENSORS.txt for wiring and setup.')

    def closeEvent(self, event):
        """!
        @brief Stops timers, detaches custom graphics items, and cleanly joins the reader thread on exit.
        @param event Qt QCloseEvent.
        """
        self.timer.stop()
        self._redraw_timer.stop()
        self.plot.removeItem(self.graticule)
        if self.reader:
            self.reader.close()
            self.reader.join(timeout=.5)
        event.accept()


def main():
    """!
    @brief Application entry point initializing QApplication, default UI fonts, and @ref ScopeWindow.
    """
    app = QApplication(sys.argv)
    font = QFont()
    font.setFamilies(UI_FONTS)
    font.setPointSize(10)
    app.setFont(font)
    window = ScopeWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()