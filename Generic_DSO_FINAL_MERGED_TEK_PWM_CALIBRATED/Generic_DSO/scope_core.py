"""Hardware sample protocol and numerical processing. No generated source."""
from dataclasses import dataclass
import json
import math
import numpy as np

MAX_POINTS = 65536
MAX_LINE = 2_000_000


class ProtocolError(ValueError):
    pass


@dataclass
class Metadata:
    device: str
    channels: list


@dataclass
class Frame:
    t: np.ndarray
    y: np.ndarray
    seq: int
    gap: bool = False
    clipped: bool = False


@dataclass
class Capture:
    t: np.ndarray
    y: np.ndarray
    origin: float
    triggered: bool
    clipped: bool


def number(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ProtocolError('Expected a finite numeric value')
    return float(x)


class Decoder:
    def __init__(self):
        self.metadata = None
        self.seq = None
        self.last_t = None
        self.last_dt = None
        self.force_gap = False

    def decode(self, line):
        try:
            return self._decode(line)
        except (ValueError, TypeError, KeyError, OverflowError, IndexError) as exc:
            self.force_gap = True
            if isinstance(exc, ProtocolError): raise
            raise ProtocolError(str(exc)) from exc

    def _decode(self, line):
        if len(line) > MAX_LINE: raise ProtocolError('Frame exceeds 2 MB limit')
        obj = json.loads(line)
        if not isinstance(obj, dict): raise ProtocolError('Expected JSON object')
        if obj.get('type') == 'hello':
            if obj.get('protocol') != 1: raise ProtocolError('Expected protocol version 1')
            channels = obj['channels']
            if not isinstance(channels, list) or not 1 <= len(channels) <= 4:
                raise ProtocolError('Device must define 1–4 channels')
            clean = []
            for ch in channels:
                if not isinstance(ch, dict): raise ProtocolError('Invalid channel metadata')
                name, unit = ch.get('name'), ch.get('unit')
                if not isinstance(name, str) or not name or len(name) > 40:
                    raise ProtocolError('Channel name is required, maximum 40 characters')
                if not isinstance(unit, str) or len(unit) > 16:
                    raise ProtocolError('Channel unit is required, maximum 16 characters')
                low = number(ch['min']) if 'min' in ch else None
                high = number(ch['max']) if 'max' in ch else None
                if (low is None) != (high is None) or (low is not None and low >= high):
                    raise ProtocolError('Channel range requires min < max')
                clean.append(dict(name=name, unit=unit, min=low, max=high))
            device = obj.get('device', 'Generic device')
            if not isinstance(device, str) or len(device) > 80:
                raise ProtocolError('Invalid device name')
            meta = Metadata(device, clean)
            # Repeated identical metadata is a keepalive, not a new sequence epoch.
            if meta != self.metadata:
                self.seq = self.last_t = self.last_dt = None
            self.metadata = meta
            return meta
        if obj.get('type') != 'samples': raise ProtocolError('Unknown message type')
        if self.metadata is None: raise ProtocolError('Waiting for device hello metadata')
        seq = obj['seq']
        if type(seq) is not int or seq < 0: raise ProtocolError('seq must be a nonnegative integer')
        if self.seq is not None and seq <= self.seq: raise ProtocolError('Duplicate/out-of-order seq; reconnect after device restart')
        values = obj['values']
        if not isinstance(values, list) or not 1 <= len(values) <= 4096:
            raise ProtocolError('Frames require 1–4096 sample rows')
        width = len(self.metadata.channels)
        for row in values:
            if not isinstance(row, list) or len(row) != width:
                raise ProtocolError('Sample row does not match channel count')
            for v in row: number(v)
        y = np.asarray(values, dtype=float)
        if 'times' in obj:
            if 'fs' in obj or 't0' in obj: raise ProtocolError('Use times OR t0/fs')
            times = obj['times']
            if not isinstance(times, list) or len(times) != len(y): raise ProtocolError('Timing length mismatch')
            t = np.asarray([number(v) for v in times])
            dt = None
        else:
            fs, t0 = number(obj['fs']), number(obj['t0'])
            if not 0 < fs <= 1e10: raise ProtocolError('Invalid sample rate')
            dt = 1 / fs
            t = t0 + np.arange(len(y)) * dt
        if t[0] < 0 or not np.all(np.isfinite(t)) or np.any(np.diff(t) <= 0):
            raise ProtocolError('Sample times must increase and be nonnegative')
        if self.last_t is not None and t[0] <= self.last_t:
            raise ProtocolError('Sample times overlap or run backwards')
        discontinuity = obj.get('discontinuity', False)
        if type(discontinuity) is not bool: raise ProtocolError('discontinuity must be boolean')
        gap = discontinuity or self.force_gap or (self.seq is not None and seq != self.seq + 1)
        if self.last_t is not None and dt is not None and self.last_dt is not None:
            gap |= not math.isclose(t[0] - self.last_t, dt, rel_tol=.05, abs_tol=1e-9)
            gap |= not math.isclose(dt, self.last_dt, rel_tol=.001)
        if 'times' in obj:
            steps = np.diff(t)
            cadence = float(np.median(steps)) if len(steps) else self.last_dt
            if self.last_dt is not None and self.last_t is not None:
                gap |= t[0]-self.last_t > 10*self.last_dt + 1e-9
            if cadence is not None:
                breaks = np.flatnonzero(steps > 10*cadence + 1e-9)
                if len(breaks):
                    cut = int(breaks[-1])+1
                    t, y = t[cut:], y[cut:]
                    gap = True
                    cadence = float(np.median(np.diff(t))) if len(t)>1 else cadence
            if cadence is None and self.last_t is not None: cadence = float(t[0]-self.last_t)
            dt = cadence
        clipped = obj.get('clipped', False)
        if type(clipped) is not bool: raise ProtocolError('clipped must be boolean')
        for i, ch in enumerate(self.metadata.channels):
            if ch['min'] is not None:
                clipped |= bool(np.any((y[:, i] <= ch['min']) | (y[:, i] >= ch['max'])))
        self.last_t, self.last_dt, self.seq = float(t[-1]), dt, seq
        self.force_gap = False
        return Frame(t, y, seq, bool(gap), clipped)


class CaptureEngine:
    def __init__(self):
        self.clear()

    def clear(self):
        self.t = np.empty(0)
        self.y = None
        self.clipping = np.empty(0, dtype=bool)
        self.last_trigger = -math.inf
        self.last_auto = -math.inf

    def append(self, frame):
        if frame.gap: self.clear()
        self.t = np.concatenate((self.t, frame.t))[-MAX_POINTS:]
        self.y = frame.y.copy() if self.y is None else np.concatenate((self.y, frame.y))[-MAX_POINTS:]
        self.clipping = np.concatenate((self.clipping, np.full(len(frame.t), frame.clipped)))[-MAX_POINTS:]

    def capture(self, span, channel, level, slope, mode, allow_auto, prefer_newest=True):
        if len(self.t) < 2 or self.y is None or channel >= self.y.shape[1]: return None
        y = self.y[:, channel]
        mask = (y[:-1] < level) & (y[1:] >= level) if slope == 'Rising' else (y[:-1] > level) & (y[1:] <= level)
        edges = np.flatnonzero(mask)
        if prefer_newest: edges = edges[::-1]
        for edge in edges:
            frac = (level-y[edge])/(y[edge+1]-y[edge])
            at = float(self.t[edge] + frac*(self.t[edge+1]-self.t[edge]))
            if at <= self.last_trigger: continue
            start, end = at-span*.2, at+span*.8
            if start < self.t[0]: continue
            if end > self.t[-1]: continue
            lo, hi = np.searchsorted(self.t, [start, end], side='left')
            if hi-lo < 2: continue
            self.last_trigger = at
            return Capture(self.t[lo:hi].copy()-at, self.y[lo:hi].copy(), at, True, bool(np.any(self.clipping[lo:hi])))
        if mode == 'Auto' and allow_auto and self.t[-1] > self.last_auto:
            lo = min(len(self.t)-2, max(0, int(np.searchsorted(self.t, self.t[-1]-span))))
            self.last_auto = self.t[-1]
            origin = float(self.t[-1]-span*.8)
            return Capture(self.t[lo:].copy()-origin, self.y[lo:].copy(), origin, False, bool(np.any(self.clipping[lo:])))
        return None


def measurements(t, y):
    y, t = np.asarray(y), np.asarray(t)
    if not len(y): return dict.fromkeys(('vpp','mean','rms','minimum','maximum','frequency','period','duty'))
    low, high = float(np.min(y)), float(np.max(y))
    result = dict(vpp=high-low, mean=float(np.mean(y)), rms=float(np.sqrt(np.mean(y*y))),
                  minimum=low, maximum=high, frequency=None, period=None, duty=None)
    if len(y) < 8 or high-low <= max(1e-12, abs(result['mean'])*1e-9): return result
    mid, hysteresis = (high+low)*.5, (high-low)*.1
    armed = y[0] < mid-hysteresis
    crossings = []
    for i in range(1, len(y)):
        if y[i] < mid-hysteresis: armed = True
        if armed and y[i-1] < mid <= y[i]:
            crossings.append(t[i-1]+(mid-y[i-1])*(t[i]-t[i-1])/(y[i]-y[i-1]))
            armed = False
    if len(crossings) >= 3:
        periods = np.diff(crossings)
        period = float(np.median(periods))
        if period > 0 and np.max(np.abs(periods-period))/period < .15:
            # Require >= 10 samples per estimated cycle; no precision claim on undersampled data.
            if np.median(np.diff(t)) <= period/10:
                result['period'], result['frequency'] = period, 1/period
                result['duty'] = float(np.mean(y >= mid) * 100.0)
    return result
