"""Small signal-processing helpers shared by labels/ and features/."""

import numpy as np
from scipy import ndimage, signal


def spectrogram_db(x, fs, nwin, hop, nfft=None):
    """(f, dB[f, frame]); frame i covers samples [i*hop, i*hop + nwin)."""
    f, _, S = signal.spectrogram(x, fs=fs, nperseg=nwin, nfft=nfft or nwin, noverlap=nwin - hop)
    return f, 10 * np.log10(S + 1e-16)


def band_prominence(db, f, fc, half=25.0, bg=(100.0, 400.0)):
    """Per frame: dB of the strongest bin within +-half Hz of fc, over the median of the
    bins bg[0]..bg[1] Hz away on either side. A pure tone at fc scores high; broadband
    noise and speech score ~0."""
    inb = np.abs(f - fc) <= half
    side = (np.abs(f - fc) >= bg[0]) & (np.abs(f - fc) <= bg[1])
    return db[inb].max(axis=0) - np.median(db[side], axis=0)


def line_prominence(db, f, bg_hz):
    """dB above the median of the surrounding +-bg_hz/2: narrow spectral lines stand out,
    the smooth spectrum shape is removed. Causal (uses only the frame itself)."""
    k = int(bg_hz / (f[1] - f[0])) | 1
    return db - ndimage.median_filter(db, size=(k, 1), mode="nearest")


def quat_rotate(q, v):
    """Rotate vectors v [n,3] (or one [3]) by unit quaternions q [n,4] given as (x, y, z, w)."""
    v = np.broadcast_to(v, (len(q), 3))
    u, w = q[:, :3], q[:, 3:4]
    t = 2 * np.cross(u, v)
    return v + w * t + np.cross(u, t)
