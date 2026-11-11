import polars as pl
import pytest

from train_fusion.stages.reference.timetable_fit import PatternMismatch, fit, pattern_bounds


def _tt(trips):
    """trips: {trip_id: [(station, static_s, lo, hi, source), ...]}"""
    rows = [(t, k, st, s, lo, hi, src) for t, stops in trips.items() for k, (st, s, lo, hi, src) in enumerate(stops)]
    return pl.DataFrame(rows, schema=["trip_id", "stop_idx", "station_id", "static_s", "dep_lo_s", "dep_hi_s", "source"],
                        orient="row")


def _pat(**trips):
    return pl.DataFrame({"trip_id": list(trips), "pattern_id": list(trips.values())})


def _static(start, rel):
    return [(st, start + r, start + r, start + r + 60, "static") for st, r in rel]


def _exact(start, rel):
    return [(st, start + r // 60 * 60, start + r, start + r, "tu") for st, r in rel]


R = {"A>B": 100.0, "B>C": 200.0, "C>D": 150.0}
# Δ = 1.1 R + 30 on A>B and B>C, and C>D (final, so no W) = 1.1 R
REL = [("A", 0), ("B", 140), ("C", 390), ("D", 555)]


def test_exact_times_recover_p_and_w():
    b = pattern_bounds(_tt({"t": _exact(0, REL)}), _pat(t="p1"))
    f = fit(b, R)
    assert f.p == pytest.approx(0.1) and f.W == pytest.approx(30.0)
    assert f.rows["pad_s"].to_list() == pytest.approx([0, 0, 0])
    assert f.rows["sched_s"].to_list() == pytest.approx([140, 250, 165])


def test_narrowed_trips_decide_the_pattern_bounds():
    b = pattern_bounds(_tt({"t": _exact(0, REL), "u": _exact(3600, REL), "v": _static(7200, [(s, r // 60 * 60) for s, r in REL])}),
                       _pat(t="p1", u="p1", v="p1"))
    assert b["lo_s"].to_list() == [0, 140, 390, 555] and b["hi_s"].to_list() == [0, 140, 390, 555]
    assert b["n_trips"].to_list() == [3] * 4 and b["narrowed"].all()


def test_static_only_pattern_uses_static_bounds():
    b = pattern_bounds(_tt({"t": _static(0, [("A", 0), ("B", 120)]), "u": _static(3600, [("A", 0), ("B", 120)])}),
                       _pat(t="p1", u="p1"))
    assert b["lo_s"].to_list() == [0, 120] and b["hi_s"].to_list() == [60, 180] and not b["narrowed"].any()


def test_narrowed_trips_that_disagree_stop_the_build():
    other = [("A", 0), ("B", 160), ("C", 390), ("D", 555)]
    with pytest.raises(PatternMismatch, match="at stop 1 .B. trip u has \\[160, 160\\] s but trip t has \\[140, 140\\] s"):
        pattern_bounds(_tt({"t": _exact(0, REL), "u": _exact(3600, other)}), _pat(t="p1", u="p1"))


def test_trips_narrowed_at_different_stops_combine():
    # t: 511 values for the first two stops only, u for the last two (as when pre-departure
    # updates list only the next few stops); static minutes elsewhere
    half = lambda start, n: _exact(start, REL)[:n] + _static(start, [(s, r // 60 * 60) for s, r in REL])[n:]
    late = lambda start, n: _static(start, [(s, r // 60 * 60) for s, r in REL])[:n] + _exact(start, REL)[n:]
    b = pattern_bounds(_tt({"t": half(0, 2), "u": late(3600, 2)}), _pat(t="p1", u="p1"))
    assert b["lo_s"].to_list() == [0, 140, 390, 555] and b["hi_s"].to_list() == [0, 140, 390, 555]
    assert b["narrowed"].all()


def test_unnarrowed_stops_keep_the_static_minute():
    b = pattern_bounds(_tt({"t": _exact(0, REL)[:1] + _static(0, [(s, r // 60 * 60) for s, r in REL])[1:]}), _pat(t="p1"))
    assert b["lo_s"].to_list() == [0, 120, 360, 540] and b["hi_s"].to_list() == [0, 180, 420, 600]


def test_narrowed_trips_that_differ_at_a_stop_stop_the_build_even_if_they_overlap():
    t = _tt({"t": [("A", 0, 0, 6, "tu"), ("B", 120, 140, 146, "tu")],
             "u": [("A", 3600, 3600, 3606, "tu"), ("B", 3720, 3742, 3748, "tu")]})
    with pytest.raises(PatternMismatch, match="at stop 1 .B. trip u has \\[142, 148\\] s but trip t has \\[140, 146\\] s"):
        pattern_bounds(t, _pat(t="p1", u="p1"))
