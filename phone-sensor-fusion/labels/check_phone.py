"""Check phone.csv against what the phone's own sensors say about its state.

A phone lying on a surface is flat and quiet. On every resting stretch labelled so far it
was tilted 1-6 degrees from flat, with gyro jitter (frames.gyro_rms, log10 rad/s) around
-2.1. A phone in a hand is tilted and wanders (median ~17-36 degrees), and its jitter sits
around -1.4 to -0.7. A labeller looking only for the moments the phone is moved (jumps in
the low-passed accelerometer) can't tell a steady hand from a seat; these two numbers can.

Flags each labelled interval of at least MIN_S seconds that disagrees:
  resting  but with jitter above RESTING_MAX_GYRO
  held     but flat and with jitter below HELD_MIN_GYRO (looks like it was put down)
Tilt alone doesn't flag a resting phone: one propped upright (79 degrees, steady to a
tenth of a degree, and quiet) was resting. It is printed to help decide. A flag is a
reason to look again, not a verdict.

    python -m labels.check_phone                 # every labelled recording
    python -m labels.check_phone <recording-id>  # one or more

Exits 1 if anything is flagged.
"""

import sys

import numpy as np

from common import paths
from features import frames
from labels import load

FLAT_TILT = 10.0  # degrees from flat
RESTING_MAX_GYRO = -1.8
HELD_MIN_GYRO = -1.9
MIN_S = 10


def tilt_deg(acc_mean: np.ndarray) -> np.ndarray:
    """Angle between the phone's screen normal (z) and gravity, per second."""
    g = acc_mean / np.linalg.norm(acc_mean, axis=1, keepdims=True)
    return np.degrees(np.arccos(np.clip(np.abs(g[:, 2]), 0, 1)))


def check(rid: str) -> list[str]:
    f = frames.load(rid)
    tilt, gyro = tilt_deg(f["acc_mean"]), f["gyro_rms"]
    flags = []
    for t0, t1, state in load.phone(rid):
        a, b = int(np.ceil(t0)), min(int(t1), len(tilt))
        if state not in ("resting", "held") or b - a < MIN_S:
            continue
        ti, gy = float(np.nanmedian(tilt[a:b])), float(np.nanmedian(gyro[a:b]))
        if state == "resting" and gy > RESTING_MAX_GYRO:
            why = "jittery" + (" and tilted" if ti > FLAT_TILT else "")
        elif state == "held" and ti <= FLAT_TILT and gy < HELD_MIN_GYRO:
            why = "flat and still"
        else:
            continue
        flags.append(f"{a:6d}-{b:<6d} {state:8s} tilt {ti:5.1f} deg, gyro {gy:6.2f}: {why}")
    return flags


def main():
    ids = sys.argv[1:] or paths.labelled_ids()
    names = load.ride_names()
    bad = 0
    for rid in ids:
        flags = check(rid)
        print(f"{names.get(rid, rid)}: {'ok' if not flags else f'{len(flags)} interval(s) to look at again'}")
        for line in flags:
            print("   ", line)
        bad += bool(flags)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
