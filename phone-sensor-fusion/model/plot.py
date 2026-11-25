"""Plot an inference result against the labels: speed, modes, distance per leg.

    python -m model.plot [name]     # name of the saved results, default "cv"

Reads data/phone-sensor-fusion/model/<id>.<name>.npz (written by pipeline.py), writes
<id>.<name>.png next to it.
"""

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from common import paths
from labels import load
from labels.frames import MODE_NAMES, frame_labels

COLORS = ["#dddddd", "#f4a261", "#2a9d8f", "#8ecae6", "#e76f51", "#e9c46a", "#6d597a"]


def plot(rid: str, name: str = "cv"):
    r = np.load(paths.derived("model", f"{rid}.{name}.npz"))
    lab = frame_labels(rid)
    t = np.arange(len(r["v"])) + 0.5
    fig, ax = plt.subplots(3, 1, figsize=(26, 12), sharex=True, gridspec_kw={"height_ratios": [4, 1, 1]})
    vl = np.where(np.isfinite(lab.v), lab.v, np.nan)
    ax[0].plot(t, np.where(lab.confident, vl, np.nan), "k", lw=2.2, label="label (confident)")
    ax[0].plot(t, vl, "k", lw=0.6, label="label (guess)")
    ax[0].fill_between(t, r["v"] - 2 * r["v_sd"], r["v"] + 2 * r["v_sd"], color="C0", alpha=0.25)
    ax[0].plot(t, r["v"], "C0", lw=1.2, label="model, smoothed (±2 sd)")
    ax[0].plot(t, r["v_filt"], "C3", lw=0.6, alpha=0.8, label="model, online filter")
    for st in load.stops(rid):
        ax[0].axvline(st.t_open, color="g", lw=0.5)
        ax[0].text(st.t_open, 39.5, st.station, fontsize=8, rotation=20)
    ax[0].set_ylabel("speed (m/s)"); ax[0].set_ylim(-1, 42); ax[0].legend(loc="upper right", ncol=4)
    bottom = np.zeros(len(t))
    for m in range(6):
        ax[1].fill_between(t, bottom, bottom + r["mode_p"][:, m], color=COLORS[m], step="mid", label=MODE_NAMES[m])
        bottom += r["mode_p"][:, m]
    ax[1].set_ylabel("P(mode)"); ax[1].legend(loc="upper right", ncol=6, fontsize=7)
    bottom = np.zeros(len(t))
    for k, (nm, col) in enumerate((("resting", "#264653"), ("held", "#e9c46a"), ("handled", "#e76f51"))):
        ax[2].fill_between(t, bottom, bottom + r["phone"][:, k], color=col, step="mid", label=nm)
        bottom += r["phone"][:, k]
    ax[2].set_ylabel("P(phone state)"); ax[2].legend(loc="upper right", ncol=3, fontsize=7)
    ax[2].set_xlabel("t (s)"); ax[2].set_xticks(np.arange(0, len(t), 60))
    for x in ax:
        x.grid(alpha=0.3)
    fig.suptitle(f"{rid}  ({name})")
    fig.tight_layout()
    out = paths.derived("model", f"{rid}.{name}.png")
    fig.savefig(out, dpi=65)
    plt.close(fig)
    return out


if __name__ == "__main__":
    for rid in paths.labelled_ids():
        print(plot(rid, sys.argv[1] if len(sys.argv) > 1 else "cv"))
