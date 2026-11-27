"""The hidden state of the train, and its discretisation.

    state = (mode, regime, speed)

  mode    moving, or one of five ways of standing at a platform, visited in order:
          stopped (doors not yet open) -> door-open chime -> doors open
          -> door-close chime -> stopped (doors closed) -> moving
  regime  when moving: braking, coasting, or accelerating at part or at full traction: how
          the speed changed over the last second. It is what gives the dynamics a memory: a
          train that is braking keeps braking, and doesn't switch to full traction from one
          second to the next
  speed   0 when not moving; otherwise on a grid of DV m/s up to V_MAX

There is no "not on the train" mode (it is labelled, presence.csv, but not
modelled yet): see P_REANCHOR in model/emissions.py.

So a state vector has N_STOPPED + N_R x N_V entries: the stopped modes, then the speed
grid once per regime. Position is not part of this state: without
a map nothing depends on it, and it is recovered as the integral of speed. With a map it
is added as a third coordinate (model/map_hmm.py).
"""

import numpy as np

from labels.frames import CLOSE_CHIME, MOVING, OPEN, OPEN_CHIME, POST, PRE

DT = 1.0  # s per frame
DV = 0.25  # m/s
V_MAX = 38.0  # m/s; the line limit is 79 mph = 35.3 m/s
N_V = int(V_MAX / DV)
STOPPED = [PRE, OPEN_CHIME, OPEN, CLOSE_CHIME, POST]  # modes 1..5 -> state index mode - 1
N_STOPPED = len(STOPPED)

BRAKING, COASTING, ACCEL_PART, ACCEL_FULL = range(4)
REGIMES = ("braking", "coasting", "accelerating, part", "accelerating, full")  # traction, when accelerating
N_R = len(REGIMES)
N = N_STOPPED + N_R * N_V

V_MOVING = DV * np.arange(1, N_V + 1)  # the speed grid
V = np.r_[np.zeros(N_STOPPED), np.tile(V_MOVING, N_R)]  # speed of every state
MODE = np.r_[STOPPED, np.full(N_R * N_V, MOVING)]  # mode of every state
REGIME = np.r_[np.full(N_STOPPED, -1), np.repeat(np.arange(N_R), N_V)]  # regime of every state; -1 standing


def moving(regime: int, j) -> int:
    """State index of moving in `regime` at speed V_MOVING[j]."""
    return N_STOPPED + regime * N_V + j


def over_regimes(x: np.ndarray) -> np.ndarray:
    """[..., N_V] something per speed -> [..., N_R * N_V] the same for every moving state."""
    return np.tile(x, N_R)


def speed_probs(post: np.ndarray) -> np.ndarray:
    """[T, N] state probabilities -> [T, 1 + N_V]: standing (all stopped modes), then each
    speed of the grid (all regimes)."""
    return np.c_[post[:, :N_STOPPED].sum(axis=1), post[:, N_STOPPED:].reshape(len(post), N_R, N_V).sum(axis=1)]


def regime_probs(post: np.ndarray) -> np.ndarray:
    """[T, N] state probabilities -> [T, N_R]: each regime (moving at any speed)."""
    return post[:, N_STOPPED:].reshape(len(post), N_R, N_V).sum(axis=2)


def idx(mode: int) -> int:
    """State index of a stopped mode."""
    assert mode in STOPPED
    return mode - 1


def mode_probs(gamma: np.ndarray) -> np.ndarray:
    """[T, N] state posteriors -> [T, 6] mode posteriors (labels.frames order)."""
    out = np.zeros((len(gamma), 6))
    out[:, MOVING] = gamma[:, N_STOPPED:].sum(axis=1)
    for i, m in enumerate(STOPPED):
        out[:, m] += gamma[:, i]
    return out
