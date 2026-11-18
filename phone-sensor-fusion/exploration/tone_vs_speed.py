"""Is there a motor tone proportional to speed, and what are its harmonics?

Uses a recording where the phone lay still on a surface (give its id), on the legs with no
handling. Speed comes from the accelerometer alone: integrate the along-track acceleration
from wheel-start to wheel-stop, with the zero level interpolated linearly between the two
dwells (so v returns to 0). Then:

 1. distance check: the integral of that speed against the map's station spacing;
 2. the audio spectrogram is resampled onto f / v ("Hz per m/s"): anything proportional
    to speed becomes a horizontal line, and averaging over time gives a "ratio spectrum"
    whose peaks are the speed-proportional partials;
 3. the same for the accelerometer spectrogram (wheel-rotation harmonics).

Writes figures to data/phone-sensor-fusion/exploration/.

    python -m exploration.tone_vs_speed
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import sys

import numpy as np
from scipy import signal

from common import paths
from common.recording import Recording
from labels import load
from labels.build_speed import MARGIN_S, accel_leg

REC = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: python -m exploration.tone_vs_speed <recording id>")


def leg_speeds(rec):
    """[(t, v m/s, name, map distance)] for the legs the phone lay still through, from the
    accelerometer alone (labels/build_speed.py does the integration)."""
    stops, s_of = load.stops(rec.id), load.station_s()
    resting = [(a, b) for a, b, st in load.phone(rec.id) if st == "resting"]
    out = []
    for p, q in zip(stops[:-1], stops[1:]):
        still = next(((a, b) for a, b in resting
                      if a <= p.t_depart - MARGIN_S and q.t_arrive + MARGIN_S <= b), None)
        if still:
            tt, v = accel_leg(rec, still, p.t_depart, q.t_arrive)
            out.append((tt, v, f"{p.station} > {q.station}", abs(s_of[p.station] - s_of[q.station])))
    return out


def ratio_spectrum(tt_v, v, t_spec, f, db, ratios, vmin):
    """Mean dB over time of the spectrogram sampled at f = ratio * v(t), for v > vmin."""
    vi = np.interp(t_spec, tt_v, v, left=0, right=0)
    cols = np.where(vi > vmin)[0]
    acc = np.zeros(len(ratios))
    n = np.zeros(len(ratios))
    for c in cols:
        fq = ratios * vi[c]
        ok = fq < f[-1]
        acc[ok] += np.interp(fq[ok], f, db[:, c])
        n[ok] += 1
    return acc / np.maximum(n, 1), n


def main():
    rec = Recording(REC)
    legs = leg_speeds(rec)
    print("accelerometer-only odometry vs map:")
    for tt, v, name, dist in legs:
        d = np.sum(v) * np.median(np.diff(tt))
        print(f"  {name:32s} vmax {v.max():5.1f} m/s  integral {d:6.0f} m  map {dist:5.0f} m  ratio {d/dist:.3f}")

    ta0, x = rec.audio()
    f, ts, S = signal.spectrogram(np.asarray(x), fs=16000, nperseg=4096, noverlap=4096 - 1024)
    ts = ts + ta0
    db = 10 * np.log10(S + 1e-16)
    db -= np.median(db, axis=1, keepdims=True)  # whiten per frequency
    ratios = np.arange(2, 120, 0.1)
    tacc, a = rec.accel
    fa, tsa, Sa = signal.spectrogram(np.linalg.norm(a, axis=1), fs=100, nperseg=512, noverlap=384, detrend="constant")
    tsa = tsa + tacc[0]
    dba = 10 * np.log10(Sa + 1e-16)
    dba -= np.median(dba, axis=1, keepdims=True)
    ratios_a = np.arange(0.05, 4, 0.005)

    fig, ax = plt.subplots(2, 1, figsize=(22, 10))
    tot = np.zeros(len(ratios)); tot_a = np.zeros(len(ratios_a))
    for tt, v, name, dist in legs:
        scale = dist / (np.sum(v) * np.median(np.diff(tt)))  # use the map to fix accelerometer scale
        r, _ = ratio_spectrum(tt, v * scale, ts, f, db, ratios, vmin=8)
        ax[0].plot(ratios, r, lw=0.7, label=name)
        tot += r
        r, _ = ratio_spectrum(tt, v * scale, tsa, fa, dba, ratios_a, vmin=8)
        ax[1].plot(ratios_a, r, lw=0.7, label=name)
        tot_a += r
    ax[0].plot(ratios, tot / len(legs), "k", lw=1.5, label="mean")
    ax[1].plot(ratios_a, tot_a / len(legs), "k", lw=1.5, label="mean")
    ax[0].set_xlabel("audio frequency / speed  (Hz per m/s)"); ax[0].set_ylabel("mean dB over median")
    ax[1].set_xlabel("accelerometer frequency / speed  (Hz per m/s)")
    ax[0].set_xticks(np.arange(0, 120, 5)); ax[1].set_xticks(np.arange(0, 4, 0.25))
    for x_ in ax:
        x_.grid(alpha=0.3); x_.legend(fontsize=8)
    fig.tight_layout()
    out = paths.derived("exploration", "ratio_spectrum.png")
    fig.savefig(out, dpi=70)
    print(out)
    m = tot / len(legs)
    pk, _ = signal.find_peaks(m, prominence=1.0)
    print("audio partials (Hz per m/s, dB):", [(round(ratios[i], 1), round(m[i], 1)) for i in pk])
    m = tot_a / len(legs)
    pk, _ = signal.find_peaks(m, prominence=1.0)
    print("accel partials (Hz per m/s, dB):", [(round(ratios_a[i], 3), round(m[i], 1)) for i in pk])


if __name__ == "__main__":
    main()
