"""Stage 3: what the IMU says about the train's motion, once we know the phone is resting.

Per stretch of resting phone (p_rest > 0.5 for at least MIN_STRETCH_S):

  up        the vertical = minus the stretch's mean accelerometer reading
  a_long    horizontal acceleration along its principal axis over the stretch, m/s^2.
            A train accelerates along the track far more than across it, so this is the
            along-track acceleration, up to (a) an unknown sign (which way the phone
            points) and (b) a small offset (track grade, and the stretch's true mean
            acceleration). The model estimates both per stretch (model/infer.py).
  yaw_rate  rotation rate about `up`, rad/s, minus its median over the stretch (gyro
            bias; most track is straight). Positive = turning left. Equals speed x track
            curvature, whichever way the phone lies.
  stretch   index of the stretch each frame belongs to (-1 = none)
  axis_heading  (only if the recording has iOS's attitude, `att_q`) compass heading, degrees
            true, of the + direction of that principal axis. The train travels along it one
            way or the other; which way goes with the sign of a_long: if a_long rises when
            the train speeds up, travel is along the - direction (iOS reads acceleration
            with the opposite sign), so travel heading = axis_heading + 180.

Outside such stretches a_long and yaw_rate are nan: a phone in a hand tells us nothing
reliable about a 0.05 g acceleration.

Not causal yet: `up` and the principal axis use the whole stretch. Online, both can be
running estimates that settle after the first station-to-station cycle; with a
magnetometer (heading) this gets much easier, so it isn't attempted now.
"""

import numpy as np

from common.dsp import quat_rotate
from maps.track import DECLINATION_DEG

G = 9.80665
MIN_STRETCH_S = 30
EDGE_S = 2  # drop the first/last seconds of a stretch (being put down / picked up)


def stretches(p_rest: np.ndarray) -> list[tuple[int, int]]:
    on = np.r_[False, p_rest > 0.5, False]
    edges = np.flatnonzero(on[1:] != on[:-1])
    out = [(a + EDGE_S, b - EDGE_S) for a, b in zip(edges[::2], edges[1::2])]
    return [(a, b) for a, b in out if b - a >= MIN_STRETCH_S]


def extract(f: dict, p_rest: np.ndarray) -> dict:
    T = len(p_rest)
    a_long = np.full(T, np.nan)
    yaw = np.full(T, np.nan)
    stretch = np.full(T, -1)
    heading = np.full(T, np.nan)
    for i, (a, b) in enumerate(stretches(p_rest)):
        acc, gyr = f["acc_mean"][a:b].astype(float), f["gyro_mean"][a:b].astype(float)
        g = acc.mean(axis=0)
        up = -g / np.linalg.norm(g)  # iOS reports -1 g on the axis pointing up
        horiz = (acc - g) - np.outer((acc - g) @ up, up)
        w, vec = np.linalg.eigh(np.cov(horiz.T))
        a_long[a:b] = horiz @ vec[:, -1] * G
        y = gyr @ up
        yaw[a:b] = y - np.median(y)
        stretch[a:b] = i
        if "att_q" in f:
            world = quat_rotate(f["att_q"][a:b].astype(float), vec[:, -1])  # x magnetic north, y west
            heading[a:b] = (np.degrees(np.arctan2(-world[:, 1], world[:, 0])) + DECLINATION_DEG) % 360
    # rotation about `up` is counter-clockwise-positive seen from above: positive = left turn
    out = {"a_long": a_long, "yaw_rate": yaw, "stretch": stretch}
    if "att_q" in f:
        out["axis_heading"] = heading
    return out
