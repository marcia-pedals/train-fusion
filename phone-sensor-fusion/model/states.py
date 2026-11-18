"""The hidden state of the train, and its discretisation.

    state = (mode, speed)

  mode   moving, or one of five ways of standing at a platform, visited in order:
         stopped (doors not yet open) -> door-open chime -> doors open
         -> door-close chime -> stopped (doors closed) -> moving
  speed  0 when not moving; otherwise on a grid of DV m/s up to V_MAX

There is no "not on the train" mode (it is labelled, presence.csv, but not
modelled yet): see P_REANCHOR in model/emissions.py.

So a state vector has N_STOPPED + N_V entries. Position is not part of this state: without
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
N = N_STOPPED + N_V

V_MOVING = DV * np.arange(1, N_V + 1)
V = np.r_[np.zeros(N_STOPPED), V_MOVING]  # speed of every state
MODE = np.r_[STOPPED, np.full(N_V, MOVING)]  # mode of every state


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
