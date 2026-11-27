"""Learn the model's parameters from labelled recordings.

Input: the per-frame measurements (features/) and per-frame labels (labels/frames.py) of
the training recordings. Output: one JSON-able dict (written to params/all.json, or to data/ for the leave-one-out fits):

  phone        features/phone_state.py's classifier
  tone, wheel  features/comb.py's tables, for the audio and the accelerometer line spectra
               (wheel: separately for a resting and for a held phone)
  chime        P(chime measurement category | that chime's mode / any other mode)
  level        rumble_db and vib_db as Gaussians whose mean depends on speed (per phone
               state for vib), with a weight < 1 that discounts the frame-to-frame
               correlation of their residuals (an HMM assumes there is none)
  accel        sd of along-track acceleration around the labelled dv/dt
  dynamics     P(speed change per second | speed band)
  dwell        mean time spent in each stopped mode
  cabin        P(the car's hum is heard | on the train): cabin_db (features/frames.py) at
               least HUM_DB, in the seconds labelled on the train

Every block has a prior (a few pseudo-observations of a sensible default), so a training
set lacking some situation, e.g. no resting phone, still yields a complete model.
"""

from __future__ import annotations

import numpy as np

from features import comb, motion, phone_state
from labels.frames import CLOSE_CHIME, MOVING, OPEN, OPEN_CHIME, POST, PRE, UNKNOWN, FrameLabels
from model import states

CHIME_EDGES = [0.15, 0.7]  # measurement (fraction of the second) -> 3 categories
# pseudo-count per category. Small: in ~4000 labelled seconds the detector never fired
# outside a chime, and we'd rather believe that than the 1/N a count of one would claim
CHIME_PSEUDO = 0.05
LEVEL_V_KNOTS = np.array([0.0, 1.5, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0])  # m/s
DYN_V_EDGES = np.array([3.0, 8.0, 15.0, 25.0, 32.0])
DYN_MAX_STEP = 6  # speed change per frame in units of DV: +-1.5 m/s^2
DWELL_PRIOR_S = {PRE: 2.0, OPEN_CHIME: 6.0, OPEN: 20.0, CLOSE_CHIME: 8.0, POST: 5.0}
DWELL_PRIOR_WEIGHT = 2.0
ACCEL_PRIOR = (0.15, 50.0)  # sd m/s^2, weight in frames
MIN_LEVEL_FRAMES = 15
# the car's hum is "heard" at cabin_db >= this. Over the labelled rides, on the train 88 % of
# seconds reach it, off the train 4 % (mostly right next to a car: 99th percentile 4-19 dB)
# Walking: the accelerometer's strongest line at a step rate (1.4-2.6 Hz) at least WALK_DB
# above its neighbours. It is there in about half the seconds off the train and a tenth of
# those on a stopped one; a moving train shakes the phone at these rates too.
WALK_BAND = (1.4, 2.6)
WALK_DB = 14.0
WALK_PRIOR = (0.2, 20.0)  # P(walking), weight in frames, per group


def walking(f: dict) -> np.ndarray:
    """[T] bool: a step-rate line in the accelerometer (features/frames.py accel_spec)."""
    fa = f["accel_f"]
    return f["accel_spec"][:, (fa >= WALK_BAND[0]) & (fa <= WALK_BAND[1])].max(axis=1) >= WALK_DB


HUM_DB = 6.0
HUM_PRIOR = (0.9, 20.0)  # P(hum | on), weight in frames


def _autocorr_time(res_runs: list[np.ndarray], max_lag=30) -> float:
    """Integrated autocorrelation time (frames) of residuals given as contiguous runs."""
    num, den = np.zeros(max_lag + 1), 0.0
    for r in res_runs:
        if len(r) <= max_lag + 1:
            continue
        r = r - r.mean()
        den += np.sum(r * r)
        for k in range(1, max_lag + 1):
            num[k] += np.sum(r[k:] * r[:-k])
    if den == 0:
        return 1.0
    rho = num / den
    tau = 1.0
    for k in range(1, max_lag + 1):
        if rho[k] < 0.05:
            break
        tau += 2 * rho[k]
    return float(tau)


def _runs(mask):
    edges = np.flatnonzero(np.diff(np.r_[False, mask, False].astype(int)))
    return list(zip(edges[::2], edges[1::2]))


