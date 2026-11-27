"""Exact inference in a discrete HMM whose transition matrix may differ per step.

    forward_backward   smoothed and filtered state posteriors, log-likelihood
    messages           the same, keeping what p_visit needs
    smooth_upto        smoothed posteriors given only the measurements up to a frame
    p_visit            how likely the chain was in a set of states at least once in a window
    viterbi            the single most probable state sequence

`trans(t)` returns the [N, N] matrix for the step from frame t to t+1 (rows: from).
Nothing here knows what the states mean.

Messages are kept as logs. Scaled probabilities (the usual way) hold at most ~1e308
between the most and least likely state of a frame, and states that can't be left
(model/states.py: after) go beyond that: the filter can be sure of one for a while, and
later measurements prove it wrong. With logs that is an ordinary, finite answer.
"""

from dataclasses import dataclass

import numpy as np


def _lse(x: np.ndarray, axis=None) -> np.ndarray:
    """log(sum(exp(x))), -inf for all -inf."""
    m = np.max(x, axis=axis, keepdims=True)
    m = np.where(np.isfinite(m), m, 0.0)
    with np.errstate(divide="ignore"):
        out = np.log(np.sum(np.exp(x - m), axis=axis, keepdims=True)) + m
    return out.squeeze(axis) if axis is not None else out.item()


def _log(A: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore"):
        return np.log(A)


def _fwd(la: np.ndarray, log_a: np.ndarray) -> np.ndarray:
    """log(exp(la) @ A): a forward step."""
    return _lse(la[:, None] + log_a, axis=0)


def _bwd(lb: np.ndarray, log_a: np.ndarray) -> np.ndarray:
    """log(A @ exp(lb)): a backward step."""
    return _lse(log_a + lb[None, :], axis=1)


@dataclass
class Messages:
    gamma: np.ndarray  # [T, N] smoothed posteriors
    alpha: np.ndarray  # [T, N] filtered posteriors
    log_alpha: np.ndarray  # [T, N] their logs (exact where alpha underflows)
    log_beta: np.ndarray  # [T, N] backward messages, each frame's up to a constant: gamma ∝ alpha * exp(log_beta)
    loglik: float


def messages(log_b: np.ndarray, trans, prior: np.ndarray) -> Messages:
    """log_b [T, N]: log emission likelihoods."""
    T, N = log_b.shape
    la = np.zeros((T, N))
    loglik = 0.0
    a = _log(prior) + log_b[0]
    for t in range(T):
        if t:
            a = _fwd(la[t - 1], _log(trans(t - 1))) + log_b[t]
        c = _lse(a)
        la[t] = a - c
        loglik += c
    lbeta = np.zeros((T, N))
    for t in range(T - 2, -1, -1):
        x = _bwd(log_b[t + 1] + lbeta[t + 1], _log(trans(t)))
        lbeta[t] = x - _lse(x)
    lg = la + lbeta
    gamma = np.exp(lg - _lse(lg, axis=1)[:, None])
    return Messages(gamma, np.exp(la), la, lbeta, float(loglik))


def forward_backward(log_b: np.ndarray, trans, prior: np.ndarray):
    """Returns (gamma, filtered, loglik)."""
    m = messages(log_b, trans, prior)
    return m.gamma, m.alpha, m.loglik


def smooth_upto(m: Messages, log_b: np.ndarray, trans, t1: int) -> np.ndarray:
    """[t1 + 1, N] P(state at each frame up to t1 | measurements up to t1): the forward pass
    is the one already done; only the backward pass is run again, from t1."""
    gamma = np.zeros((t1 + 1, m.alpha.shape[1]))
    lbeta = np.zeros(m.alpha.shape[1])
    gamma[t1] = m.alpha[t1]
    for t in range(t1 - 1, -1, -1):
        x = _bwd(log_b[t + 1] + lbeta, _log(trans(t)))
        lbeta = x - _lse(x)
        g = m.log_alpha[t] + lbeta
        gamma[t] = np.exp(g - _lse(g))
    return gamma


def p_visit(m: Messages, log_b: np.ndarray, trans, states: np.ndarray, t0: int, t1: int, smoothed: bool,
            frames: np.ndarray | None = None) -> tuple[float, float]:
    """(P(the chain is in one of `states` (bool [N]) at some frame in [t0, t1)), P(it never
    is)), given all the measurements (smoothed) or those up to frame t1 - 1 (filtered).
    `frames` (bool [t1 - t0]): count only these frames of the window.

    The forward pass again over the window, on two copies of the chain: paths that have not
    been in the set yet, and paths that have (a path moves to the second copy when it enters
    the set). Each is then weighed by the backward message, when smoothed. Neither is one
    minus the other, so both keep their precision however close to 0 or 1 they are."""
    if frames is None:
        frames = np.ones(t1 - t0, bool)
    lo, hi = max(t0, 0), min(t1, len(log_b))
    frames = frames[lo - t0:hi - t0]
    t0, t1 = lo, hi
    if t1 <= t0 or not frames.any():
        return float("nan"), float("nan")
    hit = np.where(states, 0.0, -np.inf)  # log of the indicator
    miss = np.where(states, -np.inf, 0.0)
    a_no = m.log_alpha[t0] + (miss if frames[0] else 0.0)
    a_yes = m.log_alpha[t0] + hit if frames[0] else np.full(len(hit), -np.inf)
    for t in range(t0 + 1, t1):
        log_a = _log(trans(t - 1))
        no, yes = _fwd(a_no, log_a) + log_b[t], _fwd(a_yes, log_a) + log_b[t]
        if frames[t - t0]:
            no, yes = no + miss, np.logaddexp(yes, no + hit)
        k = _lse(np.logaddexp(no, yes))  # (any common constant: keeps the numbers in range)
        a_no, a_yes = no - k, yes - k
    w = m.log_beta[t1 - 1] if smoothed else 0.0
    total = _lse(np.logaddexp(a_yes, a_no) + w)
    return float(np.exp(_lse(a_yes + w) - total)), float(np.exp(_lse(a_no + w) - total))


def viterbi(log_b: np.ndarray, trans, prior: np.ndarray) -> np.ndarray:
    T, N = log_b.shape
    with np.errstate(divide="ignore"):
        d = np.log(prior) + log_b[0]
        back = np.zeros((T, N), dtype=np.int32)
        for t in range(1, T):
            m = d[:, None] + np.log(trans(t - 1))
            back[t] = np.argmax(m, axis=0)
            d = m[back[t], np.arange(N)] + log_b[t]
    path = np.zeros(T, dtype=int)
    path[-1] = int(np.argmax(d))
    for t in range(T - 1, 0, -1):
        path[t - 1] = back[t, path[t]]
    return path
