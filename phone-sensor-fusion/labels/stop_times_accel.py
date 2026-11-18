"""Wheel-stop and wheel-start times at each station from the accelerometer, for recordings
where the phone lies still. Proposals for <labels>/<id>/stops.csv,
checked against labels/stop_sheets.py.

The horizontal acceleration is projected on its principal axis over the still stretch
(the train's long axis: trains accelerate along the track far more than across it). While
the doors are open the train is at rest, which gives the zero level for that stop.
  arrive = last moment before the door-open chime when |a - rest| > THRESH
           (braking holds until the wheels stop, then the brake jerk releases to zero)
  depart = first moment after the door-close chime when |a - rest| > THRESH for 1 s

    python -m labels.stop_times_accel <recording-id> <still_t0> <still_t1> "<opens>" "<closes>"
"""

import sys

import numpy as np
from scipy import signal

from common.recording import Recording

THRESH_G = 0.008


def long_accel(rec, t0, t1, fc=1.0):
    """(t, low-passed horizontal acceleration along its principal axis, in g) over t0..t1."""
    t, a = rec.accel
    m = (t >= t0) & (t <= t1)
    t, a = t[m], a[m]
    g = np.median(a, axis=0)
    up = g / np.linalg.norm(g)
    al = signal.sosfiltfilt(signal.butter(2, fc, fs=100, output="sos"), a - g, axis=0)
    horiz = al - np.outer(al @ up, up)
    w, v = np.linalg.eigh(np.cov(horiz.T))
    return t, horiz @ v[:, -1], v[:, -1], up


def main(rid, t0, t1, opens, closes):
    rec = Recording(rid)
    t, a, axis, up = long_accel(rec, t0, t1)
    print("up", up.round(3), "long axis", axis.round(3))
    for o in opens:
        c = min([c for c in closes if c > o], default=None)
        rest = np.median(a[(t > o) & (t < (c or o + 10))])
        m = (t < o) & (t > o - 15) & (np.abs(a - rest) > THRESH_G)
        arrive = t[m][-1] if m.any() else np.nan
        dep = np.nan
        if c:
            over = (np.abs(a - rest) > THRESH_G) & (t > c)
            run = np.convolve(over, np.ones(100), "valid") >= 100
            i = np.argmax(run)
            dep = t[i] if run.any() else np.nan
        print(f"open {o:8.2f}  arrive {arrive:8.2f} (open-{o-arrive:.2f})  close {c or np.nan:8.2f}"
              f"  depart {dep:8.2f} (close+{dep-(c or np.nan):.2f})  rest {rest:+.4f} g")


if __name__ == "__main__":
    a = sys.argv
    main(a[1], float(a[2]), float(a[3]), [float(v) for v in a[4].split()], [float(v) for v in a[5].split()])