def fit_level(xs: list[np.ndarray], labels: list[FrameLabels], masks: list[np.ndarray]) -> dict | None:
    """x ~ N(mean(v), sd): mean piecewise linear over LEVEL_V_KNOTS (knot 0 = standing)."""
    x = np.concatenate(xs)
    v = np.concatenate([l.v for l in labels])
    ok = np.concatenate([m & l.confident & np.isfinite(l.v) for m, l in zip(masks, labels)]) & np.isfinite(x)
    if ok.sum() < 10 * MIN_LEVEL_FRAMES or (ok & (v == 0)).sum() < MIN_LEVEL_FRAMES:
        return None
    knots = LEVEL_V_KNOTS
    k = np.where(v == 0, 0, 1 + np.digitize(v, (knots[1:-1] + knots[2:]) / 2))  # nearest moving knot
    means = np.full(len(knots), np.nan)
    for j in range(len(knots)):
        m = ok & (k == j)
        if m.sum() >= MIN_LEVEL_FRAMES:
            means[j] = np.median(x[m])
    good = np.isfinite(means)
    means = np.interp(knots, knots[good], means[good])
    res = x - means[k]
    sd = float(1.4826 * np.median(np.abs(res[ok] - np.median(res[ok]))))
    res_runs, at = [], 0
    for xx, m, l in zip(xs, masks, labels):
        full_ok = ok[at:at + len(xx)]
        for a, b in _runs(full_ok):
            res_runs.append(res[at + a:at + b])
        at += len(xx)
    tau = _autocorr_time(res_runs)
    return {"knots": knots.tolist(), "mean": means.round(3).tolist(), "sd": round(max(sd, 0.5), 3),
            "weight": round(1 / tau, 4), "n_frames": int(ok.sum())}


def fit_chime(xs, labels, mode) -> dict:
    cat = np.digitize(np.concatenate(xs), CHIME_EDGES)
    is_mode = np.concatenate([l.mode == mode for l in labels])
    out = {}
    for name, m in (("in", is_mode), ("out", ~is_mode)):
        n = np.bincount(cat[m], minlength=3) + CHIME_PSEUDO
        # significant figures, not decimals: the pseudo-count must not round to 0 (log 0)
        out[name] = [float(f"{x:.4g}") for x in n / n.sum()]
    return out


def fit_dynamics(labels: list[FrameLabels]) -> dict:
    """P(dv per frame | speed band), as a table over steps of DV. Learned from pairs of
    consecutive, confidently labelled frames of which at least one is moving."""
    nb = len(DYN_V_EDGES) + 1
    counts = np.zeros((nb, 2 * DYN_MAX_STEP + 1))
    for l in labels:
        ok = l.confident[:-1] & l.confident[1:] & ((l.v[:-1] > 0) | (l.v[1:] > 0))
        v0, v1 = l.v[:-1][ok], l.v[1:][ok]
        step = np.clip(np.round((v1 - v0) / states.DV), -DYN_MAX_STEP, DYN_MAX_STEP).astype(int)
        np.add.at(counts, (np.digitize(v0, DYN_V_EDGES), step + DYN_MAX_STEP), 1)
    # smooth: neighbouring steps share counts, and every step keeps a little mass
    k = np.array([0.25, 0.5, 0.25])
    counts = np.apply_along_axis(lambda r: np.convolve(r, k, mode="same"), 1, counts) + 0.5
    return {"v_edges": DYN_V_EDGES.tolist(), "max_step": DYN_MAX_STEP,
            "p_step": (counts / counts.sum(axis=1, keepdims=True)).round(5).tolist()}


def fit_dwell(labels: list[FrameLabels]) -> dict:
    """Mean seconds in each stopped mode (complete visits only)."""
    out = {}
    for mode in DWELL_PRIOR_S:
        durs = []
        for l in labels:
            for a, b in _runs(l.mode == mode):
                complete = a > 0 and b < len(l.mode) and l.mode[a - 1] != UNKNOWN and l.mode[b] != UNKNOWN
                if complete:
                    durs.append(b - a)
        w, m0 = DWELL_PRIOR_WEIGHT, DWELL_PRIOR_S[mode]
        out[str(mode)] = round((sum(durs) + w * m0) / (len(durs) + w), 2)
    return out


