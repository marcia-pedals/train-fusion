"""Turn a recording's measurements into the HMM's ingredients, using learned parameters:

  log_emissions   log p(frame's measurements | state), [T, N]
  transition      the [N, N] matrix P(next state | state): train dynamics and the
                  station-stop cycle
  accel_factor    p(along-track acceleration measured during a step | the step's speed
                  change), which multiplies the transition matrix for that step

Each measurement can be switched off (`use`), which is how the ablations are run.
"""

from __future__ import annotations

import numpy as np

from features import comb, frames, phone_state
from labels.frames import CLOSE_CHIME, OPEN, OPEN_CHIME, POST, PRE
from model import states
from model.fit import CHIME_EDGES

ALL = ("chime", "tone", "wheel", "rumble", "vib", "accel")
P_RESUME = 0.01  # per s: pulling away again without the doors having opened (signal stop)
P_REOPEN = 0.002  # per s: doors re-opening after they closed
ACCEL_OUTLIER = (0.05, 1.0)  # share and sd (m/s^2) of readings that aren't the train (a nudge)
LEVEL_OUTLIER, LEVEL_FLOOR = 0.1, 0.1  # share of such levels; their density relative to the peak
# per s: from any state straight into the door-open chime. The model has no idea of "not
# on the train", and what it hears on a platform can look like a moving train (steady
# platform tones sit where the motor tone is at 36 m/s), from which no braking curve
# reaches the stop in time. A door-open chime is unmistakable, so it is allowed to
# override whatever was believed before, at this price. Consequence: before the first
# chime of a recording that starts on a platform, the model's output is meaningless.
P_REANCHOR = 1e-6
N_ST = states.N_STOPPED


def _level_ll(q: dict, x: np.ndarray) -> np.ndarray:
    """[T, N] weighted Gaussian log-likelihood of a level measurement per state."""
    knots, mean = np.asarray(q["knots"]), np.asarray(q["mean"])
    mu = np.r_[np.full(N_ST, mean[0]), np.interp(states.V_MOVING, knots[1:], mean[1:])]
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


def tone_ll(p: dict, f: dict) -> np.ndarray:
    """[T, N_V] log-likelihood of each speed from the audio's motor-tone lines."""
    return _relative(comb.apply(p["tone"], f["tone_spec"], f["tone_f"], states.V_MOVING, frames.TONE_POOL_BINS))


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


def log_emissions(p: dict, f: dict, phone: np.ndarray, use=ALL) -> np.ndarray:
    """phone: [T, 3] from features/phone_state.py; it decides which measurements count."""
    T = len(phone)
    lb = np.zeros((T, states.N))
    state = phone.argmax(axis=1)
    resting, held = state == phone_state.RESTING, state == phone_state.HELD
    if "tone" in use:
        lb[:, N_ST:] += tone_ll(p, f)
    if "wheel" in use:
        lb[:, N_ST:] += wheel_ll(p, f, state)
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
            lb += ll_out[:, None]
            lb[:, states.idx(mode)] += ll_in - ll_out
    return lb


def transition(p: dict) -> np.ndarray:
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
    for m in order:
        A[states.idx(m), states.idx(m)] = 1.0 - A[states.idx(m)].sum()
    A *= 1 - P_REANCHOR
    A[:, states.idx(OPEN_CHIME)] += P_REANCHOR
    assert (A >= 0).all() and np.allclose(A.sum(axis=1), 1, atol=1e-3)
    return A / A.sum(axis=1, keepdims=True)


# speed change of every (from, to) pair of states
_DV = states.V[None, :] - states.V[:, None]


def accel_factor(a_meas: float, sign: float, offset: float, sd: float) -> np.ndarray:
    """[N, N] likelihood of the measured along-track acceleration for each transition."""
    eps, sd_out = ACCEL_OUTLIER
    r = (a_meas - offset) - sign * _DV / states.DT
    good = np.exp(-0.5 * (r / sd) ** 2) / sd
    out = np.exp(-0.5 * ((a_meas - offset) / sd_out) ** 2) / sd_out
    return (1 - eps) * good + eps * out


def prior() -> np.ndarray:
    """Before any measurement: equally likely moving (any speed) or standing (any mode)."""
    return np.r_[np.full(N_ST, 0.5 / N_ST), np.full(states.N_V, 0.5 / states.N_V)]
