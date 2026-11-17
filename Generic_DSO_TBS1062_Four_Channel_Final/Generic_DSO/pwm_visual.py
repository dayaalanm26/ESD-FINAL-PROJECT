"""Measured-PWM trace presentation. Never creates or changes acquisition samples."""
import numpy as np


def looks_like_pwm(y):
    """Only use edge-hold drawing when the measured values are two-level-like."""
    y=np.asarray(y, dtype=float).reshape(-1)
    if len(y)<6 or not np.all(np.isfinite(y)):
        return False
    low=float(np.min(y)); high=float(np.max(y)); span=high-low
    if span<0.28:  # avoid amplifying DC noise into imaginary digital edges
        return False
    low_group=np.abs(y-low)<span*.16
    high_group=np.abs(y-high)<span*.16
    if not low_group.any() or not high_group.any():
        return False
    # Need mostly two stable voltage levels, not a sinusoid or ramp.
    return float(np.mean(low_group|high_group))>=.86


def pwm_hold_trace(t,y):
    """Draw vertical edges between consecutive ACTUAL sampled states.

    Add two visual display vertices at the midpoint of each pair of sample
    timestamps. This is a presentation of time uncertainty (± half a sample
    interval), not reconstruction of analog edge timing or additional data.
    Hardware timestamps/values in the capture and CSV remain untouched.
    """
    t=np.asarray(t,dtype=float).reshape(-1)
    y=np.asarray(y,dtype=float).reshape(-1)
    if len(t)!=len(y):
        raise ValueError('Time and voltage arrays must have equal length')
    if len(t)<2:
        return t.copy(),y.copy()
    if np.any(np.diff(t)<=0):
        raise ValueError('Sample times must strictly increase')
    # A sample-and-hold step plot, with transitions halfway between samples.
    # 2n vertices and no staircase interpolation/overshoot.
    transitions=(t[:-1]+t[1:])*.5
    tx=np.empty(2*len(t),dtype=float)
    vy=np.empty(2*len(y),dtype=float)
    tx[0]=t[0]; vy[0]=y[0]
    tx[1:-1:2]=transitions; tx[2::2]=transitions
    vy[1:-1:2]=y[:-1]; vy[2::2]=y[1:]
    tx[-1]=t[-1];vy[-1]=y[-1]
    return tx,vy