def fit_accel(frames, labels, p_rests) -> dict:
    """sd of a_long around sign * dv/dt + offset, sign and offset fitted per stretch.

    Only against speed labels that don't themselves come from the accelerometer (so, for
    now, tone-traced legs with a resting phone); otherwise the prior stands."""
    res = []
    for f, l, p in zip(frames, labels, p_rests):
        mo = motion.extract(f, p)
        dv = np.r_[np.diff(l.v), np.nan] / states.DT
        ok_all = (np.isfinite(mo["a_long"]) & np.isfinite(dv) & l.confident & np.r_[l.confident[1:], False]
                  & (l.source != "accel") & np.append(l.source[1:] != "accel", False))
        for s in range(mo["stretch"].max() + 1):
            ok = ok_all & (mo["stretch"] == s)
            if ok.sum() < 60:
                continue
            A = np.c_[dv[ok], np.ones(ok.sum())]
            coef, *_ = np.linalg.lstsq(A, mo["a_long"][ok], rcond=None)
            res.append(mo["a_long"][ok] - np.sign(coef[0]) * dv[ok] - coef[1])
    sd0, w = ACCEL_PRIOR
    r = np.concatenate(res) if res else np.zeros(0)
    # robust: label speeds have their own second-to-second noise
    var = (len(r) * (1.4826 * np.median(np.abs(r))) ** 2 + w * sd0**2) / (len(r) + w) if len(r) else sd0**2
    return {"sd": round(float(np.sqrt(var)), 4), "n_frames": int(len(r))}


def fit_cabin(xs: list[np.ndarray], labels: list[FrameLabels]) -> dict:
    on = np.concatenate([l.on_train == 1 for l in labels])
    hum = np.concatenate(xs) >= HUM_DB
    p0, w = HUM_PRIOR
    return {"hum_db": HUM_DB, "p_hum_on": round(float((hum[on].sum() + w * p0) / (on.sum() + w)), 4),
            "n_frames": int(on.sum())}


def fit_walk(frames: list[dict], labels: list[FrameLabels]) -> dict:
    """P(walking) off the train, on a stopped train and on a moving one."""
    walk = np.concatenate([walking(f) for f in frames])
    on = np.concatenate([l.on_train for l in labels])
    mode = np.concatenate([l.mode for l in labels])
    p0, w = WALK_PRIOR
    groups = {"off": on == 0, "stopped": (on == 1) & (mode != MOVING) & (mode != UNKNOWN), "moving": (on == 1) & (mode == MOVING)}
    return {k: round(float((walk[m].sum() + w * p0) / (m.sum() + w)), 4) for k, m in groups.items()}


def fit(frames: list[dict], labels: list[FrameLabels]) -> dict:
    p = {"phone": phone_state.fit(frames, labels)}
    phones = [phone_state.apply(p["phone"], f) for f in frames]
    p_rests = [ph[:, phone_state.RESTING] for ph in phones]
    # learn each phone-dependent block from frames that are labelled, and classified, as that state
    rest, held = ([(ph.argmax(axis=1) == k) & (l.phone == k) for ph, l in zip(phones, labels)]
                  for k in (phone_state.RESTING, phone_state.HELD))
    vs = [np.nan_to_num(l.v) for l in labels]
    known = [l.confident & np.isfinite(l.v) for l in labels]

    p["tone"] = comb.fit([f["tone_spec"] for f in frames], frames[0]["tone_f"], vs, known,
                         ratio_range=(12.0, 66.0), ratio_step=0.05)
    # wheel-rotation lines: one set of tables per phone state they can be seen in (a hand
    # damps and adds to what the phone feels, so the same line is fainter there)
    p["wheel"] = {name: comb.fit([f["accel_spec"] for f in frames], frames[0]["accel_f"], vs,
                                 [k & m for k, m in zip(known, masks)], ratio_range=(0.2, 1.38), ratio_step=0.0025)
                  for name, masks in (("resting", rest), ("held", held))}
    p["chime"] = {"open": fit_chime([f["open_chime"] for f in frames], labels, OPEN_CHIME),
                  "close": fit_chime([f["close_chime"] for f in frames], labels, CLOSE_CHIME)}
    p["level"] = {"rumble": fit_level([f["rumble_db"] for f in frames], labels, [np.ones(len(l.v), bool) for l in labels])}
    for i, band in enumerate(("vib_low", "vib_high")):
        for name, masks in (("resting", rest), ("held", held)):
            p["level"][f"{band}/{name}"] = fit_level([f["vib_db"][:, i] for f in frames], labels, masks)
    p["accel"] = fit_accel(frames, labels, p_rests)
    p["dynamics"] = fit_dynamics(labels)
    p["dwell"] = fit_dwell(labels)
    p["cabin"] = fit_cabin([f["cabin_db"] for f in frames], labels)
    p["walk"] = fit_walk(frames, labels)
    return p
