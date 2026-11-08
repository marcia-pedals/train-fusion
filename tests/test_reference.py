import numpy as np
import polars as pl

from train_fusion.stages.reference.physics import min_time_run
from train_fusion.stages.reference.stage import chain, constant_speed_stretches


def test_chain_adds_dwells_and_inverts():
    knots = {"A>B": (np.array([0.0, 500.0, 1000.0]), np.array([0.0, 50.0, 100.0])),
             "B>C": (np.array([0.0, 1000.0, 2000.0]), np.array([0.0, 60.0, 120.0]))}
    stops = pl.DataFrame({"station_id": ["A", "B", "C"], "s_m": [0.0, 1000.0, 3000.0], "sched_s": [0, 300, 600]})
    ref = chain("t", stops, 1000.0, knots, 30.0)
    assert list(ref.tau) == [0, 50, 100, 130, 190, 250]
    assert ref.phase == ["run", "run", "dwell", "run", "run", "end"]
    # a stop maps to the arrival there; positions inside runs interpolate
    assert list(ref.tau_at(np.array([0.0, 250.0, 1000.0, 2000.0, 3000.0]))) == [0, 25, 100, 190, 250]
    assert list(ref.run_of(np.array([10.0, 999.0, 1000.0, 2999.0, 3000.0]))) == [0, 0, 1, 1, 1]
    assert ref.dep_epoch == 1000.0


def test_constant_speed_stretches():
    lim = lambda x: np.where((x >= 2000) & (x < 2500), 15.0, 35.0)
    r = min_time_run(5000, lim, 1.0)
    st = constant_speed_stretches(r.x, r.v)
    # cruise at 35 after accelerating (612.5 m), braking for the restriction from ~1500 m,
    # 15 m/s through it, then 35 again until braking for the stop at ~4387.5 m
    assert len(st) == 3
    (a0, b0), (a1, b1), (a2, b2) = st
    assert abs(a0 - 612.5) < 10 and abs(b0 - 1500) < 10
    assert abs(a1 - 2000) < 10 and abs(b1 - 2500) < 10
    assert abs(b2 - (5000 - 612.5)) < 10
