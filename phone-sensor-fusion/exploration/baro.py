"""Does the barometer follow the track's elevation, and what else does it feel?

 1. Pressure altitude against the ground elevation at the labelled position
    (maps/data/elevation.csv), per ride, allowing a constant offset (the weather) and a
    linear drift.
 2. How sharply that pins position: the misfit when the elevation profile is slid along
    the line.
 3. Doors: the average pressure change around door-open and door-close chimes.
 4. Speed: does pressure in the car depend on speed?
 5. Grade without a barometer: is the track's slope visible in the accelerometer (gravity's
    component along the track adds to the measured acceleration)?

    python -m exploration.baro
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import ndimage

from common import paths
from common.recording import Recording
from exploration.newdata import NEW, per_second, truth
from features import frames, motion
from labels import load
from maps import track as trackmod


def altitude(p_kpa):
    return 44330.0 * (1 - (p_kpa / 101.325) ** 0.1903)


def fit(alt, elev, t, m):
    """Residual of alt ~ elev + offset + drift * t over mask m."""
    A = np.c_[np.ones(m.sum()), t[m]]
    coef, *_ = np.linalg.lstsq(A, (alt - elev)[m], rcond=None)
    return alt - elev - coef[0] - coef[1] * t, coef


def main():
    tr = trackmod.load()
    fig, axes = plt.subplots(len(NEW), 1, figsize=(24, 5.5 * len(NEW)))
    for ax, rid in zip(axes, NEW):
        rec = Recording(rid)
        lab, pos, direction = truth(rid)
        T = len(lab.v)
        tb, p, rel = rec.baro
        print(f"## {rid}: {len(tb)} samples, {1 / np.median(np.diff(tb)):.2f} Hz, pressure {p.min():.3f}..{p.max():.3f} kPa")
        alt = per_second(tb, altitude(p), T)
        alt = np.interp(np.arange(T), np.flatnonzero(np.isfinite(alt)), alt[np.isfinite(alt)])
        t = np.arange(T) + 0.5
        on = (lab.on_train == 1) & np.isfinite(pos)
        elev = tr.elevation_at(np.nan_to_num(pos))
        res, coef = fit(alt, elev, t, on)
        r = np.corrcoef(alt[on] - coef[1] * t[on], elev[on])[0, 1]
        print(f"   on the train ({on.sum()} s): track elevation spans {elev[on].min():.1f}..{elev[on].max():.1f} m; "
              f"pressure altitude follows it with r = {r:.3f}, residual sd {res[on].std():.2f} m "
              f"(robust {1.4826 * np.median(np.abs(res[on] - np.median(res[on]))):.2f} m); offset {coef[0]:+.1f} m, drift {coef[1] * 3600:+.1f} m/h")
        slope = np.polyfit(elev[on], (alt - coef[1] * t)[on], 1)[0]
        print(f"   slope of pressure altitude against map elevation: {slope:.2f} (1 = perfect)")
        # slide the map under the ride
        print("   misfit (sd, m) if the ride were this far along the line from where it was:")
        row = []
        for shift in (-4000, -2000, -1000, -500, -200, 0, 200, 500, 1000, 2000, 4000):
            e2 = tr.elevation_at(np.nan_to_num(pos) + shift)
            r2, _ = fit(alt, e2, t, on)
            row.append(f"{shift / 1000:+.1f} km: {r2[on].std():.2f}")
        print("     " + "   ".join(row))
        # per leg: could elevation alone tell which leg this is?
        stops = load.stops(rid)
        # doors
        raw_t, raw_a = tb, altitude(p)
        for name, key in (("door-open chime", "t_open"), ("door-close chime", "t_close")):
            stack = []
            for st in stops[1:-1]:
                t0 = getattr(st, key)
                if not np.isfinite(t0):
                    continue
                g = np.arange(-12, 20.1, 1.0)
                a = np.interp(t0 + g, raw_t, raw_a)
                stack.append(a - a[(g >= -10) & (g <= -3)].mean())
            s = np.array(stack)
            g = np.arange(-12, 20.1, 1.0)
            at = lambda x: s[:, np.argmin(np.abs(g - x))]
            print(f"   {name}: altitude change vs 3-10 s before, mean ± sd over {len(s)} stops:  "
                  + "  ".join(f"{x:+.0f} s: {at(x).mean():+.2f}±{at(x).std():.2f} m" for x in (0, 2, 4, 8, 15)))
        # how distinctive is the door-open pressure change? 4 s altitude change, by situation
        d4 = np.r_[alt[4:] - alt[:-4], np.zeros(4)]
        at_open = np.zeros(T, bool)
        for st in stops[1:]:
            at_open[int(st.t_open):int(st.t_open) + 3] = True
        standing = on & (lab.v == 0) & ~at_open
        q = lambda m: np.percentile(np.abs(d4[m]), [50, 90, 99]).round(2)
        print(f"   |altitude change over 4 s|, median / 90th / 99th percentile: at door-open {q(at_open)} m; "
              f"otherwise standing {q(standing)} m; moving {q(on & (lab.v > 1))} m")
        # track grade seen by the accelerometer: resting, a_long - dv/dt should be -g x grade
        f = frames.load(rid)
        mo = motion.extract(f, (lab.phone == 0).astype(float))
        dv = np.r_[np.diff(lab.v), np.nan]
        e_s = tr.elevation_at(tr.s)
        e_s = ndimage.median_filter(e_s, 31)  # 300 m: the DEM dips into every underpass
        grade = np.interp(np.nan_to_num(pos), tr.s, np.gradient(ndimage.gaussian_filter1d(e_s, 15), tr.s)) * direction
        rows = []
        for k in range(mo["stretch"].max() + 1):
            m = on & (mo["stretch"] == k) & np.isfinite(dv) & lab.confident & (lab.v > 2)
            if m.sum() < 200:
                continue
            sign = np.sign(np.polyfit(dv[m], mo["a_long"][m], 1)[0])
            extra = sign * mo["a_long"][m] - dv[m]
            rows.append((m.sum(), np.corrcoef(extra, -9.81 * grade[m])[0, 1], np.polyfit(-9.81 * grade[m], extra, 1)[0]))
        print("   accelerometer minus speed change against -g x map grade, per resting stretch (n, r, slope): "
              + "; ".join(f"{n}, {r:+.2f}, {sl:+.2f}" for n, r, sl in rows)
              + f"   (grades here: 5-95 % {np.percentile(grade[on], 5) * 100:+.2f}..{np.percentile(grade[on], 95) * 100:+.2f} %)")
        mv = on & (lab.v > 1)
        print(f"   residual against speed: r = {np.corrcoef(res[mv], lab.v[mv])[0, 1]:+.2f}; "
              f"mean residual standing {res[on & (lab.v < 0.5)].mean():+.2f} m, above 30 m/s {res[on & (lab.v > 30)].mean():+.2f} m")
        ax.plot(t, np.where(on, elev, np.nan), "k", lw=2, label="map: ground elevation at the labelled position")
        ax.plot(t, alt - coef[0] - coef[1] * t, "C0", lw=0.9, label="barometer: pressure altitude (offset and drift removed)")
        for st in stops:
            ax.axvline(st.t_open, color="g", lw=0.5)
            ax.text(st.t_open, ax.get_ylim()[1], st.station, fontsize=8, rotation=20, va="bottom")
        ax.set_ylabel("m"); ax.grid(alpha=0.3); ax.legend(loc="lower right"); ax.set_title(rid, loc="left")
        ax.set_xlim(0, T)
    fig.tight_layout()
    out = paths.derived("exploration", "baro.png"); fig.savefig(out, dpi=60); print(out)


if __name__ == "__main__":
    main()
