"""Shared pieces for the explorations of the newer sensors (magnetometer,
barometer, iOS device motion): per-second resampling, rotations, and the train's forward
axis in the phone's frame for each stretch of resting phone."""

import numpy as np

from common.recording import Recording
from labels import load
from labels.frames import frame_labels
from labels.position import position



def _with_new_sensors() -> tuple[str, ...]:
    """The labelled recordings that have a magnetometer (and with it the barometer and
    iOS's device motion)."""
    from common import paths
    ids = sorted(p.name for p in paths.LABELS.iterdir() if p.is_dir())
    return tuple(i for i in ids if Recording(i).has("mag"))


NEW = _with_new_sensors()
DECLINATION_DEG = 12.9  # magnetic north is this far east of true north on the Peninsula (2026)


def per_second(t, x, T, how=np.mean):
    """Samples at times t -> one value per second [T] (nan where a second has none)."""
    idx = np.floor(t).astype(int)
    ok = (idx >= 0) & (idx < T)
    x = np.asarray(x, float)
    out = np.full((T,) + x.shape[1:], np.nan)
    order = np.argsort(idx[ok], kind="stable")
    ii, xx = idx[ok][order], x[ok][order]
    starts = np.flatnonzero(np.r_[True, ii[1:] != ii[:-1]])
    for a, b in zip(starts, np.r_[starts[1:], len(ii)]):
        out[ii[a]] = how(xx[a:b], axis=0)
    return out


def quat_rotate(q, v, inverse=False):
    """Rotate vectors v [n,3] by quaternions q [n,4] (x, y, z, w)."""
    u = q[:, :3] * (-1 if inverse else 1)
    w = q[:, 3:4]
    t = 2 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def forward_axes(rid):
    """Per resting stretch on the train: (t0, t1, unit vector in phone axes pointing the
    way the train travels, up vector). From the accelerometer alone: the principal axis of
    horizontal acceleration, signed so that it agrees with the labelled speed changes."""
    rec, lab = Recording(rid), frame_labels(rid)
    T = len(lab.v)
    ta, a = rec.accel
    acc = per_second(ta, a, T)
    dv = np.r_[np.diff(lab.v), np.nan]
    out = []
    for t0, t1, state in load.phone(rid):
        t0, t1 = int(t0) + 3, int(t1) - 3
        if state != "resting" or t1 - t0 < 60 or lab.on_train[(t0 + t1) // 2] != 1:
            continue
        seg = acc[t0:t1]
        g = np.nanmean(seg, axis=0)
        up = -g / np.linalg.norm(g)
        h = (seg - g) - np.outer((seg - g) @ up, up)
        w, vec = np.linalg.eigh(np.cov(h[np.isfinite(h[:, 0])].T))
        axis = vec[:, -1]
        d = dv[t0:t1]
        ok = np.isfinite(d) & lab.confident[t0:t1] & np.isfinite(h[:, 0])
        if ok.sum() < 30:
            continue
        if np.sum((h[ok] @ axis) * d[ok]) < 0:
            axis = -axis
        # iOS reports -1 g on the axis pointing up and, consistently, acceleration with the
        # opposite sign: speeding up along +axis reads as negative along it
        out.append((t0, t1, -axis, up))
    return out


def circ_diff(a, b):
    """a - b in degrees, wrapped to -180..180."""
    return (np.asarray(a) - np.asarray(b) + 180) % 360 - 180


def truth(rid):
    lab = frame_labels(rid)
    pos, direction = position(rid)
    return lab, pos, direction
