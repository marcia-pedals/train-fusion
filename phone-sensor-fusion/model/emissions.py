"""Turn a recording's measurements into the HMM's ingredients, using learned parameters:

  log_emissions   log p(frame's measurements | state), [T, N]
  transition      the [N, N] matrix P(next state | state): train dynamics, the
                  station-stop cycle, and the rider getting on and off
  accel_factor    p(along-track acceleration measured during a step | the step's speed
                  change), which multiplies the transition matrix for that step

Each measurement can be switched off (`use`), which is how the ablations are run. So can the
off-train states ("off" in `use`): without it they are unreachable and the model is the
on-train model as it was before they existed.

Off the train (before and after the ride): the train's speed and mode are hidden. Levels are
those of standing; the motor tone and the door chimes get what the on-train model makes of them
(`background`), so they say nothing about on or off; the car's hum has its own off-train table. Nothing about the on-train states changes
with them, so given that the rider is on the train, the model is the on-train model.
"""

from __future__ import annotations

import numpy as np

from features import comb, frames, phone_state
from labels.frames import CLOSE_CHIME, OPEN, OPEN_CHIME, POST, PRE
from model import states
from model.fit import CHIME_EDGES

ALL = ("chime", "tone", "wheel", "rumble", "vib", "accel", "cabin", "off")
# Getting on and off: before -> on the train -> after, once each, and only while walking
# (model/infer.py: the gate is 1 or 0 every second). On at a standing train, off through open
# doors. Per s while allowed; the exact second doesn't matter.
P_BOARD = 0.05  # before -> a standing mode (spread evenly)
P_ALIGHT = 0.05  # doors open -> after
P_BEFORE_PRIOR = 0.5  # before any measurement: not yet on the train, or on it (spread as before)
CABIN_WEIGHT = None  # override the fitted weight of the car's hum (experiments)
P_RESUME = 0.01  # per s: pulling away again without the doors having opened (signal stop)
P_REOPEN = 0.002  # per s: doors re-opening after they closed
ACCEL_OUTLIER = (0.05, 1.0)  # share and sd (m/s^2) of readings that aren't the train (a nudge)
LEVEL_OUTLIER, LEVEL_FLOOR = 0.1, 0.1  # share of such levels; their density relative to the peak
N_ST = states.N_STOPPED


def _level_ll(q: dict, x: np.ndarray) -> np.ndarray:
    """[T, N] weighted Gaussian log-likelihood of a level measurement per state."""
    knots, mean = np.asarray(q["knots"]), np.asarray(q["mean"])
    mu = np.r_[np.full(N_ST, mean[0]), np.interp(states.V_MOVING, knots[1:], mean[1:]), np.full(states.N_OFF, mean[0])]
    z2 = ((x[:, None] - mu[None, :]) / q["sd"]) ** 2
    # a Gaussian plus a flat floor: a level that fits no speed (a voice, a passing train,
    # the phone being handled) then says nothing, instead of forcing the least bad speed
    return q["weight"] * np.log((1 - LEVEL_OUTLIER) * np.exp(-0.5 * z2) + LEVEL_OUTLIER * LEVEL_FLOOR)


def _relative(ll: np.ndarray) -> np.ndarray:
    """Make a line-spectrum likelihood say only *which* speed, not *whether* moving: scale
    it so that its average over speeds is 1 (the same as standing still gets). Otherwise
    any frame full of spectral lines (speech) would read as motion at some speed."""
    m = ll.max(axis=1, keepdims=True)
    return ll - (m + np.log(np.mean(np.exp(ll - m), axis=1, keepdims=True)))


def wheel_tables(p: dict, name: str) -> tuple[dict | None, bool]:
    """The wheel-line tables for a phone state ("resting" / "held"), and whether they had
    to be borrowed from the other state because the training data had none of this one.
    Borrowed tables are miscalibrated (the line is fainter in a hand), but the lines are at
    the same frequencies, and with one ride of each kind it is the only way either gets used."""
    own = p["wheel"][name]
    if own:
        return own, False
    return p["wheel"]["held" if name == "resting" else "resting"], True


