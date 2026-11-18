"""Does iOS's fused device motion (attitude, gravity-free "user" acceleration,
bias-corrected rotation rate) do better than what we derive from the raw sensors?

 1. Along-track acceleration against the labelled speed change, phone resting:
      raw     what the model uses: raw accelerometer, principal axis per resting stretch
      user    the same recipe on iOS's user acceleration
      world   iOS's user acceleration turned into the world frame with iOS's attitude and
              projected on the track's direction at the labelled position: no stretches,
              no principal axis, nothing to re-learn when the phone is moved
 2. Odometry from `world` alone, wheel-start to wheel-stop (v forced to 0 at both ends),
    against the map: on legs the phone lay still through, and on the legs it did not
    (picked up mid-leg; first leg while walking to a seat; last leg walking to the door),
    which the raw recipe cannot do at all.
 3. Yaw rate against speed x track curvature: raw gyro about our gravity estimate, and
    iOS's rotation rate about iOS's gravity.

    python -m exploration.fused
"""

import numpy as np

from common.recording import Recording
from exploration.newdata import DECLINATION_DEG, NEW, per_second, quat_rotate, truth
from features import frames, motion
from labels import load
from maps import track as trackmod

G = 9.80665


def resid(a, dv, m):
    """sd of a - (slope * dv + offset) with slope and offset fitted on m; and the slope."""
    A = np.c_[dv[m], np.ones(m.sum())]
    coef, *_ = np.linalg.lstsq(A, a[m], rcond=None)
    return float(np.std(a[m] - A @ coef)), float(coef[0])


def main():
    tr = trackmod.load()
    s_of = load.station_s()
    for rid in NEW:
        rec = Recording(rid)
        lab, pos, direction = truth(rid)
        T = len(lab.v)
        dm = rec.device_motion
        q = dm["q"] / np.linalg.norm(dm["q"], axis=1, keepdims=True)
        g_ref = quat_rotate(q[::50], dm["gravity"][::50])
        inverse = g_ref[:, 2].mean() > quat_rotate(q[::50], dm["gravity"][::50], inverse=True)[:, 2].mean()
        a_world = per_second(dm["t"], -quat_rotate(q, dm["user_accel"], inverse=inverse) * G, T)  # true acceleration, x north, y west, z up
        ua = per_second(dm["t"], dm["user_accel"], T)
        rot = per_second(dm["t"], dm["rot"], T)
        grav = per_second(dm["t"], dm["gravity"], T)
        dv = np.r_[np.diff(lab.v), np.nan]
        resting = (lab.phone == 0).astype(float)
        f = frames.load(rid)
        raw = motion.extract(f, resting)
        user = motion.extract({"acc_mean": ua + grav, "gyro_mean": rot}, resting)  # same recipe, iOS's inputs
        print(f"## {rid}")
        ok_all = (lab.phone == 0) & (lab.on_train == 1) & lab.confident & np.r_[lab.confident[1:], False] & np.isfinite(dv) & np.isfinite(pos)
        # world-frame acceleration along the track at the labelled position
        th = np.radians(tr.heading_at(np.nan_to_num(pos), direction) - DECLINATION_DEG)
        along = a_world[:, 0] * np.cos(th) - a_world[:, 1] * np.sin(th)
        across = a_world[:, 0] * np.sin(th) + a_world[:, 1] * np.cos(th)
        res = {"raw": [], "user": [], "world": []}
        for s in range(raw["stretch"].max() + 1):
            m = ok_all & (raw["stretch"] == s) & np.isfinite(raw["a_long"])
            if m.sum() < 100:
                continue
            for name, a in (("raw", raw["a_long"]), ("user", user["a_long"]), ("world", along)):
                sd, slope = resid(a, dv, m)
                res[name].append((m.sum(), sd, abs(slope)))
        for name, rows in res.items():
            n = sum(r[0] for r in rows)
            print(f"   along-track acceleration, {name:5s}: residual sd {np.sqrt(sum(r[0] * r[1] ** 2 for r in rows) / n):.3f} m/s²"
                  f"  |slope| {', '.join(f'{r[2]:.2f}' for r in rows)}   ({n} s)")
        sd_w, slope_w = resid(along, dv, ok_all)
        print(f"   world, one fit for the whole ride (no stretches): residual sd {sd_w:.3f} m/s², slope {slope_w:+.2f}, "
              f"offset-free mean {np.mean(along[ok_all] - dv[ok_all]):+.3f} m/s²; across-track sd {np.std(across[ok_all]):.3f} m/s²")
        # odometry from world-frame acceleration, leg by leg
        stops = load.stops(rid)
        deps = [st.t_depart for st in stops]
        typical = np.nanmedian([st.t_depart - st.t_close for st in stops])
        print("   odometry from `world` alone (integral of along-track acceleration, v = 0 at both ends) / map distance:")
        for p_, q_ in zip(stops[:-1], stops[1:]):
            dep = p_.t_depart if np.isfinite(p_.t_depart) else p_.t_close + typical
            arr = q_.t_arrive if np.isfinite(q_.t_arrive) else q_.t_open - 1.9
            i0, i1 = int(round(dep)), int(round(arr))
            # heading of travel from the map needs position; use the straight line between the two stations' headings
            s0, s1 = s_of[p_.station], s_of[q_.station]
            frac = np.linspace(0, 1, i1 - i0)
            # first pass with the chord heading, then re-project with the position it implies
            pos_guess = s0 + (s1 - s0) * frac
            for _ in range(2):
                th_leg = np.radians(tr.heading_at(pos_guess, direction) - DECLINATION_DEG)
                a = a_world[i0:i1, 0] * np.cos(th_leg) - a_world[i0:i1, 1] * np.sin(th_leg)
                v = np.cumsum(np.nan_to_num(a))
                v -= v[-1] * frac
                d = np.cumsum(v)
                pos_guess = s0 + direction * d * abs(s1 - s0) / d[-1] if d[-1] > 0 else pos_guess
            ph = lab.phone[i0:i1]
            state = "resting throughout" if (ph == 0).all() else f"NOT resting for {np.mean(ph != 0):.0%} of the leg"
            est = " (wheel times estimated)" if not (np.isfinite(p_.t_depart) and np.isfinite(q_.t_arrive)) else ""
            print(f"     {p_.station:18s} > {q_.station:18s} {d[-1] / abs(s1 - s0):6.3f}   {state}{est}")
        # yaw
        upv = -grav / np.linalg.norm(grav, axis=1, keepdims=True)
        yaw_ios = np.sum(rot * upv, axis=1)
        pred = direction * lab.v * tr.curvature_at(np.nan_to_num(pos))
        m = ok_all & (lab.v > 5) & np.isfinite(raw["yaw_rate"]) & (np.abs(raw["yaw_rate"]) < 0.03)
        for name, y in (("raw gyro, median removed per stretch", raw["yaw_rate"]), ("iOS rotation rate, as is", yaw_ios)):
            r = y[m] - pred[m]
            print(f"   yaw vs speed x curvature, {name:38s}: r = {np.corrcoef(y[m], pred[m])[0, 1]:.3f}, residual sd {r.std():.4f}, mean {r.mean():+.5f} rad/s")


if __name__ == "__main__":
    main()
