"""Speed labels, 1 per second, from the best source available at each moment:

  dwell  the train is at a platform: v = 0 from wheel-stop (or, where that isn't labelled,
         from the door-open chime) to wheel-start (or the end of the door-close chime)
  accel  phone lying still for the whole leg: integrate the along-track acceleration from
         wheel-start to wheel-stop, zero level interpolated between the two dwells
  tone   otherwise: the traced motor tone (labels/trace_tone.py)

`accel` and `tone` legs are each scaled by one constant so that the leg's integral equals
the map distance between its two stations. `confident` is 0 where the label is a guess:
on tone legs wherever the ridge isn't actually visible (below 250 Hz, or under speech).

Writes <labels>/<id>/speed.csv (t, v_mps, source, confident, leg) and prints how well
the two leg sources agree where both exist.

    python -m labels.build_speed
"""

import csv

import numpy as np

from common import paths
from common.recording import load_all
from labels import load
from labels.stop_times_accel import long_accel
from labels.trace_tone import F_BLIND

G = 9.80665
MARGIN_S = 8.0  # the phone must be still from this long before a leg to this long after
TONE_SEEN_DB = 3.0
TONE_K, TONE_K_TOL = 26.4, 0.04  # Hz per m/s on every leg where the tone is clear; tolerance
MIN_SEEN = 0.20  # share of a leg's seconds in which the traced ridge is actually visible


def accel_leg(rec, still, dep, arr):
    """(t, v) at 100 Hz from the accelerometer over one leg; v(dep) = v(arr) = 0."""
    t, a, _, _ = long_accel(rec, still[0], still[1], fc=2.0)
    pre = np.median(a[(t > dep - MARGIN_S) & (t < dep - 1)])
    post = np.median(a[(t > arr + 1) & (t < arr + MARGIN_S)])
    m = (t >= dep) & (t <= arr)
    tt = t[m]
    frac = (tt - dep) / (arr - dep)
    v = np.cumsum((a[m] - (pre + (post - pre) * frac)) * G) * np.median(np.diff(tt))
    if v[len(v) // 2] < 0:  # the principal axis has no preferred sign
        v = -v
    return tt, v - v[-1] * frac


def main():
    s_of = load.station_s()
    for rec in load_all():
        stops = load.stops(rec.id)
        resting = [(a, b) for a, b, st in load.phone(rec.id) if st == "resting"]
        trace = load.table(rec.id, "tone_trace.csv")
        sec = np.arange(0.5, rec.duration, 1.0)
        v = np.full(len(sec), np.nan)
        src = np.full(len(sec), "", dtype=object)
        conf = np.zeros(len(sec), dtype=int)
        legname = np.full(len(sec), "", dtype=object)

        for st in stops:
            a = st.t_arrive if np.isfinite(st.t_arrive) else st.t_open
            b = st.t_depart if np.isfinite(st.t_depart) else st.t_close_end
            if np.isnan(b):
                b = rec.duration  # last stop: the recording ends at the platform
            m = (sec - 0.5 >= a) & (sec + 0.5 <= b)
            v[m], src[m], conf[m] = 0.0, "dwell", 1

        print(f"## {rec.id}")
        for p, q in zip(stops[:-1], stops[1:]):
            leg = f"{p.station} > {q.station}"
            dist = abs(s_of[p.station] - s_of[q.station])
            m = (sec > p.t_close_end) & (sec < q.t_open) & (src != "dwell")
            legname[m] = leg
            k = trace["leg"] == leg
            tt, ff, pp = trace["t"][k], trace["f_hz"][k], trace["prominence_db"][k]
            k_leg = np.sum(ff) * 0.25 / dist
            v_tone = np.interp(sec[m], tt, ff / k_leg, left=0, right=0)
            seen = np.interp(sec[m], tt, ((ff > F_BLIND) & (pp > TONE_SEEN_DB)).astype(float)) > 0.5
            still = next(((a, b) for a, b in resting
                          if a <= p.t_depart - MARGIN_S and q.t_arrive + MARGIN_S <= b), None)
            msg = f"{leg:36s} {dist:5.0f} m  tone k={k_leg:5.2f} seen {seen.mean():.0%}"
            if still:
                ta, va = accel_leg(rec, still, p.t_depart, q.t_arrive)
                scale = dist / (np.sum(va) * np.median(np.diff(ta)))
                v_acc = np.interp(sec[m], ta, va * scale, left=0, right=0)
                v[m], src[m], conf[m] = v_acc.clip(0), "accel", 1
                d = (v_tone - v_acc)[seen]
                msg += f" | accel scale {scale:.3f}; tone-accel where seen: mean {d.mean():+.2f} rms {d.std():.2f} m/s"
            else:
                # a trace that didn't find the tone gives itself away: the tone constant it
                # implies is off, or the ridge was hardly ever seen. Such a leg stays
                # unlabelled (confident = 0 throughout) rather than carry a made-up speed.
                ok = abs(k_leg / TONE_K - 1) <= TONE_K_TOL and seen.mean() >= MIN_SEEN
                v[m], src[m], conf[m] = v_tone, "tone", seen.astype(int) * ok
                if not ok:
                    msg += " | REJECTED: no usable speed label for this leg"
            print(msg)

        with (paths.LABELS / rec.id / "speed.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["t", "v_mps", "source", "confident", "leg"])
            for i in np.flatnonzero(src != ""):
                w.writerow([f"{sec[i]:.1f}", f"{v[i]:.2f}", src[i], conf[i], legname[i]])


if __name__ == "__main__":
    main()
