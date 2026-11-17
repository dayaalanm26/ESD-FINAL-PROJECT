"""Version 1 DSO packets, bounded capture history and measurements."""
import json
import math
from collections import deque
from dataclasses import dataclass
from statistics import median

MAX_LINE = 2_000_000
MAX_ROWS = 8192
MAX_CHANNELS = 4
UINT32_MASK = (1 << 32) - 1


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Device:
    name: str
    channels: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Packet:
    sequence: int | None
    times: tuple[float, ...]
    values: tuple[tuple[float, ...], ...]
    discontinuity: bool = False


class Decoder:
    def __init__(self):
        self.device = None

    def decode(self, line):
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError("expected a JSON object")
            if obj.get("type") == "hello":
                if type(obj.get("protocol")) is not int or obj["protocol"] != 1:
                    raise ValueError("expected DSO protocol version 1")
                names = obj["channels"]
                if not isinstance(names, list) or not 1 <= len(names) <= MAX_CHANNELS:
                    raise ValueError("expected one to four channels")
                self.device = Device(
                    str(obj.get("device", "DSO device"))[:80],
                    tuple((str(ch.get("name", f"CH{i + 1}"))[:64], str(ch.get("unit", "V"))[:12])
                          for i, ch in enumerate(names)),
                )
                return self.device
            if obj.get("type") != "samples":
                raise ValueError("unknown message type")
            if self.device is None:
                return None
            times = obj["times"]
            values = obj["values"]
            if not isinstance(times, list) or not isinstance(values, list):
                raise ValueError("expected arrays of timestamps and values")
            if not 1 <= len(times) <= MAX_ROWS or len(times) != len(values):
                raise ValueError("invalid sample count")
            converted = tuple(number(t) for t in times)
            if any(b <= a for a, b in zip(converted, converted[1:])):
                raise ValueError("timestamps must increase within each packet")
            width = len(self.device.channels)
            if any(not isinstance(row, list) or len(row) != width for row in values):
                raise ValueError("sample width differs from the device profile")
            seq = obj.get("seq")
            if seq is not None and (type(seq) is not int or not 0 <= seq <= UINT32_MASK):
                raise ValueError("invalid packet sequence")
            return Packet(seq, converted, tuple(tuple(number(v) for v in row) for row in values),
                          obj.get("discontinuity") is True)
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ProtocolError(str(exc)) from exc


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e12:
        raise ValueError("samples must be finite numbers within range")
    return float(value)


@dataclass(frozen=True)
class Capture:
    times: tuple[float, ...]
    values: tuple[tuple[float, ...], ...]
    start: float
    span: float
    trigger: float | None


class CaptureBuffer:
    def __init__(self, max_samples=90_000):
        self.rows = deque(maxlen=max_samples)
        self.gaps = 0
        self._sequence = None

    @property
    def latest(self):
        return self.rows[-1][0] if self.rows else None

    def append(self, packet):
        seq_gap = (self._sequence is not None and packet.sequence is not None
                   and packet.sequence != ((self._sequence + 1) & UINT32_MASK))
        restarted = self.latest is not None and packet.times[0] <= self.latest
        gap = packet.discontinuity or seq_gap or restarted
        if gap:
            self.rows.clear()
            self.gaps += 1
        self.rows.extend(zip(packet.times, packet.values))
        self._sequence = packet.sequence
        return gap

    def capture(self, span, channel, level, rising, auto, after=None, rolling=False):
        if len(self.rows) < 2:
            return None
        newest = self.latest
        if after is not None and newest <= after:
            return None
        # Inspect the visible time span plus trigger search history without copying old rows.
        recent = []
        for row in reversed(self.rows):
            recent.append(row)
            if row[0] < newest - 2.5 * span:
                break
        ready = newest - 0.8 * span
        edge = None
        if not rolling and channel < len(recent[0][1]):
            for (tb, vb), (ta, va) in zip(recent, recent[1:]):
                if tb < ready - span or (after is not None and tb <= after):
                    break
                if ta > ready:
                    continue
                a, b = va[channel], vb[channel]
                from_value, to_value = (a, b) if rising else (b, a)
                if from_value < level <= to_value:
                    crossing = ta + (tb - ta) * (level - a) / (b - a)
                    if crossing <= ready and (after is None or crossing > after):
                        edge = crossing
                        break
        if edge is None and not auto:
            return None
        start = newest - span if edge is None else edge - 0.2 * span
        end = newest if edge is None else edge + 0.8 * span
        rows = [(t, values) for t, values in reversed(recent) if start <= t <= end]
        if len(rows) < 2:
            return None
        times, values = zip(*rows)
        return Capture(times, values, start, span, edge)


def sample_rate(times):
    if len(times) < 2:
        return None
    steps = [b - a for a, b in zip(times, times[1:]) if b > a]
    return 1 / median(steps) if steps else None


def measure(times, values):
    """Use the captured samples and measured time intervals, including for duty."""
    if not values:
        return {}
    lo, hi = min(values), max(values)
    total = len(values)
    span = hi - lo
    result = dict(low=lo, high=hi, mean=sum(values) / total, vpp=span,
                  rms=math.sqrt(sum(v * v for v in values) / total),
                  frequency=None, period=None, duty=None)
    if total < 5 or span < 0.025:
        return result
    middle = (hi + lo) / 2
    lower, upper = middle - 0.10 * span, middle + 0.10 * span
    high_state = values[0] >= middle
    rises, falls = [], []
    candidate = None
    for ta, tb, a, b in zip(times, times[1:], values, values[1:]):
        if (not high_state and a < middle <= b) or (high_state and b <= middle < a):
            candidate = ta + (tb - ta) * (middle - a) / (b - a)
        if candidate is None:
            continue
        if not high_state and b >= upper:
            rises.append(candidate)
            high_state, candidate = True, None
        elif high_state and b <= lower:
            falls.append(candidate)
            high_state, candidate = False, None
        elif (not high_state and b <= lower) or (high_state and b >= upper):
            candidate = None
    periods = [b - a for a, b in zip(rises, rises[1:])]
    if len(periods) < 2:
        periods = [b - a for a, b in zip(falls, falls[1:])]
    if len(periods) >= 2:
        period = median(periods)
        result["period"] = period
        result["frequency"] = 1 / period
        duties = []
        next_fall = 0
        for a, b in zip(rises, rises[1:]):
            while next_fall < len(falls) and falls[next_fall] <= a:
                next_fall += 1
            if next_fall < len(falls) and falls[next_fall] < b:
                duties.append((falls[next_fall] - a) / (b - a) * 100)
        result["duty"] = median(duties) if duties else None
    return result


def pixel_envelope(times, values, start, span, width):
    """Retain each pixel bucket's first, last and voltage extrema in time order."""
    if len(times) <= width * 2:
        return list(zip(times, values))
    reduced = []
    samples = zip(times, values)
    for x, y in samples:
        pixel = int((x - start) / span * width)
        if not reduced or pixel != reduced[-1][0]:
            reduced.append((pixel, [(x, y)] * 4))
        points = reduced[-1][1]
        points[3] = x, y
        if y < points[1][1]:
            points[1] = x, y
        if y > points[2][1]:
            points[2] = x, y
    return [point for _, points in reduced for point in sorted(set(points))]


def time_text(seconds):
    value = abs(round(seconds, 12))
    if value >= 1:
        return f"{seconds:.3g} s"
    if value >= 1e-3 or value == 0:
        return f"{seconds * 1e3:.3g} ms"
    return f"{seconds * 1e6:.3g} µs"