def wheel_ll(p: dict, f: dict, phone_state_now: np.ndarray) -> np.ndarray:
    """[T, N_V] log-likelihood of each speed from the accelerometer's wheel-rotation
    lines; zero while the phone is being handled."""
    out = np.zeros((len(phone_state_now), states.N_V))
    for k, name in ((phone_state.RESTING, "resting"), (phone_state.HELD, "held")):
        tables, _ = wheel_tables(p, name)
        m = phone_state_now == k
        if tables and m.any():
            out[m] = _relative(comb.apply(tables, f["accel_spec"][m], f["accel_f"], states.V_MOVING, frames.ACC_POOL_BINS))
    return out


def background(p: dict, ll: np.ndarray, walked: np.ndarray, best_path: bool = False) -> np.ndarray:
    """[T] log p(this second's sounds | the past ones) under the on-train model with nothing else
    measured: a forward pass over the on-train states alone, on `ll` [T, N_ON], their
    log-likelihoods of the motor tone and the door chimes. Given to the off-train states, it
    makes those say exactly nothing about whether the rider is on the train; the car's hum and
    walking decide that. Causal, so it runs live.

    Why: off the train you hear trains too. The tone term only says *which* speed, but a line
    held steady for minutes still piles up evidence for "a train at that speed" (0.4-2.5 nats/s
    on platforms: an arriving train's motor tone, steady platform tones, walking noise). And the
    door chimes are heard from the platform: the boarding train's opening on nearly every ride,
    the closing behind the rider who just stepped off on six of the eight that go on long
    enough. A single off-train table of chimes, averaged over every off-train second, would
    call that unlikely and keep the rider aboard.

    It also gets the model's freedom to start a ride: wherever the rider may board (`walked`
    [T]: the walking gate), a share P_BOARD of it restarts in the standing modes, as boarding
    does. Without that, the model could pick the boarding second that makes the motor tone after
    it fit a train best, a bonus the background never gets, and walking to the station reads as
    riding.

    `best_path`: the same for the Viterbi path, which compares single paths: the score of the
    best path instead of the sum over all of them."""
    A = transition(p, use=())[:states.N_ON, :states.N_ON]
    A = A / A.sum(axis=1, keepdims=True)
    a = np.r_[np.full(N_ST, 0.5 / N_ST), np.full(states.N_V, 0.5 / states.N_V)]
    out = np.zeros(len(ll))
    for t in range(len(ll)):
        m = ll[t].max()
        if t:
            prev = a
            a = (a[:, None] * A).max(axis=0) if best_path else a @ A
            if walked[t - 1]:  # the step from t-1 to t, as infer.py gates it
                a *= 1 - P_BOARD
                a[:N_ST] += P_BOARD / N_ST * (prev.max() if best_path else prev.sum())
        a = a * np.exp(ll[t] - m)
        c = a.max() if best_path else a.sum()
        out[t] = np.log(c) + m
        a /= c
    return out


def log_emissions(p: dict, f: dict, phone: np.ndarray, use=ALL, walked: np.ndarray | None = None,
                  best_path: bool = False) -> np.ndarray:
    """phone: [T, 3] from features/phone_state.py; it decides which measurements count.
    walked: [T] the walking gate (model/infer.py), for the off-train states' background.
    best_path: for the Viterbi path (see background)."""
    T = len(phone)
    lb = np.zeros((T, states.N))
    state = phone.argmax(axis=1)
    resting, held = state == phone_state.RESTING, state == phone_state.HELD
    heard = np.zeros((T, states.N_ON))  # the on-train states' tone and chimes (see background)
    if "tone" in use:
        heard[:, states.MOVING_SLICE] += _relative(
            comb.apply(p["tone"], f["tone_spec"], f["tone_f"], states.V_MOVING, frames.TONE_POOL_BINS))
    if "wheel" in use:
        lb[:, states.MOVING_SLICE] += wheel_ll(p, f, state)
    if "rumble" in use and p["level"]["rumble"]:
        lb += _level_ll(p["level"]["rumble"], f["rumble_db"])
    if "vib" in use:
        for i, band in enumerate(("vib_low", "vib_high")):
            for name, m in (("resting", resting), ("held", held)):
                q = p["level"][f"{band}/{name}"]
                if q:
                    lb[m] += _level_ll(q, f["vib_db"][:, i])[m]
    if "chime" in use:
        for name, mode in (("open", OPEN_CHIME), ("close", CLOSE_CHIME)):
            cat = np.digitize(f[f"{name}_chime"], CHIME_EDGES)
            ll_in = np.log(np.asarray(p["chime"][name]["in"]))[cat]
            ll_out = np.log(np.asarray(p["chime"][name]["out"]))[cat]
            heard += ll_out[:, None]
            heard[:, states.idx(mode)] += ll_in - ll_out
    lb[:, :states.N_ON] += heard
    if "off" in use and ("tone" in use or "chime" in use):
        gate = np.zeros(T, bool) if walked is None else walked
        lb[:, states.N_ON:] += background(p, heard, gate, best_path)[:, None]
    if "cabin" in use and "off" in use:
        q = p["cabin"]
        cat = np.digitize(f["cabin_db"], q["edges"])
        w = q["weight"] if CABIN_WEIGHT is None else CABIN_WEIGHT
        lb[:, :states.N_ON] += w * np.log(np.asarray(q["p"]["on"]))[cat][:, None]
        lb[:, states.N_ON:] += w * np.log(np.asarray(q["p"]["off"]))[cat][:, None]
    return lb


