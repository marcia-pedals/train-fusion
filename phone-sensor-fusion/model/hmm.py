"""Exact inference in a discrete HMM whose transition matrix may differ per step.

    forward_backward   smoothed and filtered state posteriors, log-likelihood
    messages           the same, keeping what p_visit needs
    smooth_upto        smoothed posteriors given only the measurements up to a frame
    p_visit            how likely the chain was in a set of states at least once in a window
    viterbi            the single most probable state sequence

`trans(t)` returns the [N, N] matrix for the step from frame t to t+1 (rows: from), dense
or a scipy.sparse array: the train's is almost all zeros (a moving state reaches ~50 of
600), and kept in compressed-column form (csc_array) it makes every step cost what its
nonzeros do. Nothing here knows what the states mean.
"""

from dataclasses import dataclass

import numpy as np
from scipy import sparse


@dataclass
class Messages:
    gamma: np.ndarray  # [T, N] smoothed posteriors
    alpha: np.ndarray  # [T, N] filtered posteriors (scaled forward messages)
    beta: np.ndarray  # [T, N] scaled backward messages: gamma = alpha * beta
    c: np.ndarray  # [T] the forward pass's scale factors
    loglik: float


def messages(log_b: np.ndarray, trans, prior: np.ndarray) -> Messages:
    """log_b [T, N]: log emission likelihoods."""
    T, N = log_b.shape
    b = np.exp(log_b - log_b.max(axis=1, keepdims=True))
    alpha = np.zeros((T, N))
    c = np.zeros(T)
    a = prior * b[0]
    for t in range(T):
        if t:
            a = (alpha[t - 1] @ trans(t - 1)) * b[t]
        c[t] = a.sum()
        alpha[t] = a / c[t]
    beta = np.ones((T, N))
    for t in range(T - 2, -1, -1):
        beta[t] = trans(t) @ (b[t + 1] * beta[t + 1]) / c[t + 1]
    gamma = alpha * beta
    gamma /= gamma.sum(axis=1, keepdims=True)
    loglik = float(np.log(c).sum() + log_b.max(axis=1).sum())
    return Messages(gamma, alpha, beta, c, loglik)


def forward_backward(log_b: np.ndarray, trans, prior: np.ndarray):
    """Returns (gamma, filtered, loglik)."""
    m = messages(log_b, trans, prior)
    return m.gamma, m.alpha, m.loglik


def smooth_upto(m: Messages, log_b: np.ndarray, trans, t1: int) -> np.ndarray:
    """[t1 + 1, N] P(state at each frame up to t1 | measurements up to t1): the forward pass
    is the one already done; only the backward pass is run again, from t1."""
    gamma = np.zeros((t1 + 1, m.alpha.shape[1]))
    beta = np.ones(m.alpha.shape[1])
    gamma[t1] = m.alpha[t1]
    for t in range(t1 - 1, -1, -1):
        b = np.exp(log_b[t + 1] - log_b[t + 1].max())
        beta = trans(t) @ (b * beta) / m.c[t + 1]
        g = m.alpha[t] * beta
        gamma[t] = g / g.sum()
    return gamma


def p_visit(m: Messages, log_b: np.ndarray, trans, states: np.ndarray, t0: int, t1: int, smoothed: bool,
            frames: np.ndarray | None = None) -> tuple[float, float]:
    """(P(the chain is in one of `states` (bool [N]) at some frame in [t0, t1)), P(it never
    is)), given all the measurements (smoothed) or those up to frame t1 - 1 (filtered).
    `frames` (bool [t1 - t0]): count only these frames of the window.

    The forward pass again over the window, on two copies of the chain: paths that have not
    been in the set yet, and paths that have (a path moves to the second copy when it enters
    the set). Same scale factors, so what each copy holds at the end is that probability
    (times the backward message, when smoothed). Neither is one minus the other, so both
    keep their precision however close to 0 or 1 they are."""
    if frames is None:
        frames = np.ones(t1 - t0, bool)
    lo, hi = max(t0, 0), min(t1, len(log_b))
    frames = frames[lo - t0:hi - t0]
    t0, t1 = lo, hi
    if t1 <= t0 or not frames.any():
        return float("nan"), float("nan")
    hit = states.astype(float)
    a_no = m.alpha[t0] * ((1 - hit) if frames[0] else 1)
    a_yes = m.alpha[t0] * hit if frames[0] else np.zeros_like(hit)
    for t in range(t0 + 1, t1):
        b = np.exp(log_b[t] - log_b[t].max()) / m.c[t]
        A = trans(t - 1)
        no, yes = (a_no @ A) * b, (a_yes @ A) * b
        if frames[t - t0]:
            no, yes = no * (1 - hit), yes + no * hit
        a_no, a_yes = no, yes
    w = m.beta[t1 - 1] if smoothed else 1.0
    return float((a_yes * w).sum()), float((a_no * w).sum())


def _best_into(d: np.ndarray, A) -> tuple[np.ndarray, np.ndarray]:
    """For every state j: max over i of d[i] + log A[i, j], and the i that gives it."""
    if not sparse.issparse(A):
        m = d[:, None] + np.log(A)
        i = np.argmax(m, axis=0)
        return m[i, np.arange(len(d))], i
    A = sparse.csc_array(A)  # column j's predecessors are indices[indptr[j]:indptr[j + 1]]
    vals = d[A.indices] + np.log(A.data)
    starts, n = A.indptr[:-1], np.diff(A.indptr)
    empty = n == 0
    s = np.minimum(starts, max(len(vals) - 1, 0))
    best = np.maximum.reduceat(vals, s) if len(vals) else np.full(len(d), -np.inf)
    best[empty] = -np.inf
    # the first position in each column holding its maximum
    pos = np.where(vals == np.repeat(best, n), np.arange(len(vals)), len(vals))
    first = np.minimum.reduceat(pos, s) if len(vals) else np.zeros(len(d), int)
    i = np.where(empty, 0, A.indices[np.minimum(first, len(vals) - 1)])
    return best, i


def viterbi(log_b: np.ndarray, trans, prior: np.ndarray) -> np.ndarray:
    T, N = log_b.shape
    with np.errstate(divide="ignore"):
        d = np.log(prior) + log_b[0]
        back = np.zeros((T, N), dtype=np.int32)
        for t in range(1, T):
            best, back[t] = _best_into(d, trans(t - 1))
            d = best + log_b[t]
    path = np.zeros(T, dtype=int)
    path[-1] = int(np.argmax(d))
    for t in range(T - 1, 0, -1):
        path[t - 1] = back[t, path[t]]
    return path
