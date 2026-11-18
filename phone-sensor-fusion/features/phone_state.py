"""Stage 2, learned: what is the phone doing?

  resting   lying still on a surface: the IMU measures the train
  held      in a hand, more or less still (reading, typing): the IMU measures the hand,
            the microphone still hears the train
  handled   being moved about (walking, boarding, pocketing): the IMU measures that, and
            the microphone picks up rubbing and knocks, so sound levels are unreliable too

A three-state HMM on one measurement, the log gyroscope RMS of each second: Gaussian
emissions per state and a sticky transition matrix, fitted to the phone labels. Applied as
a causal forward filter, so it works online. Its output gates the other measurements
(features/motion.py, model/emissions.py).

A state missing from the training data (one ride is all `held`/`handled`, the other all
`resting`/`handled`) keeps the prior below.
"""

import numpy as np

STATES = ("resting", "held", "handled")
RESTING, HELD, HANDLED = range(3)
# prior (used as pseudo-observations): mean, sd of log10 gyro rms (rad/s), from exploration
PRIOR = {"resting": (-2.1, 0.25), "held": (-0.8, 0.35), "handled": (0.0, 0.35)}
PRIOR_WEIGHT = 20.0  # in frames
PRIOR_SWITCH_PER_S = 1 / 200.0


def fit(frames: list[dict], labels: list) -> dict:
    x = np.concatenate([f["gyro_rms"] for f in frames])
    y = np.concatenate([l.phone for l in labels])
    p = {}
    for i, name in enumerate(STATES):
        m = y == i
        mu0, sd0 = PRIOR[name]
        n = m.sum()
        mu = (x[m].sum() + PRIOR_WEIGHT * mu0) / (n + PRIOR_WEIGHT)
        var = (((x[m] - mu) ** 2).sum() + PRIOR_WEIGHT * sd0**2) / (n + PRIOR_WEIGHT)
        p[name] = {"mean": float(mu), "sd": float(max(np.sqrt(var), 0.15)), "n_frames": int(n)}
    switches = sum(int(np.sum(l.phone[1:] != l.phone[:-1])) for l in labels)
    p["switch_per_s"] = float((switches + 1) / (len(x) + 1 / PRIOR_SWITCH_PER_S))
    return p


def apply(p: dict, f: dict) -> np.ndarray:
    """[T, 3] P(state | gyro so far)."""
    x = f["gyro_rms"]
    ll = np.stack([-0.5 * ((x - p[s]["mean"]) / p[s]["sd"]) ** 2 - np.log(p[s]["sd"]) for s in STATES], axis=1)
    q = p["switch_per_s"]
    A = np.full((3, 3), q / 2) + np.eye(3) * (1 - 1.5 * q)
    alpha = np.full(3, 1 / 3)
    out = np.zeros((len(x), 3))
    for t in range(len(x)):
        alpha = (alpha @ A) * np.exp(ll[t] - ll[t].max())
        alpha /= alpha.sum()
        out[t] = alpha
    return out
