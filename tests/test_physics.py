import math

import numpy as np

from train_fusion.stages.reference.physics import min_time_run


def const(v):
    return lambda x: np.full(len(x), v)


def test_matches_trapezoid_and_triangle():
    a, v = 1.0, 35.0
    long = min_time_run(5000, const(v), a)
    assert abs(long.run_s - (5000 / v + v / a)) < 0.5      # accelerate, cruise, brake
    short = min_time_run(800, const(v), a)                  # 800 m < v^2 / a: never reaches v
    assert abs(short.run_s - 2 * math.sqrt(800 / a)) < 0.5
    assert long.v[0] == long.v[-1] == 0 and long.v.max() <= v + 1e-9


def test_respects_restriction_and_brakes_before_it():
    lim = lambda x: np.where((x >= 2000) & (x < 2500), 15.0, 35.0)
    r = min_time_run(5000, lim, 1.0)
    assert np.all(r.v <= r.limit + 1e-9)
    assert np.all(r.v[(r.x >= 2000) & (r.x < 2500)] <= 15.0 + 1e-9)
    # braking from 35 to 15 m/s at 1 m/s^2 takes (35^2 - 15^2) / 2 = 500 m: at 1600 m it's still at line speed
    assert r.v[np.searchsorted(r.x, 1490)] > 34.9
    free = min_time_run(5000, const(35.0), 1.0)
    assert r.run_s > free.run_s


def test_knots_span_the_run():
    r = min_time_run(3000, const(35.0), 1.0)
    p, t, v = r.knots(21)
    assert p[0] == 0 and abs(p[-1] - 3000) < 1e-6 and t[-1] == r.run_s and np.all(np.diff(p) > 0)
