"""Minimum-time run between two stops, from rest to rest, under speed limits.

The train accelerates at a and brakes at a (the same constant), and never exceeds the
speed limit. Computed on a grid of positions x:

    v(x) = min( limit(x),
                forward  pass: sqrt(v(x - dx)^2 + 2 a dx)   (can't accelerate faster),
                backward pass: sqrt(v(x + dx)^2 + 2 a dx) ) (must be able to brake in time)

with v = 0 at both ends. Between grid points speed changes at constant acceleration, so
the time per cell is exactly 2 dx / (v_i + v_{i+1}).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DX_M = 5.0


@dataclass(frozen=True)
class Run:
    x: np.ndarray      # positions from the start stop
    v: np.ndarray      # speed at x
    t: np.ndarray      # time at x since starting to move
    limit: np.ndarray  # speed limit at x

    @property
    def run_s(self) -> float:
        return float(self.t[-1])

    def knots(self, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """n knots evenly spaced in time: (p, t, v)."""
        tk = np.linspace(0.0, self.run_s, n)
        return np.interp(tk, self.t, self.x), tk, np.interp(tk, self.t, self.v)


def min_time_run(length_m: float, limit_at, a: float, dx: float = DX_M) -> Run:
    """limit_at(x) -> speed limit (m/s) at positions x from the start stop (vectorised)."""
    n = max(2, int(np.ceil(length_m / dx)) + 1)
    x = np.linspace(0.0, length_m, n)
    step = x[1] - x[0]
    limit = np.asarray(limit_at(x), float)
    v = limit.copy()
    v[0] = v[-1] = 0.0
    for i in range(1, n):
        v[i] = min(v[i], np.sqrt(v[i - 1] ** 2 + 2 * a * step))
    for i in range(n - 2, -1, -1):
        v[i] = min(v[i], np.sqrt(v[i + 1] ** 2 + 2 * a * step))
    dt = 2 * step / (v[:-1] + v[1:])
    return Run(x, v, np.concatenate([[0.0], np.cumsum(dt)]), limit)
