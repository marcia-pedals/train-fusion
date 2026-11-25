"""Exact inference in a discrete HMM whose transition matrix may differ per step.

    forward_backward   smoothed and filtered state posteriors, log-likelihood
    fixed_lag          P(state at t | everything up to t + lag): what a live display can
                       show `lag` seconds late
    viterbi            the single most probable state sequence

`trans(t)` returns the [N, N] matrix for the step from frame t to t+1 (rows: from).
Nothing here knows what the states mean.
"""

import numpy as np


def forward_backward(log_b: np.ndarray, trans, prior: np.ndarray):
    """log_b [T, N]: log emission likelihoods. Returns (gamma, filtered, loglik)."""
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
    beta = np.ones(N)
    gamma = np.zeros((T, N))
    gamma[-1] = alpha[-1]
    for t in range(T - 2, -1, -1):
        beta = trans(t) @ (b[t + 1] * beta) / c[t + 1]
        g = alpha[t] * beta
        gamma[t] = g / g.sum()
    loglik = float(np.log(c).sum() + log_b.max(axis=1).sum())
    return gamma, alpha, loglik


def fixed_lag(log_b: np.ndarray, trans, alpha: np.ndarray, lag: int) -> np.ndarray:
    """[T, N] P(state_t | frames 0..t+lag), from the filtered posteriors `alpha` of
    forward_backward: a backward pass over the next `lag` frames for every t (the last
    frames see only what there is)."""
    T, N = log_b.shape
    b = np.exp(log_b - log_b.max(axis=1, keepdims=True))
    out = np.zeros((T, N))
    mats = {}
    for t in range(T):
        end = min(T - 1, t + lag)
        beta = np.ones(N)
        for u in range(end - 1, t - 1, -1):
            if u not in mats:
                mats[u] = trans(u)
            beta = mats[u] @ (b[u + 1] * beta)
            beta /= beta.sum()
        mats.pop(t, None)
        g = alpha[t] * beta
        out[t] = g / g.sum()
    return out


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
