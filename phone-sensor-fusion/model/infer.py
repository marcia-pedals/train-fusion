"""Run the model on one recording's measurements.

    result = infer(params, frames)

Two passes. The along-track acceleration (features/motion.py) comes with an unknown sign
and offset per stretch of resting phone, so:
  1. infer without it;
  2. per stretch, regress the measured acceleration on the inferred change of speed: the
     slope's sign is the phone's direction, the intercept the offset (one EM step for two
     nuisance parameters);
  3. infer again with the acceleration included (and repeat 2-3 once to refine the offset).
  4. if the recording has iOS's attitude: a stretch too short or too quiet for step 2 to
     tell the sign gets it from its compass heading agreeing with the other stretches.

Everything in a pass is exact HMM inference (model/hmm.py). `v_filt` uses only the past
(what an online filter would report at that moment, given the stretch parameters);
`v` and `mode_p` are smoothed over the whole recording; `path` is the Viterbi sequence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from features import motion, phone_state
from scipy.ndimage import maximum_filter1d

from model import emissions, hmm, states

# Getting on or off the train takes walking: a step-band level (features/frames.py steps_db)
# above WALK_DB within the last WALK_WINDOW_S seconds. -13 dB or more at every boarding and
# alighting so far, and not one second above it mid-ride on any of ten rides (hand-held too).
# Without it, no getting on or off: a soft floor lets ten minutes of a silent car "get off".
WALK_DB = -15.0
WALK_WINDOW_S = 10
MIN_SLOPE = 0.3


def walking_gate(f: dict) -> np.ndarray:
    """[T] bool: may the rider get on or off in the step from t to t+1 (walked within the last
    WALK_WINDOW_S seconds, counting second t+1)."""
    walk = np.r_[f["steps_db"][1:], -np.inf] > WALK_DB
    return maximum_filter1d(walk.astype(float), WALK_WINDOW_S, origin=(WALK_WINDOW_S - 1) // 2) > 0  # a stretch whose acceleration doesn't follow the speed changes is left out
N_REFINE = 2


@dataclass
class Result:
    v: np.ndarray  # [T] posterior mean speed (smoothed), m/s, given that the rider is on the train
    v_sd: np.ndarray  # [T] posterior sd of speed, the same
    v_filt: np.ndarray  # [T] filtered (causal) mean speed, the same
    mode_p: np.ndarray  # [T, 7] smoothed mode probabilities (labels.frames order, then off the train)
    path_mode: np.ndarray  # [T] Viterbi mode
    path_v: np.ndarray  # [T] Viterbi speed
    phone: np.ndarray  # [T, 3] P(phone resting / held / handled), filtered
    loglik: float
    gamma: np.ndarray = None  # [T, N] smoothed state posteriors (states.py order)
    alpha: np.ndarray = None  # [T, N] filtered state posteriors
    stretches: list = field(default_factory=list)  # per resting stretch: sign, offset, slope
    p_on: np.ndarray = None  # [T] smoothed P(the rider is on the train)
    p_on_filt: np.ndarray = None  # [T] the same, filtered (what a live filter says)
    p_on_lag: dict = field(default_factory=dict)  # lag (s) -> [T] the same, given lag s more
    travel_heading: np.ndarray = None  # [T] compass heading of travel, degrees true; nan where unknown
    # (needs iOS's attitude in the recording, a resting phone, and that stretch's sign)

    @property
    def distance(self) -> np.ndarray:
        """[T+1] metres travelled by the start of each frame (smoothed)."""
        return np.r_[0, np.cumsum(self.v * states.DT)]

    @property
    def distance_filt(self) -> np.ndarray:
        return np.r_[0, np.cumsum(self.v_filt * states.DT)]


def _circ_mean(deg):
    r = np.radians(deg[np.isfinite(deg)])
    return np.degrees(np.arctan2(np.sin(r).mean(), np.cos(r).mean())) % 360


def _signs_from_heading(mo: dict, sign: np.ndarray, info: list) -> bool:
    """A train doesn't turn round mid-ride: where the regression could not tell which way
    the phone points in a stretch, take the sign that makes its travel heading agree
    (within 90 degrees) with the stretches where it could. Returns whether anything changed."""
    known = [s for s in range(len(sign)) if sign[s]]
    if not known:
        return False
    ref = _circ_mean(np.concatenate([(mo["axis_heading"][mo["stretch"] == s] + (180 if sign[s] > 0 else 0)) for s in known]))
    changed = False
    for s in range(len(sign)):
        if sign[s] == 0:
            h = _circ_mean(mo["axis_heading"][mo["stretch"] == s])
            off = abs((h - ref + 180) % 360 - 180)
            sign[s] = -1.0 if off < 90 else 1.0
            info[s]["sign"], info[s]["sign_from"] = float(sign[s]), "heading"
            changed = True
    return changed


def infer(p: dict, f: dict, use=emissions.ALL, lags=()) -> Result:
    phone = phone_state.apply(p["phone"], f)
    p_rest = phone[:, phone_state.RESTING]
    mo = motion.extract(f, p_rest)
    walked = walking_gate(f)
    log_b = emissions.log_emissions(p, f, phone, use, walked)
    A = emissions.transition(p, use)
    prior = emissions.prior(use)
    T = len(p_rest)
    n_stretch = mo["stretch"].max() + 1
    sign, offset = np.zeros(n_stretch), np.zeros(n_stretch)
    info = []

    gate = "off" in use
    if gate:
        A_still = emissions.ungated(A)

    def trans(t):
        At = (A if walked[t] else A_still) if gate else A
        s = mo["stretch"][t]
        if s < 0 or sign[s] == 0 or not np.isfinite(mo["a_long"][t]) or mo["stretch"][min(t + 1, T - 1)] != s:
            return At
        return At * emissions.accel_factor(mo["a_long"][t], sign[s], offset[s], p["accel"]["sd"])

    gamma, alpha, loglik = hmm.forward_backward(log_b, trans, prior)
    if "accel" in use:
        for _ in range(N_REFINE):
            dv = np.r_[np.diff(gamma @ states.V), 0.0] / states.DT  # off the train: 0
            info = []
            for s in range(n_stretch):
                m = (mo["stretch"] == s) & np.isfinite(mo["a_long"])
                slope, off = np.polyfit(dv[m], mo["a_long"][m], 1)
                sign[s] = np.sign(slope) if abs(slope) >= MIN_SLOPE else 0.0
                offset[s] = off
                info.append({"t0": int(np.flatnonzero(m)[0]), "t1": int(np.flatnonzero(m)[-1]) + 1,
                             "sign": float(sign[s]), "offset": float(off), "slope": float(slope)})
            gamma, alpha, loglik = hmm.forward_backward(log_b, trans, prior)
        if "axis_heading" in mo and _signs_from_heading(mo, sign, info):
            gamma, alpha, loglik = hmm.forward_backward(log_b, trans, prior)
    travel = np.full(T, np.nan)
    if "axis_heading" in mo:
        for s in range(n_stretch):
            if sign[s]:
                m = mo["stretch"] == s
                travel[m] = (mo["axis_heading"][m] + (180 if sign[s] > 0 else 0)) % 360
    path = hmm.viterbi(emissions.log_emissions(p, f, phone, use, walked, best_path=True) if "off" in use else log_b, trans, prior)
    lagged = {L: states.on_train(hmm.fixed_lag(log_b, trans, alpha, L)) for L in lags}
    # the train's speed given that the rider is on it (off it, the train's speed isn't known)
    def on_mean(g, x):
        w = g[:, :states.N_ON]
        return (w @ x[:states.N_ON]) / np.maximum(w.sum(axis=1), 1e-300)
    v = on_mean(gamma, states.V)
    return Result(v=v, v_sd=np.sqrt(np.maximum(on_mean(gamma, states.V**2) - v**2, 0)), v_filt=on_mean(alpha, states.V),
                  mode_p=states.mode_probs(gamma), path_mode=states.MODE[path], path_v=states.V[path],
                  phone=phone, loglik=loglik, gamma=gamma, alpha=alpha, stretches=info, travel_heading=travel,
                  p_on=states.on_train(gamma), p_on_filt=states.on_train(alpha), p_on_lag=lagged)
