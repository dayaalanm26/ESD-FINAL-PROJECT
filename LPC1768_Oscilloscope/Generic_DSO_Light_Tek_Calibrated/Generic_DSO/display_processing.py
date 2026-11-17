"""Processing of received records; never generates a runtime signal source."""
from collections import deque
import numpy as np


def display_interpolate(t, y, pixels, method='Sin(x)/x'):
    """Windowed sinc on a display copy, only for sparse, uniformly timed data.

    All original samples remain in the display path, preserving extrema. Dense
    traces are left to pyqtgraph's peak downsampler. Irregular data uses linear
    segments because sinc interpolation assumes a uniform sampling interval.
    """
    t, y = np.asarray(t), np.asarray(y)
    if method != 'Sin(x)/x' or len(t) < 3 or len(t) >= 4*max(1,pixels):
        return t, y
    dt = np.diff(t); step = float(np.median(dt))
    if step <= 0 or np.max(np.abs(dt-step)) > step*.01:
        return t, y
    count = min(16000, max(len(t), int(pixels)*2))
    targets = np.linspace(t[0],t[-1],count)
    u = (targets-t[0])/step
    base = np.floor(u).astype(int)
    indices = base[:,None]+np.arange(-7,9)[None,:]
    delta = u[:,None]-indices
    valid = (indices>=0)&(indices<len(y))&(np.abs(delta)<8)
    weights = np.sinc(delta)*np.sinc(delta/8)*valid
    denom = weights.sum(axis=1)
    values = (weights*y[np.clip(indices,0,len(y)-1)]).sum(axis=1)/np.where(abs(denom)>1e-12,denom,1)
    # Keep exact sample values, even at isolated spikes.
    times = np.concatenate((targets,t)); vals = np.concatenate((values,y))
    order = np.argsort(times,kind='stable')
    return times[order],vals[order]


class AcquisitionProcessor:
    def __init__(self): self.reset()

    def reset(self):
        self.records=deque(); self.signature=None; self.count=0

    def process(self,capture,mode='Sample',average_n=16,hires_width=4):
        raw=capture.y
        if mode=='Sample' or len(raw)==0:
            self.reset(); return raw.copy()
        if mode=='Hi-Res':
            self.reset()
            width=min(max(2,int(hires_width)),len(raw))
            if width<2: return raw.copy()
            before=(width-1)//2; after=width-1-before
            padded=np.pad(raw,((before,after),(0,0)),mode='edge')
            return np.column_stack([np.convolve(padded[:,i],np.ones(width)/width,mode='valid') for i in range(raw.shape[1])])
        # Never combine an automatic free-run record with triggered acquisitions.
        if not capture.triggered:
            self.reset(); return raw.copy()
        dt=np.diff(capture.t)
        step=float(np.median(dt)) if len(dt) else 0.
        if step<=0 or np.max(abs(dt-step))>step*.01:
            self.reset(); return raw.copy()
        signature=(raw.shape,round(step,12),int(average_n))
        if signature!=self.signature: self.reset(); self.signature=signature
        # Resample only the processing copy onto the newest trigger-relative grid
        # to account for the sub-sample phase of an interpolated trigger crossing.
        self.records.append((capture.t.copy(),raw.copy()))
        while len(self.records)>average_n: self.records.popleft()
        self.count=len(self.records)
        aligned=[np.column_stack([np.interp(capture.t,t,y[:,i]) for i in range(raw.shape[1])]) for t,y in self.records]
        return np.mean(aligned,axis=0)
