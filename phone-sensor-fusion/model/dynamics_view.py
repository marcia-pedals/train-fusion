"""What the viewer's dynamics page shows (viewer/src/dynamics/): the train dynamics
(model/fit.py fit_dynamics) fitted on every labelled ride, and the labelled seconds they
were fitted from.

    <run>/dynamics.json
      dv, max_step, a_coast   the discretisation (model/states.py), the coasting threshold
      regimes       their names (states.REGIMES)
      speeds        the tables' rows: 0 (pulling away), then the speed grid, m/s
      p_step        [regime][speed][step]: P(speed change of (step - max_step) * dv over the
                    next second | that second's regime, speed)
      p_regime      [regime][speed][next regime]
      bandwidth     {"step", "regime"}: [regime][speed], the kernel sd each row was smoothed
                    with, m/s
      n             per regime: seconds behind its step table, switches seen out of it
      split         the part / full traction mixture the regimes were labelled with
      points        {"v", "a", "regime"}: every labelled second used, its speed, its speed
                    change over the next second (m/s^2) and its regime

The cross-validation folds each fit their own dynamics on the other rides; this is the fit
on all of them, which is what params/all.json gets.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from labels.frames import FrameLabels
from model import fit, states


def export(labels: list[FrameLabels], run: Path) -> None:
    d = fit.fit_dynamics(labels)
    label = fit.regime_labels(d["split"])
    v_pts, a_pts, r_pts = [], [], []
    for l in labels:
        v, c = np.nan_to_num(l.v), l.confident & np.isfinite(l.v)
        g = label(l.v, l.confident)
        ok = c[:-1] & c[1:] & ((v[:-1] > 0) | (v[1:] > 0)) & (g[:-1] >= 0)
        v_pts.append(v[:-1][ok]); a_pts.append((v[1:] - v[:-1])[ok] / states.DT); r_pts.append(g[:-1][ok])
    out = {
        "dv": states.DV, "max_step": d["max_step"], "a_coast": d["a_coast"], "regimes": list(states.REGIMES),
        "speeds": np.r_[0.0, states.V_MOVING].tolist(),
        "p_step": np.round(d["p_step"], 6).tolist(), "p_regime": d["p_regime"],
        "bandwidth": {k: np.round(v, 3).tolist() for k, v in d["bandwidth"].items()}, "n": d["n"], "split": d["split"],
        "points": {"v": np.round(np.concatenate(v_pts), 2).tolist(), "a": np.round(np.concatenate(a_pts), 3).tolist(),
                   "regime": np.concatenate(r_pts).astype(int).tolist()},
    }
    (run / "dynamics.json").write_text(json.dumps(out, separators=(",", ":")))