def transition(p: dict, use=ALL) -> np.ndarray:
    d = p["dynamics"]
    M = d["max_step"]
    p_step = np.asarray(d["p_step"])
    A = np.zeros((states.N, states.N))
    # moving: speed changes by a step of DV; reaching 0 = stopping
    band = np.digitize(states.V_MOVING, d["v_edges"])
    for j in range(states.N_V):  # speed (j + 1) * DV
        for s in range(-M, M + 1):
            k = j + s
            to = states.idx(PRE) if k < 0 else N_ST + min(k, states.N_V - 1)
            A[N_ST + j, to] += p_step[band[j], s + M]
    # pulling away from a stand: the positive steps of the slowest band
    launch = np.zeros(states.N)
    launch[N_ST:N_ST + M] = p_step[0, M + 1:] / p_step[0, M + 1:].sum()
    dwell = {m: 1.0 / max(v, 1.01) for m, v in ((int(k), v) for k, v in p["dwell"].items())}
    order = [PRE, OPEN_CHIME, OPEN, CLOSE_CHIME, POST]
    for a, b in zip(order[:-1], order[1:]):
        A[states.idx(a), states.idx(b)] = dwell[a]
    A[states.idx(PRE)] += P_RESUME * launch
    A[states.idx(POST)] += dwell[POST] * launch
    A[states.idx(POST), states.idx(OPEN_CHIME)] = P_REOPEN
    if "off" in use:  # once on at a standing train, once off through open doors (gated by walking in infer.py)
        A[states.BEFORE, :N_ST] = P_BOARD / N_ST
        A[states.idx(OPEN), states.AFTER] = P_ALIGHT
    for m in order:
        A[states.idx(m), states.idx(m)] = 1.0 - A[states.idx(m)].sum()
    A[states.BEFORE, states.BEFORE] = 1.0 - A[states.BEFORE].sum()
    A[states.AFTER, states.AFTER] = 1.0
    assert (A >= 0).all() and np.allclose(A.sum(axis=1), 1, atol=1e-3)
    return A / A.sum(axis=1, keepdims=True)


# speed change of every (from, to) pair of states
_DV = states.V[None, :] - states.V[:, None]


def ungated(A: np.ndarray) -> np.ndarray:
    """The transition matrix for a second without walking: no getting on or off."""
    B = A.copy()
    B[states.BEFORE, states.BEFORE] += B[states.BEFORE, :N_ST].sum()
    B[states.BEFORE, :N_ST] = 0.0
    o = states.idx(OPEN)
    B[o, o] += B[o, states.AFTER]
    B[o, states.AFTER] = 0.0
    return B


def accel_factor(a_meas: float, sign: float, offset: float, sd: float) -> np.ndarray:
    """[N, N] likelihood of the measured along-track acceleration for each transition."""
    eps, sd_out = ACCEL_OUTLIER
    r = (a_meas - offset) - sign * _DV / states.DT
    good = np.exp(-0.5 * (r / sd) ** 2) / sd
    out = np.exp(-0.5 * ((a_meas - offset) / sd_out) ** 2) / sd_out
    return (1 - eps) * good + eps * out


def prior(use=ALL) -> np.ndarray:
    """Before any measurement: before the ride or on the train (equally likely moving at any
    speed, or standing in any mode), so a recording may start anywhere."""
    p_on = 1 - P_BEFORE_PRIOR if "off" in use else 1.0
    return np.r_[np.full(N_ST, 0.5 * p_on / N_ST), np.full(states.N_V, 0.5 * p_on / states.N_V), 1 - p_on, 0.0]
