"""The inference code against brute force, and the learned pieces on synthetic data.

    python -m pytest -q tests        (from phone-sensor-fusion/)
"""

import itertools

import numpy as np

from features import comb
from model import emissions, hmm, states


def _brute(log_b, A, prior):
    T, N = log_b.shape
    post = np.zeros((T, N))
    best, best_p, total = None, -1.0, 0.0
    for path in itertools.product(range(N), repeat=T):
        p = prior[path[0]] * np.exp(log_b[0, path[0]])
        for t in range(1, T):
            p *= A[path[t - 1], path[t]] * np.exp(log_b[t, path[t]])
        total += p
        for t, s in enumerate(path):
            post[t, s] += p
        if p > best_p:
            best, best_p = path, p
    return post / total, np.log(total), np.array(best)


def test_forward_backward_and_viterbi_match_brute_force():
    rng = np.random.default_rng(0)
    T, N = 6, 3
    A = rng.random((N, N)); A /= A.sum(axis=1, keepdims=True)
    prior = rng.random(N); prior /= prior.sum()
    log_b = rng.normal(size=(T, N))
    gamma, alpha, loglik = hmm.forward_backward(log_b, lambda t: A, prior)
    post, ll, best = _brute(log_b, A, prior)
    assert np.allclose(gamma, post)
    assert np.isclose(loglik, ll)
    assert np.allclose(alpha[-1], post[-1])  # filtered = smoothed at the last frame
    assert (hmm.viterbi(log_b, lambda t: A, prior) == best).all()

def test_p_visit_matches_brute_force():
    rng = np.random.default_rng(1)
    T, N = 6, 3
    A = rng.random((N, N)); A /= A.sum(axis=1, keepdims=True)
    prior = rng.random(N); prior /= prior.sum()
    log_b = rng.normal(size=(T, N))
    m = hmm.messages(log_b, lambda t: A, prior)
    target = np.array([False, True, False])
    for t0, t1 in [(0, 6), (1, 4), (2, 3), (3, 6)]:
        for smoothed in (True, False):
            Tn = T if smoothed else t1  # filtered: the measurements up to t1 - 1
            total = hit = 0.0
            for path in itertools.product(range(N), repeat=Tn):
                p = prior[path[0]] * np.exp(log_b[0, path[0]])
                for t in range(1, Tn):
                    p *= A[path[t - 1], path[t]] * np.exp(log_b[t, path[t]])
                total += p
                hit += p * any(target[path[t]] for t in range(t0, t1))
            visit, avoid = hmm.p_visit(m, log_b, lambda t: A, target, t0, t1, smoothed)
            assert np.isclose(visit, hit / total) and np.isclose(avoid, 1 - hit / total)
    # only some frames of the window count
    total = hit = 0.0
    for path in itertools.product(range(N), repeat=T):
        p = prior[path[0]] * np.exp(log_b[0, path[0]])
        for t in range(1, T):
            p *= A[path[t - 1], path[t]] * np.exp(log_b[t, path[t]])
        total += p
        hit += p * (target[path[1]] or target[path[4]])
    frames = np.array([True, False, False, True, False])
    assert np.allclose(hmm.p_visit(m, log_b, lambda t: A, target, 1, 6, True, frames), (hit / total, 1 - hit / total))


def test_p_visit_keeps_tiny_probabilities():
    # a state that is almost never entered: P(visit) ~ 1e-30 must not come out as 0
    eps = 1e-15
    A = np.array([[1 - eps, eps], [0.5, 0.5]])
    log_b = np.zeros((3, 2))
    m = hmm.messages(log_b, lambda t: A, np.array([1.0, 0.0]))
    visit, avoid = hmm.p_visit(m, log_b, lambda t: A, np.array([False, True]), 0, 3, True)
    assert np.isclose(visit, 2 * eps, rtol=1e-6) and visit > 0 and np.isclose(avoid, 1)



def test_smooth_upto_is_smoothing_the_prefix():
    rng = np.random.default_rng(2)
    T, N = 8, 4
    A = rng.random((N, N)); A /= A.sum(axis=1, keepdims=True)
    prior = rng.random(N); prior /= prior.sum()
    log_b = rng.normal(size=(T, N))
    m = hmm.messages(log_b, lambda t: A, prior)
    for t1 in (0, 3, T - 1):
        assert np.allclose(hmm.smooth_upto(m, log_b, lambda t: A, t1), hmm.messages(log_b[:t1 + 1], lambda t: A, prior).gamma)


def test_transition_matrix_is_a_stop_cycle():
    p = {"dynamics": {"v_edges": [3.0, 8.0], "max_step": 2,
                      "p_step": [[0.1, 0.2, 0.4, 0.2, 0.1]] * 3},
         "dwell": {"1": 2.0, "2": 6.0, "3": 20.0, "4": 8.0, "5": 5.0}}
    A = emissions.transition(p)
    assert np.allclose(A.sum(axis=1), 1)
    PRE, CHIME, OPEN, CLOSE, POST = range(5)
    assert A[PRE, CHIME] > 0.4 and A[CHIME, OPEN] > 0.1 and A[OPEN, CLOSE] > 0.04
    assert A[OPEN, states.N_STOPPED:].sum() == 0  # doors open: can't pull away
    assert A[states.N_STOPPED, PRE] > 0.2  # slowest moving state can stop
    assert A[states.N_STOPPED + 40, :states.N_STOPPED].sum() < 1e-5  # at 10 m/s only via the chime re-anchor


def test_comb_recovers_a_planted_partial():
    rng = np.random.default_rng(1)
    f = np.arange(0, 1200, 2.0)
    v = np.r_[np.linspace(2, 35, 600), np.linspace(35, 2, 600)]
    spec = rng.gamma(2.0, 1.0, size=(len(v), len(f)))
    k = 25.0
    for t, vt in enumerate(v):
        spec[t, int(round(k * vt / 2.0))] += 18.0
    p = comb.fit([spec], f, [v], [np.ones(len(v), bool)], ratio_range=(12.0, 40.0), ratio_step=0.05)
    assert any(abs(r - k) < 0.2 for r in p["ratios"])
    grid = np.arange(1, 150) * 0.25
    ll = comb.apply(p, spec, f, grid)
    fast = v > 12
    assert np.mean(np.abs(grid[ll[fast].argmax(axis=1)] - v[fast]) < 0.5) > 0.95


def test_isotonic():
    assert np.allclose(comb._isotonic(np.array([1.0, 3.0, 2.0, 4.0]), np.ones(4)), [1, 2.5, 2.5, 4])
