import polars as pl
import pytest

from train_fusion.stages.timetable.stage import TimetableMismatch, combine

STOPS = pl.DataFrame({"trip_id": ["t", "t", "t"], "stop_idx": [0, 1, 2], "stop_id": ["a", "b", "c"],
                      "station_id": ["A", "B", "C"], "sched_s": [600, 720, 900]})


def _tu(rows):
    return pl.DataFrame(rows, schema=["trip_id", "stop_id", "tu_s", "date"], orient="row")


def test_trip_updates_narrow_static_minutes():
    tt = combine(STOPS, _tu([("t", "a", 612.0, "d1"), ("t", "a", 612.0, "d2"), ("t", "b", 750.0, "d1")]))
    assert tt["dep_lo_s"].to_list() == [612, 750, 900]
    assert tt["dep_hi_s"].to_list() == [618, 756, 960]
    assert tt["source"].to_list() == ["tu", "tu", "static"]
    assert tt["n_tu_dates"].to_list() == [2, 1, 0]


def test_trip_updates_that_disagree_stop_the_build():
    with pytest.raises(TimetableMismatch, match="trip t stop a: trip updates give different scheduled times"):
        combine(STOPS, _tu([("t", "a", 612.0, "d1"), ("t", "a", 618.0, "d2")]))


def test_static_must_be_the_trip_update_time_truncated():
    with pytest.raises(TimetableMismatch, match="trip t stop b: static time 720 s isn't the trip updates' 786 s"):
        combine(STOPS, _tu([("t", "b", 786.0, "d1")]))
