import pytest

from train_fusion.stages.tu_checks.stage import (HORIZON_S, Snap, Trip, TuHypothesisViolation, check_h1, check_h2, check_h3_h4)

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


def test_h1_needs_a_complete_sighting_in_the_window():
    x = _trip()
    snaps = [_snap(T0 - 600, {}), _snap(T0, {"101": _listed(x, STOPS[:-1])}), _snap(T0 + 600, {})]
    x.obs = [(1, snaps[1].trips["101"])]
    assert check_h1(x, snaps, {})["outcome"] == "ok"
    x.obs = [(1, _listed(x, STOPS[:1]))]
    assert check_h1(x, snaps, {})["outcome"] == "failed"
    x.obs = []
    assert check_h1(x, snaps, {}) is None  # not seen in the window: not judged


def test_h2_wants_a_suffix_of_the_schedule_up_to_the_horizon():
    x = _trip(dep0=T0)  # stops a..d at T0 + 0, 300, 600, 900; d is the terminus
    lists = [STOPS[:-1], STOPS[1:-1], ["c"], ["a", "c"], ["b", "a", "c"], ["d"]]
    snaps = [_snap(T0 + k, {"101": _listed(x, s)}) for k, s in enumerate(lists)]
    x.obs = [(k, s.trips["101"]) for k, s in enumerate(snaps)]
    rows = check_h2(x, snaps, {})
    assert [r["outcome"] for r in rows] == ["ok", "ok", "ok", "failed", "failed", "failed"]
    assert "missing b" in rows[3]["detail"]
    # beyond the 90 min horizon a stop needn't be listed
    far = Trip("2026-11-09", "101", STOPS, dict(zip(STOPS, (T0 + 600, T0 + 3000, T0 + HORIZON_S + 600, T0 + HORIZON_S + 900))),
               T0 + 600)  # c is beyond the horizon
    snaps = [_snap(T0, {"101": _listed(far, ["a", "b"])}), _snap(T0 + 60, {"101": _listed(far, ["a"])})]
    far.obs = [(0, snaps[0].trips["101"]), (1, snaps[1].trips["101"])]
    assert [r["outcome"] for r in check_h2(far, snaps, {})] == ["ok", "failed"]


def test_h2_judges_each_content_once():
    x = _trip(dep0=T0)
    snaps = [_snap(T0, {"101": _listed(x, ["b", "a"])}), _snap(T0 + 60, {"101": _listed(x, ["b", "a"])}, first=False)]
    x.obs = [(0, snaps[0].trips["101"]), (1, snaps[1].trips["101"])]
    assert len(check_h2(x, snaps, {})) == 1
