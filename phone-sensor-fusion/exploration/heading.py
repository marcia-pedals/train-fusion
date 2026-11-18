"""Can the phone tell which way the train is pointing?

For the stretches the phone lay still, the train's forward direction in the phone's own
axes is known from the accelerometer (exploration/newdata.py). Turning that into a compass
heading takes the phone's attitude; three sources are compared against the track's
heading at the labelled position:

  ios      iOS's fused attitude (gyro + accelerometer + magnetometer; device_motion.csv)
  mag      the calibrated magnetic field alone, tilt-compensated with gravity, no gyro
  gyro     no compass at all: the heading *change* since the start of the stretch from the
           gyro, compared with the track's heading change (tests curves, not north)

    python -m exploration.heading
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from common import paths
from common.recording import Recording
from exploration.newdata import DECLINATION_DEG, NEW, circ_diff, forward_axes, per_second, quat_rotate, truth
from maps import track as trackmod


def stats(name, err, moving):
    for label, m in (("moving", moving), ("standing", ~moving)):
        e = err[m & np.isfinite(err)]
        if len(e) < 20:
            continue
        print(f"    {name:5s} {label:8s} n={len(e):4d}  median {np.median(e):+6.1f}°  spread (16-84%) ±{(np.percentile(e, 84) - np.percentile(e, 16)) / 2:5.1f}°"
              f"  |err|<20°: {np.mean(np.abs(e) < 20):4.0%}  <45°: {np.mean(np.abs(e) < 45):4.0%}  >90° (points the wrong way): {np.mean(np.abs(e) > 90):4.0%}")


def main():
    tr = trackmod.load()
    fig, axes = plt.subplots(len(NEW), 1, figsize=(24, 5.5 * len(NEW)))
    for ax, rid in zip(axes, NEW):
        rec = Recording(rid)
        lab, pos, direction = truth(rid)
        T = len(lab.v)
        dm = rec.device_motion
        q = per_second(dm["t"], dm["q"], T)
        q /= np.linalg.norm(q, axis=1, keepdims=True)
        B = per_second(dm["t"], dm["mag"], T)
        grav = per_second(dm["t"], dm["gravity"], T)
        rot = per_second(dm["t"], dm["rot"], T)
        acc_ok = per_second(dm["t"], dm["mag_accuracy"], T)
        want = tr.heading_at(np.nan_to_num(pos), direction)
        want[~np.isfinite(pos)] = np.nan
        h_ios, h_mag, h_gyro = (np.full(T, np.nan) for _ in range(3))
        # which way does the quaternion go? the one that sends measured gravity to straight down
        g_ref = quat_rotate(q, grav)
        inverse = np.nanmean(g_ref[:, 2]) > np.nanmean(quat_rotate(q, grav, inverse=True)[:, 2])
        for t0, t1, fwd, up in forward_axes(rid):
            sl = slice(t0, t1)
            n = t1 - t0
            f_ref = quat_rotate(q[sl], np.tile(fwd, (n, 1)), inverse=inverse)
            h_ios[sl] = (np.degrees(np.arctan2(-f_ref[:, 1], f_ref[:, 0])) + DECLINATION_DEG) % 360  # x north, y west
            upv = -grav[sl] / np.linalg.norm(grav[sl], axis=1, keepdims=True)
            north = B[sl] - np.sum(B[sl] * upv, axis=1, keepdims=True) * upv
            east = np.cross(north, upv)
            fh = fwd - (upv @ fwd)[:, None] * upv
            h_mag[sl] = (np.degrees(np.arctan2(np.sum(fh * east, axis=1), np.sum(fh * north, axis=1))) + DECLINATION_DEG) % 360
            yaw = np.sum(rot[sl] * upv, axis=1)  # + = turning left = heading decreasing
            first = np.flatnonzero(np.isfinite(want[sl]))
            if len(first):
                h_gyro[sl] = (want[sl][first[0]] - np.degrees(np.cumsum(np.nan_to_num(yaw)) - np.cumsum(np.nan_to_num(yaw))[first[0]])) % 360
        moving = lab.v > 2
        print(f"## {rid}  ({'south' if direction > 0 else 'north'}bound; iOS says magnetometer accuracy "
              f"{np.nanmedian(acc_ok[np.isfinite(h_ios)]):.0f} of 2 on the train)")
        for name, h in (("ios", h_ios), ("mag", h_mag), ("gyro", h_gyro)):
            stats(name, circ_diff(h, want), moving)
        ok = np.isfinite(h_ios) & np.isfinite(want)
        print(f"    north or south? mean cos(error): ios {np.mean(np.cos(np.radians(circ_diff(h_ios, want)[ok]))):+.2f}"
              f"  mag {np.mean(np.cos(np.radians(circ_diff(h_mag, want)[ok]))):+.2f}   (+1 = always right way, -1 = always backwards)")
        t = np.arange(T) + 0.5
        ax.plot(t, want, "k", lw=2, label="track heading at the labelled position")
        ax.plot(t, h_ios, "C0", lw=0.8, label="iOS fused attitude")
        ax.plot(t, h_mag, "C1", lw=0.6, alpha=0.8, label="magnetic field + gravity")
        ax.plot(t, h_gyro, "C2", lw=0.8, label="gyro only (anchored at the stretch start)")
        ax.set_ylim(0, 360); ax.set_yticks(range(0, 361, 45)); ax.set_ylabel("heading of travel (° true)")
        ax.set_title(rid); ax.grid(alpha=0.3); ax.legend(loc="upper right", ncol=4)
        m = np.isfinite(want)
        ax.set_xlim(t[m][0] - 20, t[m][-1] + 20)
    fig.tight_layout()
    out = paths.derived("exploration", "heading.png")
    fig.savefig(out, dpi=60)
    print(out)
    # how much does heading say about position?
    # (over the stretch of line the first of these rides covers, from its labels)
    from labels import load
    ridden = [load.station_s()[st.station] for st in load.stops(NEW[0])]
    s = tr.s[(tr.s > min(ridden)) & (tr.s < max(ridden))]
    h = tr.heading_at(s, 1)
    print(f"track heading over the stretch ridden: {h.min():.0f}°..{h.max():.0f}° southbound; "
          f"per km it changes by a median of {np.median(np.abs(np.diff(h[::100]))):.1f}°")
    for sd in (3, 10, 25):
        frac = np.mean([np.mean(np.abs(circ_diff(h, x)) < sd) for x in h[::20]])
        print(f"   a heading good to ±{sd}° leaves {frac:.0%} of that stretch of line possible")


if __name__ == "__main__":
    main()
