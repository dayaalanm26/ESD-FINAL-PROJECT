"""Safe, deterministic display autoscaling and calibration health calculations.

These functions never alter capture samples or claim the voltage source was verified.
"""
import math
import numpy as np


def fit_voltage_scale(values, scale_steps, divisions=8):
    """Find a 1-2-5-style scale and offset with >=1 division of breathing room.

    Use actual min/max so the fitted trace is not silently cropped. Returns
    (volts_per_division, vertical_midpoint) or None for invalid/no data.
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
    """Return fraction of sampled trace outside graticule and whether it clips.

    This denotes *display clipping*, not front-end ADC saturation.
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
    """Invert gain for a raw-channel span, ignoring constant offsets."""
    if not math.isfinite(gain) or abs(gain) <= 1e-15:
        return None
    values = np.asarray(calibrated_values, dtype=float)
    if values.size == 0 or not np.isfinite(values).all():
        return None
    return float(np.ptp(values) / abs(gain))


def suspicious_gain(gain):
    return not math.isfinite(gain) or gain <= 0 or gain > 5 or gain < .2
