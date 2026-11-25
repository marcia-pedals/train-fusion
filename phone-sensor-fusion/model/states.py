"""The hidden state of the train and the rider, and its discretisation.

    state = (mode, speed) of the train, with the rider on it
          | before the ride | after the ride

  mode   moving, or one of five ways of standing at a platform, visited in order:
         stopped (doors not yet open) -> door-open chime -> doors open
         -> door-close chime -> stopped (doors closed) -> moving
  speed  0 when not moving; otherwise on a grid of DV m/s up to V_MAX

Off the train the train's speed and mode are none of our business. A recording holds at most
one ride: before -> on the train -> after. The rider gets on and off through open doors and
only while walking (model/emissions.py, model/infer.py).

So a state vector has N_STOPPED + N_V + 2 entries. Position is not part of this state: without
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
N_ON = N_STOPPED + N_V
BEFORE, AFTER = N_ON, N_ON + 1  # state indices: off the train, before and after the ride
N_OFF = 2
N = N_ON + N_OFF
OFF_MODE = 6  # the "mode" of every off-train state (MODE, mode_probs)

V_MOVING = DV * np.arange(1, N_V + 1)
V = np.r_[np.zeros(N_STOPPED), V_MOVING, np.zeros(N_OFF)]  # speed of every state (0 off the train)
MODE = np.r_[STOPPED, np.full(N_V, MOVING), np.full(N_OFF, OFF_MODE)]  # mode of every state
MOVING_SLICE = slice(N_STOPPED, N_ON)


def idx(mode: int) -> int:
    """State index of a stopped mode."""
    assert mode in STOPPED
    return mode - 1


def mode_probs(gamma: np.ndarray) -> np.ndarray:
    """[T, N] state posteriors -> [T, 7] mode posteriors (labels.frames order, then off the train)."""
    out = np.zeros((len(gamma), 7))
    out[:, MOVING] = gamma[:, MOVING_SLICE].sum(axis=1)
    for i, m in enumerate(STOPPED):
        out[:, m] += gamma[:, i]
    out[:, OFF_MODE] = gamma[:, N_ON:].sum(axis=1)
    return out


def on_train(gamma: np.ndarray) -> np.ndarray:
    """[T, N] state posteriors -> [T] P(the rider is on the train)."""
    return gamma[:, :N_ON].sum(axis=1)
