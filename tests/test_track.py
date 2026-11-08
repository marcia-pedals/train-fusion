import numpy as np
import polars as pl

from train_fusion.stages.reference.track import MPH, Point, Restriction, SpeedLimits, load_speed_limits, on_shape

LIMITS = SpeedLimits(79, [Restriction(15, Point("A", -100), Point("A", 200)),
                          Restriction(40, Point("A", 200), Point("B", -500))])
STATIONS = pl.DataFrame({"station_id": ["A", "B"], "s_m": [1000.0, 5000.0]})


def test_northbound_offsets_add():
    sl = on_shape(LIMITS, STATIONS, direction_id=0, length_m=8000)
    assert list(sl.edges) == [0, 900, 1200, 4500, 8000]
    assert np.allclose(sl.mps / MPH, [79, 15, 40, 79])
    assert np.allclose(sl.at(np.array([0.0, 950, 1200, 7999, 9000])) / MPH, [79, 15, 40, 79, 79])


def test_southbound_offsets_subtract_and_missing_stations_skip():
    # southbound: s increases away from SF, so "towards SF" offsets subtract
    sb = pl.DataFrame({"station_id": ["B", "A"], "s_m": [3000.0, 7000.0]})
    sl = on_shape(LIMITS, sb, direction_id=1, length_m=8000)
    assert list(sl.edges) == [0, 3500, 6800, 7100, 8000]
    assert np.allclose(sl.mps / MPH, [79, 40, 15, 79])
    only_a = on_shape(LIMITS, STATIONS.filter(pl.col("station_id") == "A"), 0, 8000)
    assert np.allclose(only_a.mps / MPH, [79, 15, 79])


def test_repo_file_loads_and_is_sane():
    lim = load_speed_limits()
    assert lim.default_mph == 79 and lim.restrictions
    assert all(0 < r.mph < lim.default_mph for r in lim.restrictions)
