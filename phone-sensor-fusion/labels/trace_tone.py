"""Semi-automatic speed labels: trace the motor-tone ridge through each leg.

The traction tone (exploration/tone_vs_speed.py) is proportional to speed, but only partly
visible: faint below ~100 Hz and under speech. With hindsight we can still follow it: per
leg, dynamic programming finds the smoothest path through the spectrogram that starts at
0 Hz when the door-close chime ends and is back at 0 Hz when the door-open chime starts, and collects the most
tone prominence on the way (at the fundamental and at its 2.2x partial).

The traced frequency is turned into speed with one constant per leg, chosen so that the
leg's integral equals the map distance between its stations: that is what makes these
*labels* (they use the map, the whole leg and a human check of the overlay plots), not
something a model may use at inference.

Writes, per recording:
  <labels>/<id>/tone_trace.csv     t, f_hz, prominence_db, leg   (4 Hz)
  data/phone-sensor-fusion/labels/<id>.trace_<n>.png  overlays to verify by eye

    python -m labels.trace_tone [recording-id ...]
"""

import csv

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import ndimage, signal

from common import paths
from common.recording import load_all

FS, NWIN, NFFT, HOP = 16000, 4096, 16384, 4000  # 0.26 s window zero-padded to ~1 Hz bins; 4 frames/s
DF = FS / NFFT
F_MAX = 1010.0  # the tone tops out near 965 Hz at the 79 mph line limit
F_BLIND = 250.0  # below this the tone is lost in rumble and mains-like hums: no evidence
# there, so the path crosses it in a straight line continuing the slope seen above it
PARTIAL = 58.0 / 26.4  # second speed-proportional partial (ratio spectrum)
MAX_SLOPE = 8  # bins per frame: 31 Hz/s, i.e. ~1.2 m/s^2
MAX_JERK = 2  # change of slope per frame
CURVE = 3.0  # penalty per (change of slope)^2: trains hold a steady acceleration
F_RAMP = 450.0  # below this (17 m/s) a train between stations is always pulling away or braking:
MIN_RAMP = 2  # ... the slope there is at least this (0.3 m/s^2), so no plateaus or reversals
NO_TONE = 0.3  # cost per frame of being somewhere without tone (and not at 0 Hz)


def prominence(x):
    """(f, t, dB above the local-in-frequency median, also whitened per frequency)."""
    f, t, S = signal.spectrogram(x, fs=FS, nperseg=NWIN, nfft=NFFT, noverlap=NWIN - HOP)
    db = 10 * np.log10(S + 1e-16)
    db -= np.median(db, axis=1, keepdims=True)
    db -= ndimage.median_filter(db, size=(int(160 / DF) | 1, 1))
    return f, t, db


def trace(E, ramp_bins):
    """Best path through E[frame, bin] from bin 0 to bin 0. State = (bin, slope), and the
    slope may change by at most MAX_JERK per frame, at a cost: the path is smooth in its
    second difference, like a train's speed. Below `ramp_bins` it must keep climbing or
    keep falling (see F_RAMP)."""
    T, B = E.shape
    slopes = np.arange(-MAX_SLOPE, MAX_SLOPE + 1)
    nS = len(slopes)
    score = np.full((B, nS), -np.inf)
    score[0, MAX_SLOPE] = 0.0
    back = np.zeros((T, B, nS), dtype=np.int8)
    for t in range(1, T):
        # 1. change slope (in place, same bin)
        best = score.copy()
        arg = np.zeros((B, nS), dtype=np.int8)
        for d in range(-MAX_JERK, MAX_JERK + 1):
            if d == 0:
                continue
            cand = np.full((B, nS), -np.inf)
            if d > 0:
                cand[:, d:] = score[:, :-d] - CURVE * d * d
            else:
                cand[:, :d] = score[:, -d:] - CURVE * d * d
            better = cand > best
            best[better] = cand[better]
            arg[better] = d
        prev = np.arange(nS, dtype=np.int8)[None, :] - arg  # slope index before the change
        best[0, :] = score[0].max()  # standing still: free to pull away at any slope
        prev[0, :] = np.argmax(score[0])
        # 2. move by the (new) slope
        new = np.full((B, nS), -np.inf)
        for j, st in enumerate(slopes):
            if st >= 0:
                new[st:, j] = best[: B - st, j]
            else:
                new[:st, j] = best[-st:, j]
        score = new + E[t][:, None]
        score[1:ramp_bins, np.abs(slopes) < MIN_RAMP] = -np.inf
        back[t] = prev
    b, j = 0, int(np.argmax(score[0]))
    path = np.zeros(T, dtype=int)
    for t in range(T - 1, 0, -1):
        path[t] = b
        b -= slopes[j]
        j = back[t, b, j]
    return path


def main(only=()):
    for rec in load_all():
        if only and rec.id not in only:
            continue
        stops = list(csv.DictReader((paths.LABELS / rec.id / "stops.csv").open()))
        t0, x = rec.audio()
        f, t, prom = prominence(np.asarray(x))
        t = t + t0
        nb = int(F_MAX / DF)
        # a frame full of peaks (speech, clatter) says little: count only what stands above
        # the frame's own 90th percentile
        busy = np.percentile(prom[: 2 * nb], 90, axis=0, keepdims=True)
        fund = (prom[:nb] - busy).clip(0, 15)
        idx2 = np.round(np.arange(nb) * PARTIAL).astype(int)
        E_all = fund + 0.5 * (prom[idx2] - busy).clip(0, 15)
        E_all -= NO_TONE
        E_all[f[:nb] < F_BLIND] = 0.0
        E_all[0] = 0.0
        rows = []
        for n in range(len(stops) - 1):
            # wheels can't turn before the door-close chime has ended, and are still by the
            # time the door-open chime starts
            a, b = float(stops[n]["t_close_end"]), float(stops[n + 1]["t_open"])
            m = (t >= a) & (t <= b)
            path = trace(E_all[:, m].T.copy(), int(F_RAMP / DF))
            fp = f[path]
            pr = fund[path, np.where(m)[0]]
            leg = f"{stops[n]['station']} > {stops[n+1]['station']}"
            rows += [(tt, ff, pp, leg) for tt, ff, pp in zip(t[m], fp, pr)]
            fig, ax = plt.subplots(2, 1, figsize=(22, 11), sharex=True, sharey=True)
            for x_ in ax:
                x_.pcolormesh(t[m], f[: 2 * nb], prom[: 2 * nb, m], shading="auto", cmap="magma", vmin=0, vmax=15)
            ax[1].plot(t[m], fp, "c", lw=0.8)
            ax[1].plot(t[m], fp * PARTIAL, "c", lw=0.5, alpha=0.6)
            ax[0].set_title(f"{rec.id}  {leg}")
            ax[0].set_xticks(np.arange(np.ceil(a / 5) * 5, b, 5))
            for x_ in ax:
                x_.grid(alpha=0.3)
            fig.tight_layout()
            fig.savefig(paths.derived("labels", f"{rec.id}.trace_{n:02d}.png"), dpi=55)
            plt.close(fig)
        with (paths.LABELS / rec.id / "tone_trace.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["t", "f_hz", "prominence_db", "leg"])
            for tt, ff, pp, leg in rows:
                w.writerow([f"{tt:.2f}", f"{ff:.1f}", f"{pp:.1f}", leg])
        print(rec.id, len(rows))


if __name__ == "__main__":
    import sys

    main(sys.argv[1:])
