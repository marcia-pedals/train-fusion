"""The hidden state of the train, and its discretisation.

    state = (mode, speed), or before, or after

  mode   moving, or one of five ways of standing at a platform, visited in order:
         stopped (doors not yet open) -> door-open chime -> doors open
         -> door-close chime -> stopped (doors closed) -> moving
  speed  0 when not moving; otherwise on a grid of DV m/s up to V_MAX
  before nothing heard yet from the train the rider is going to take (not even its doors
         opening); a recording can start here, and once left it is never re-entered
  after  nothing more will be heard from it: the rider has got off; never left

So a state vector has N_ON = N_STOPPED + N_V on-train entries, then BEFORE_S and AFTER_S. Position is not part of this state: without
a map nothing depends on it, and it is recovered as the integral of speed. With a map it
is added as a third coordinate (model/map_hmm.py).
"""

import numpy as np

from labels.frames import AFTER, BEFORE, CLOSE_CHIME, MOVING, OPEN, OPEN_CHIME, POST, PRE

DT = 1.0  # s per frame
DV = 0.25  # m/s
V_MAX = 38.0  # m/s; the line limit is 79 mph = 35.3 m/s
N_V = int(V_MAX / DV)
STOPPED = [PRE, OPEN_CHIME, OPEN, CLOSE_CHIME, POST]  # modes 1..5 -> state index mode - 1
N_STOPPED = len(STOPPED)
N_ON = N_STOPPED + N_V
BEFORE_S, AFTER_S = N_ON, N_ON + 1  # state indices
N = N_ON + 2
MOVING_S = slice(N_STOPPED, N_ON)  # the moving states
OFF_S = [BEFORE_S, AFTER_S]

V_MOVING = DV * np.arange(1, N_V + 1)
V = np.r_[np.zeros(N_STOPPED), V_MOVING, 0.0, 0.0]  # speed of every state (0 off the train)
MODE = np.r_[STOPPED, np.full(N_V, MOVING), BEFORE, AFTER]  # mode of every state (labels.frames codes)
ON = MODE < BEFORE  # [N] the on-train states


def idx(mode: int) -> int:
    """State index of a stopped mode."""
    assert mode in STOPPED
    return mode - 1


def mode_probs(gamma: np.ndarray) -> np.ndarray:
    """[T, N] state posteriors -> [T, 8] mode posteriors (labels.frames order: the six train
    modes, then BEFORE and AFTER)."""
    out = np.zeros((len(gamma), 8))
    out[:, MOVING] = gamma[:, MOVING_S].sum(axis=1)
    for i, m in enumerate(STOPPED):
        out[:, m] += gamma[:, i]
    out[:, BEFORE], out[:, AFTER] = gamma[:, BEFORE_S], gamma[:, AFTER_S]
    return out
