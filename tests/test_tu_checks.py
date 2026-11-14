import pytest

from train_fusion.stages.tu_checks.stage import (Snap, Trip, TuHypothesisViolation, check_h1, check_h2, check_h3_h4,
                                                 monotone)

T0 = 1_800_000_000.0  # a snapshot time
STOPS = ["a", "b", "c", "d"]


def _trip(dep0=T0 + 720):
    return Trip("2026-11-09", "101", STOPS, {s: dep0 + 300 * k for k, s in enumerate(STOPS)}, dep0)


def _snap(t, trips, exact=True, first=True):
    return Snap(t, exact, len(trips), first, trips)


def _listed(x, stops, delay=0):
    """x's stops as a TU lists them: departure delay s late, arrival 60 s before it."""
    return [(s, int(x.sched[s] + delay - 60), int(x.sched[s] + delay)) for s in stops]


def test_h3_h4_pass_on_good_updates():
    x = _trip()
    snaps = [_snap(T0, {"101": _listed(x, STOPS[:-1])})]
    assert check_h3_h4(snaps, [(0, snaps[0].trips["101"], x)], {}) == {"stops": 3}


@pytest.mark.parametrize("stops, match", [
    ([("a", None, int(T0) - 30)], "H3: .* 30 s in the past"),
    ([("a", int(T0) + 100, int(T0) + 130)], "H4: .* arrival"),
    ([("a", int(T0) + 100, None)], "H4: .* has no departure"),
])
def test_h3_h4_violations_stop_the_build(stops, match):
    snaps = [_snap(T0, {"101": stops})]
    with pytest.raises(TuHypothesisViolation, match=match):
        check_h3_h4(snaps, [(0, stops, _trip())], {})


def test_h3_allows_an_inexact_content_time_to_be_late():
    stops = [("a", None, int(T0) - 10)]
    snaps = [_snap(T0, {"101": stops}, exact=False)]
    check_h3_h4(snaps, [(0, stops, _trip())], {})


def test_monotone_is_same_or_minus_a_prefix():
    p = [("a", 1.0), ("b", 2.0), ("c", 3.0)]
    assert monotone(p, p, cut=10)
    assert monotone(p, p[1:], cut=10)
    assert not monotone(p, [p[0], p[2]], cut=10)  # lost a stop in the middle
    assert not monotone(p[1:], p, cut=10)  # grew at the start
    assert monotone(p, p[:2], cut=2.5)  # beyond the horizon cut, ignored


def test_h1_needs_a_complete_sighting_in_the_window():
    x = _trip()
    snaps = [_snap(T0 - 600, {}), _snap(T0, {"101": _listed(x, STOPS[:-1])}), _snap(T0 + 600, {})]
    x.obs = [(1, snaps[1].trips["101"])]
    assert check_h1(x, snaps, {})["outcome"] == "ok"
    x.obs = [(1, _listed(x, STOPS[:1]))]
    assert check_h1(x, snaps, {})["outcome"] == "failed"
    x.obs = []
    assert check_h1(x, snaps, {}) is None  # not seen in the window: not judged


def test_h2_counts_non_monotone_changes_and_allows_dropouts():
    x = _trip()
    # a dropout (None), a prefix lost twice, then b back: one failure
    lists = [STOPS[:-1], STOPS[:-1], None, STOPS[1:-1], ["c"], ["b", "c"]]
    snaps = [_snap(T0 + 60 * k, {} if s is None else {"101": _listed(x, s)}) for k, s in enumerate(lists)]
    x.obs = [(k, s.trips["101"]) for k, s in enumerate(snaps) if s.trips]
    r = check_h2(x, snaps, {})
    assert (r["outcome"], r["n_failures"], r["at_epoch"]) == ("failed", 1, T0 + 300)
