"""Do curves, switches and level crossings on the map show up in the IMU?

A recording with the phone resting (give its id). Position along the line comes from the labels: each
leg's labelled speed integrated from its origin station. Then:

 1. yaw rate about the vertical against speed x map curvature (should be equal);
 2. vibration (10-45 Hz accelerometer level, 1 s frames) within 30 m of a mapped switch
    or level crossing against the same speeds elsewhere.

Writes data/phone-sensor-fusion/exploration/track_features.png.

    python -m exploration.track_features
"""

import csv

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import sys

import numpy as np

from common import paths
from features import frames, motion
from labels import load
from labels.frames import frame_labels
from maps import track as trackmod

REC = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: python -m exploration.track_features <recording id>")
NEAR_M = 30.0


def main():
    tr = trackmod.load()
    f, lab = frames.load(REC), frame_labels(REC)
    stops, s_of = load.stops(REC), load.station_s()
    direction = 1 if load.journey(REC)["direction"] == "south" else -1
    T = len(lab.v)
    pos = np.full(T, np.nan)
    for p, q in zip(stops[:-1], stops[1:]):
        m = np.flatnonzero(lab.leg == f"{p.station} > {q.station}")
        pos[m] = s_of[p.station] + direction * np.cumsum(lab.v[m])
    resting = (lab.phone == 0).astype(float)
    mo = motion.extract(f, resting)
    pred = direction * lab.v * tr.curvature_at(np.nan_to_num(pos))
    # the phone was nudged a few times (spikes of > 0.03 rad/s): leave those seconds out
    ok = np.isfinite(pos) & np.isfinite(mo["yaw_rate"]) & (lab.v > 5) & (np.abs(mo["yaw_rate"]) < 0.03)
    r = np.corrcoef(mo["yaw_rate"][ok], pred[ok])[0, 1]
    slope = np.polyfit(pred[ok], mo["yaw_rate"][ok], 1)[0]
    res_y = mo["yaw_rate"][ok] - pred[ok]
    print(f"yaw vs speed x curvature: r = {r:.3f}, slope = {slope:.2f}, residual sd = {np.std(res_y):.4f}"
          f" (robust {1.4826 * np.median(np.abs(res_y - np.median(res_y))):.4f}) rad/s over {ok.sum()} s")

    points = list(csv.DictReader((trackmod.DATA / "points.csv").open()))
    vib = f["vib_db"][:, 1]
    okv = np.isfinite(pos) & (lab.phone == 0) & (lab.v > 10)
    # remove the speed dependence: residual from a quadratic in speed
    coef = np.polyfit(lab.v[okv], vib[okv], 2)
    res = vib - np.polyval(coef, lab.v)
    for kind in ("switch", "level_crossing"):
        ps = np.array([float(p["s_m"]) for p in points if p["kind"] == kind])
        near = np.array([np.min(np.abs(ps - x)) < NEAR_M if np.isfinite(x) else False for x in pos])
        a, b = res[okv & near], res[okv & ~near]
        print(f"{kind:15s}: {len(ps)} on the map; vibration within {NEAR_M:.0f} m: {a.mean():+.2f} dB "
              f"(n={len(a)}) vs elsewhere {b.mean():+.2f} dB (n={len(b)}), sd {b.std():.1f} dB")

    fig, ax = plt.subplots(2, 1, figsize=(24, 9), sharex=True)
    t = np.arange(T) + 0.5
    ax[0].plot(t, np.where(np.isfinite(pos), pred, np.nan), "k", lw=1.5, label="speed × map curvature")
    ax[0].plot(t, mo["yaw_rate"], "C3", lw=0.8, label="gyro about the vertical")
    ax[0].set_ylabel("yaw rate (rad/s)"); ax[0].legend(); ax[0].set_ylim(-0.03, 0.03)
    ax[1].plot(t, np.where(okv, res, np.nan), "C0", lw=0.6)
    for p in points:
        x = float(p["s_m"])
        i = np.nanargmin(np.abs(pos - x))
        if abs(pos[i] - x) < 15:
            ax[1].axvline(t[i], color="C1" if p["kind"] == "switch" else "C2", lw=0.6, alpha=0.7)
    ax[1].set_ylabel("vibration 10–45 Hz, speed trend removed (dB)\norange: switch, green: level crossing")
    ax[1].set_xlabel("t (s)")
    for x in ax:
        x.grid(alpha=0.3)
    fig.tight_layout()
    out = paths.derived("exploration", "track_features.png")
    fig.savefig(out, dpi=60)
    print(out)


if __name__ == "__main__":
    main()
