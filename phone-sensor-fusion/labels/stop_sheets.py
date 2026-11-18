"""Contact sheets for labelling when the wheels stop and start at each station.

One row per door-chime candidate (labels/propose.py): left, the 30 s before the door-open
chime (arrival); right, the 30 s from the door-close chime (departure). Each panel shows
the low audio spectrum (the motor tone runs down to / up from 0 Hz), and under it the
low-passed accelerometer (the brake-release jerk / the pull-away) and the high-passed
accelerometer and audio rumble levels.

    python -m labels.stop_sheets <recording-id> "<open times>" "<close times>"
"""

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal

from common import paths
from common.recording import Recording


def panel(ax_s, ax_a, rec, f, tt, db, t0, t1, mark):
    m = (tt >= t0) & (tt <= t1)
    ax_s.pcolormesh(tt[m], f, db[:, m], shading="auto", cmap="magma", vmin=-5, vmax=22)
    ax_s.axvline(mark, color="cyan", lw=0.8)
    ta, a = rec.accel
    k = (ta >= t0) & (ta <= t1)
    sos = signal.butter(2, 0.7, fs=100, output="sos")
    al = signal.sosfiltfilt(sos, a, axis=0)
    for i, c in enumerate("xyz"):
        ax_a.plot(ta[k], al[k, i] - np.median(al[k, i]), lw=0.8, label=c)
    ax_a.set_ylim(-0.12, 0.12)
    ax2 = ax_a.twinx()
    hp = np.linalg.norm(a - al, axis=1)
    sm = signal.sosfiltfilt(signal.butter(2, 1.0, fs=100, output="sos"), hp)
    ax2.semilogy(ta[k], sm[k].clip(1e-4), color="k", lw=0.6)
    ax2.set_ylim(1e-3, 1)
    for ax in (ax_s, ax_a):
        ax.set_xlim(t0, t1)
        ax.set_xticks(np.arange(np.ceil(t0), t1, 2))
        ax.set_xticks(np.arange(np.ceil(t0), t1, 1), minor=True)
        ax.grid(alpha=0.4, which="both")
        ax.tick_params(labelsize=7)
    ax_a.axvline(mark, color="c", lw=0.8)


def main(rid, opens, closes):
    rec = Recording(rid)
    ta0, x = rec.audio()
    f, tt, S = signal.spectrogram(np.asarray(x), fs=16000, nperseg=2048, noverlap=2048 - 512)
    keep = f <= 700
    f, S = f[keep], S[keep]
    db = 10 * np.log10(S + 1e-16)
    db -= np.median(db, axis=1, keepdims=True)
    tt = tt + ta0
    n = max(len(opens), len(closes))
    fig, ax = plt.subplots(2 * n, 2, figsize=(24, 4.2 * n),
                           gridspec_kw={"height_ratios": [2, 1] * n})
    for i in range(n):
        if i < len(opens):
            panel(ax[2 * i, 0], ax[2 * i + 1, 0], rec, f, tt, db, opens[i] - 30, opens[i] + 2, opens[i])
            ax[2 * i, 0].set_title(f"arrive before open {opens[i]:.1f}", fontsize=9)
        if i < len(closes):
            panel(ax[2 * i, 1], ax[2 * i + 1, 1], rec, f, tt, db, closes[i] - 2, closes[i] + 30, closes[i])
            ax[2 * i, 1].set_title(f"depart after close {closes[i]:.1f}", fontsize=9)
    fig.tight_layout()
    out = paths.derived("labels", f"{rid}.stops.png")
    fig.savefig(out, dpi=60)
    print(out)


if __name__ == "__main__":
    main(sys.argv[1], [float(v) for v in sys.argv[2].split()], [float(v) for v in sys.argv[3].split()])
