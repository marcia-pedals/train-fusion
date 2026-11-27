"""Turn a recording's measurements into the HMM's ingredients, using learned parameters:

  log_emissions   log p(frame's measurements | state), [T, N]
  transition      the [N, N] matrix P(next state | state): train dynamics and the
                  station-stop cycle
  accel_factor    p(along-track acceleration measured during a step | the step's speed
                  change), which multiplies the transition matrix for that step

Each measurement can be switched off (`use`), which is how the ablations are run.

Off the train (states.BEFORE_S, AFTER_S) the phone hears what it would standing still,
except that neither chime is heard (P_CHIME_OFF) and neither is the car's hum (P_HUM_OFF):
hearing either means being on, or about to get on, the train.
"""

from __future__ import annotations

import numpy as np

from features import comb, frames, phone_state
from labels.frames import CLOSE_CHIME, OPEN, OPEN_CHIME, POST, PRE
from model import states
from model.fit import CHIME_EDGES, walking

ALL = ("chime", "tone", "wheel", "rumble", "vib", "accel", "cabin", "walk")
P_RESUME = 0.01  # per s: pulling away again without the doors having opened (signal stop)
P_REOPEN = 0.002  # per s: doors re-opening after they closed
ACCEL_OUTLIER = (0.05, 1.0)  # share and sd (m/s^2) of readings that aren't the train (a nudge)
LEVEL_OUTLIER, LEVEL_FLOOR = 0.1, 0.1  # share of such levels; their density relative to the peak
# per s: from any on-train state straight into the door-open chime. A wrong belief (say a
# fast train where the platform tones sit where the motor tone is at 36 m/s), from which
# no braking curve reaches the stop in time, can't otherwise explain a door-open chime,
# which is unmistakable; so it is allowed to override the belief, at this price.
P_REANCHOR = 1e-6
# per s, getting on and off (states.py: before -> on -> after, once each). Set by hand.
P_BOARD_CHIME = 2e-3  # before -> door-open chime: the doors are heard opening, then the rider boards
P_BOARD_QUIET = 2e-4  # before -> stopped (doors not yet open): boarding without having heard them open
P_ALIGHT = 1e-3  # doors open -> after, and door-close chime -> after (heard from the platform)
P_CHIME_OFF = 1e-5  # off the train: P(each chime measurement category but "none")
P_HUM_OFF = 1e-3  # off the train: P(the car's hum)
# EXPERIMENT switches (scratch runner sets them)
OFF_CHIMES = "quiet"  # "quiet": P_CHIME_OFF; "like on": as a stopped train between chimes
OFF_EMIT = "standing"  # "standing": off hears what a standing train would; "mixture": the average over every on state
BOARD_INTO = ("open chime", "pre")  # stopped modes boarding enters; + "open": doors already open
N_ST = states.N_STOPPED


def _level_ll(q: dict, x: np.ndarray) -> np.ndarray:
    """[T, N] weighted Gaussian log-likelihood of a level measurement per state."""
    knots, mean = np.asarray(q["knots"]), np.asarray(q["mean"])
    mu = np.r_[np.full(N_ST, mean[0]), np.interp(states.V_MOVING, knots[1:], mean[1:]), np.full(2, mean[0])]
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


def log_emissions(p: dict, f: dict, phone: np.ndarray, use=ALL) -> np.ndarray:
    """phone: [T, 3] from features/phone_state.py; it decides which measurements count."""
    T = len(phone)
    lb = np.zeros((T, states.N))
    state = phone.argmax(axis=1)
    resting, held = state == phone_state.RESTING, state == phone_state.HELD
    if "tone" in use:
        lb[:, states.MOVING_S] += _relative(
            comb.apply(p["tone"], f["tone_spec"], f["tone_f"], states.V_MOVING, frames.TONE_POOL_BINS))
    if "wheel" in use:
        lb[:, states.MOVING_S] += wheel_ll(p, f, state)
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
            ll_off = np.log(np.r_[1 - 2 * P_CHIME_OFF, P_CHIME_OFF, P_CHIME_OFF])[cat]
            lb[:, states.ON] += ll_out[:, None]
            lb[:, states.idx(mode)] += ll_in - ll_out
            lb[:, states.OFF_S] += (ll_out if OFF_CHIMES == "like on" else ll_off)[:, None]
    if OFF_EMIT == "mixture":
        # off the train anything goes: each second, the average of what the train's states
        # would give (half standing, half moving at any speed). Only a coherent ride beats it.
        on_w = np.r_[np.full(N_ST, 0.5 / N_ST), np.full(states.N_V, 0.5 / states.N_V)]
        x = lb[:, states.ON] + np.log(on_w)
        m = x.max(axis=1, keepdims=True)
        lb[:, states.OFF_S] = (m + np.log(np.exp(x - m).sum(axis=1, keepdims=True)))
    if "cabin" in use:
        hum = f["cabin_db"] >= p["cabin"]["hum_db"]
        p_on = p["cabin"]["p_hum_on"]
        lb[:, states.ON] += np.log(np.where(hum, p_on, 1 - p_on))[:, None]
        lb[:, states.OFF_S] += np.log(np.where(hum, P_HUM_OFF, 1 - P_HUM_OFF))[:, None]
    if "walk" in use and "walk" in p:
        w = walking(f)
        lw = lambda q: np.log(np.where(w, q, 1 - q))[:, None]
        lb[:, :N_ST] += lw(p["walk"]["stopped"])
        lb[:, states.MOVING_S] += lw(p["walk"]["moving"])
        lb[:, states.OFF_S] += lw(p["walk"]["off"])
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
    A[states.idx(OPEN), states.AFTER_S] = A[states.idx(CLOSE_CHIME), states.AFTER_S] = P_ALIGHT
    for m in order:
        A[states.idx(m), states.idx(m)] = 1.0 - A[states.idx(m)].sum()
    on = states.ON
    A[on] *= 1 - P_REANCHOR
    A[on, states.idx(OPEN_CHIME)] += P_REANCHOR
    B = states.BEFORE_S
    A[B, states.idx(OPEN_CHIME)], A[B, states.idx(PRE)] = P_BOARD_CHIME, P_BOARD_QUIET
    if "open" in BOARD_INTO:
        A[B, states.idx(OPEN)] = P_BOARD_QUIET
    A[B, B] = 1 - A[B].sum()
    A[states.AFTER_S, states.AFTER_S] = 1.0
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
    f = (1 - eps) * good + eps * out
    if OFF_EMIT == "mixture":  # off the train the phone's acceleration is anyone's
        f[states.OFF_S, :] = out
    return f


def prior() -> np.ndarray:
    """Before any measurement: half before boarding; half on the train, equally likely moving
    (any speed) or standing (any mode). Never after."""
    return np.r_[np.full(N_ST, 0.25 / N_ST), np.full(states.N_V, 0.25 / states.N_V), 0.5, 0.0]
