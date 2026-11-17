"""Transparent two-reference-point calibration helpers, independent of the GUI."""
from dataclasses import dataclass
import math
import numpy as np


@dataclass(frozen=True)
class CalibrationResult:
    gain: float
    offset: float
    measured_low: float
    measured_high: float
    measured_span: float
    gain_multiplier: float
    suspicious: bool


def estimate_plateaus(samples):
    """Use middle portions of both square-wave plateaus (reject edge overshoot).

    Caller must ensure the captured waveform is from the specified known two-level
    source. This is NOT a substitute for calibrated reference equipment.
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
    """Return new full conversion coefficients for transmitted raw channel units.

    Captures already contain raw*current_gain+current_offset, so invert that first.
    """
    for v in (current_gain,current_offset,reference_low,reference_high):
        if not math.isfinite(v): raise ValueError('Calibration values must be finite.')
    if abs(current_gain) < 1e-15: raise ValueError('Existing gain must be non-zero.')
    if reference_high <= reference_low: raise ValueError('Reference HIGH must exceed LOW.')
    already_calibrated = np.asarray(samples, dtype=float)
    original = (already_calibrated - current_offset)/current_gain
    observed_low, observed_high = estimate_plateaus(original)
    difference = observed_high - observed_low
    if abs(difference) < 1e-8: raise ValueError('Measured low and high levels are indistinguishable.')
    gain = (reference_high-reference_low)/difference
    offset = reference_low-gain*observed_low
    if not (math.isfinite(gain) and math.isfinite(offset)):
        raise ValueError('Calibration overflow.')
    multiplier = gain/current_gain
    # >5x is suspect; a tiny voltage with nominal 3.3V input is usually a wiring
    # or transmission-unit fault, not ordinary calibration error.
    suspicious = abs(multiplier) > 5 or abs(multiplier) < .2
    return CalibrationResult(float(gain),float(offset),observed_low,observed_high,
                             difference,float(multiplier),suspicious)
