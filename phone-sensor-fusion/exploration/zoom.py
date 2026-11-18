"""Zoom into a time window of a recording: audio spectrogram (linear frequency, fine time
resolution) over the accelerometer and gyro. The main tool for manual labelling.

    python -m exploration.zoom <recording-id> <t_start> <t_end> [fmax_hz] [out.png]
"""

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal

from common import paths
from common.recording import Recording


def plot(rec: Recording, t0: float, t1: float, fmax: float = 8000, out=None, nfft=1024):
    ta0, x = rec.audio()
    fs = 16000
    i0, i1 = max(0, int((t0 - ta0) * fs)), int((t1 - ta0) * fs)
    seg = np.asarray(x[i0:i1])
    hop = max(nfft // 8, int(len(seg) / 2400))
    f, tt, S = signal.spectrogram(seg, fs=fs, nperseg=nfft, noverlap=nfft - hop)
    fig, ax = plt.subplots(4, 1, figsize=(26, 16), sharex=True,
                           gridspec_kw={"height_ratios": [4, 1, 1, 1]})
    db = 10 * np.log10(S + 1e-16)
    # per-frequency median removed: steady hum fades, events stand out
    ax[0].pcolormesh(tt + ta0 + i0 / fs, f, db - np.median(db, axis=1, keepdims=True),
                     shading="auto", cmap="magma", vmin=-5, vmax=25)
    ax[0].set_ylim(0, fmax); ax[0].set_ylabel("audio (Hz), dB over median")
    tacc, a = rec.accel
    m = (tacc >= t0) & (tacc <= t1)
    sos = signal.butter(2, 1.0, fs=100, output="sos")
    al = signal.sosfiltfilt(sos, a, axis=0)
    for i, c in enumerate("xyz"):
        ax[1].plot(tacc[m], al[m, i] - np.median(al[m, i]), lw=0.8, label=c)
    ax[1].set_ylabel("accel <1Hz − median (g)"); ax[1].legend(loc="upper right")
    ax[2].plot(tacc[m], np.linalg.norm(a[m] - al[m], axis=1), lw=0.4)
    ax[2].set_yscale("log"); ax[2].set_ylabel("|accel >1Hz| (g)")
    tg, g = rec.gyro
    m = (tg >= t0) & (tg <= t1)
    for i, c in enumerate("xyz"):
        ax[3].plot(tg[m], g[m, i], lw=0.5, label=c)
    lim = max(0.02, min(0.5, np.percentile(np.abs(g[m]), 99)))
    ax[3].set_ylim(-lim, lim); ax[3].set_ylabel("gyro (rad/s)")
    step = max(1, round((t1 - t0) / 60))
    ax[3].set_xticks(np.arange(np.ceil(t0 / step) * step, t1, step))
    for x_ in ax:
        x_.grid(alpha=0.3)
    ax[0].set_title(f"{rec.id}  {t0:.0f}–{t1:.0f} s")
    fig.tight_layout()
    out = out or paths.derived("exploration", f"{rec.id}.zoom_{t0:.0f}_{t1:.0f}.png")
    fig.savefig(out, dpi=70)
    plt.close(fig)
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    print(plot(Recording(a[0]), float(a[1]), float(a[2]), float(a[3]) if len(a) > 3 else 8000,
               a[4] if len(a) > 4 else None))
