"""Score a test ride's inference against its labels: the rows of model/schemas.py's tables.

The labels split the ride up; the model never sees them. Each labelled stop has a window:
its labelled times (wheels stop, door chimes, wheels start), widened by PAD_S on both
sides. A leg runs from one labelled stop to the next, and its moving window is the time
between the two stops' windows, less any door openings labelled in it (doors reopened during
a hold, modes.csv) and PAD_S around them.

Per leg, filtered and smoothed:
  dist_err       the speed summed from the first stop's door opening to the second's,
                 against the map distance between the two stations;
  p_missed_stop  P(the doors did not start opening anywhere in the end stop's window);
  p_false_stop   P(they started opening at least once within the moving window).
Both are the probability of the model being wrong, computed directly (hmm.p_visit), so
they keep their precision however small they are.
Filtered probabilities are given the measurements up to the end of their window: what a
phone would believe once the window is over. Smoothed ones are given the whole ride.
And live_dist_err: the leg's distance from the speeds smoothed over everything up to its
end stop's door opening, which is what the causal localiser is given for it.

Per ride:
  the causal localiser (stations.localise(causal=True), on speeds smoothed up to each
  opening): its belief, after each door opening
  it is given, about the station and direction of the last real stop (Localisation.now_filt);
  the truth is the last labelled stop's station and the ride's direction (within PAD_S either
  side of a labelled door opening, the stops before and after it both count). Time to fix: from
  leaving the first labelled stop until that belief puts FIX_P on the truth. Lost: after
  that, the seconds it is below LOST_P. Confidently wrong: the seconds its most probable
  station and direction are above FIX_P and not the truth (the app then shows a wrong place
  as "confident"). The belief only changes at door openings for now.
  the localiser's most likely path given the whole ride (stations.localise(),
  Localisation.path): which door openings it takes for stops, all at once, against the
  labelled stops.
"""

from __future__ import annotations

import numpy as np

from labels import load
from labels.frames import MOVING, OPEN, OPEN_CHIME, PHONE_STATES, FrameLabels
from maps import track as trackmod
from model import stations
from model.infer import Result

PAD_S = 5.0  # a labelled stop's window reaches this far beyond its labelled times
FIX_P = 0.9  # the localiser has a fix once it puts this on the true station and direction
LOST_P = 0.5  # ... and has lost it while that is below this

CONSTANTS = {"pad_s": PAD_S, "fix_p": FIX_P, "lost_p": LOST_P}


def _times(st: load.Stop) -> np.ndarray:
    t = np.array([st.t_arrive, st.t_open, st.t_open_end, st.t_close, st.t_close_end, st.t_depart])
    return t[np.isfinite(t)]


def _window(st: load.Stop) -> tuple[int, int]:
    """Frames [t0, t1) of a labelled stop's window."""
    t = _times(st)
    return int(np.floor(t.min() - PAD_S)), int(np.ceil(t.max() + PAD_S))


def _anchor(st: load.Stop) -> float:
    """When the train is at the stop: the door opening, or (not in the recording) the
    earliest labelled time; it stands still in between, so the choice doesn't matter."""
    return st.t_open if np.isfinite(st.t_open) else float(_times(st).min())


def legs(res: Result, lab: FrameLabels, stops: list[load.Stop], key: dict) -> list[dict]:
    s_of = load.station_s()
    T = len(res.v)
    # frames near a labelled door opening (any: a hold can have its own)
    doors = np.isin(lab.mode, (OPEN_CHIME, OPEN))
    pad = int(np.ceil(PAD_S))
    near_doors = np.convolve(doors, np.ones(2 * pad + 1), mode="same") > 0
    rows = []
    for i in range(len(stops) - 1):
        a, b = stops[i], stops[i + 1]
        fa, fb = int(round(_anchor(a))), int(round(_anchor(b)))
        map_m = abs(s_of[b.station] - s_of[a.station])
        stop_w = _window(b)
        move_w = (_window(a)[1], _window(b)[0])
        mw = np.arange(max(move_w[0], 0), min(move_w[1], T))
        move_frames = ~near_doors[np.clip(np.arange(*move_w), 0, T - 1)] if move_w[1] > move_w[0] else None
        phone = PHONE_STATES[np.bincount(lab.phone[mw], minlength=3).argmax()] if len(mw) else PHONE_STATES[0]
        row = {**key, "leg": i + 1, "from_station": a.station, "to_station": b.station, "phone": phone,
               "t_from_s": _anchor(a), "t_to_s": _anchor(b), "map_m": map_m, "duration_s": _anchor(b) - _anchor(a)}
        for name, dist, smoothed in (("filt", res.distance_filt, False), ("smooth", res.distance, True)):
            d = float(dist[fb] - dist[fa])
            row[f"{name}_dist_m"] = d
            row[f"{name}_dist_err"] = d / map_m - 1
            # every labelled stop counts, whether or not its chime is in the recording
            row[f"{name}_p_missed_stop"] = res.p_mode_visit(OPEN_CHIME, *stop_w, smoothed)[1]
            row[f"{name}_p_false_stop"] = (res.p_mode_visit(OPEN_CHIME, *move_w, smoothed, move_frames)[0]
                                           if move_w[1] > move_w[0] else None)
        v = res.v_upto(min(fb, T - 1))
        row["live_dist_m"] = float(v[fa:fb].sum())
        row["live_dist_err"] = row["live_dist_m"] / map_m - 1
        rows.append(_nan_to_none(row))
    return rows


def _p_true(loc: stations.Localisation, stops: list[load.Stop], T: int, wrong: np.ndarray | None = None,
            best: np.ndarray | None = None, best_place: np.ndarray | None = None,
            best_wrong: np.ndarray | None = None) -> np.ndarray:
    """[T] the causal localiser's probability for the true (last station, direction).
    `wrong` ([T] bool), if given, is set where its most probable (station, direction) is
    above FIX_P and not the truth: where the app would show a wrong place as confident.
    `best` ([T] float), if given, gets the probability of that most probable one, and
    `best_place` ([T, 2] int) its station index (maps/track.py) and direction (0 south,
    1 north); -1 before the first opening. `best_wrong` ([T] bool), if given, is set where
    that most probable one is not the truth, however probable."""
    tr = trackmod.load()
    s_of = load.station_s()
    d_true = 0 if s_of[stops[-1].station] > s_of[stops[0].station] else 1  # 0: station index increasing
    switch = np.array([_anchor(st) for st in stops])
    idx = [tr.stations.index(st.station) for st in stops]
    t = np.arange(T) + 0.5
    k = np.searchsorted(switch, t, side="right") - 1  # last labelled stop at t (-1: none yet)
    j = np.searchsorted(loc.t_stop, np.arange(T), side="right") - 1  # last opening given by frame t
    p = np.zeros(T)
    for tt in range(T):
        if j[tt] < 0:
            continue
        now = loc.now_filt[j[tt]]
        # Within PAD_S of a labelled door opening, either side, the stop before it and the
        # stop it opens are both right: the model's own opening may come a little later
        # (it is still waiting) or a little earlier (it heard the chime first).
        right = {k[tt]}
        if k[tt] >= 0 and t[tt] < switch[k[tt]] + PAD_S:
            right.add(k[tt] - 1)
        if k[tt] + 1 < len(stops) and t[tt] >= switch[k[tt] + 1] - PAD_S:
            right.add(k[tt] + 1)
        p[tt] = max((now[idx[i], d_true] for i in right if i >= 0), default=0.0)
        a, d = np.unravel_index(np.argmax(now), now.shape)
        if best is not None:
            best[tt] = now[a, d]
        if best_place is not None:
            best_place[tt] = a, d
        is_truth = d == d_true and any(i >= 0 and idx[i] == a for i in right)
        if wrong is not None:
            wrong[tt] = now[a, d] > FIX_P and not is_truth
        if best_wrong is not None:
            best_wrong[tt] = not is_truth
    return p


def _departure(first: load.Stop, lab: FrameLabels) -> int:
    """When the train leaves the ride's first stop: its labelled wheel start, else the end of
    what is labelled there (the doors closing), or the first labelled moving second if
    that is earlier. (The labels' moving seconds alone can start much later: where a leg's
    wheel times aren't labelled, only confidently fast seconds are called moving.)"""
    if np.isfinite(first.t_depart):
        return int(np.floor(first.t_depart))
    moving = np.flatnonzero(lab.mode == MOVING)
    t = int(np.ceil(_times(first).max()))
    return min(t, int(moving[0])) if len(moving) else t


def ride(res: Result, f: dict, lab: FrameLabels, stops: list[load.Stop], leg_rows: list[dict], key: dict,
         trace: dict | None = None) -> dict:
    """`trace`, if given, gets the causal localiser's belief per second (model/ride_view.py)."""
    T = len(res.v)
    t_move0 = _departure(stops[0], lab)
    t_end = min(int(np.ceil(_times(stops[-1]).max())), T)
    row = {**key, "stops": len(stops), "legs": len(leg_rows), "moving_s": float(t_end - t_move0)}

    loc_c = stations.localise(res, f, causal=True)
    wrong, best, best_place, best_wrong = np.zeros(T, bool), np.zeros(T), np.full((T, 2), -1), np.zeros(T, bool)
    p = _p_true(loc_c, stops, T, wrong, best, best_place, best_wrong)
    if trace is not None:
        trace.update(p_true=p, p_best=best, best_place=best_place, best_wrong=best_wrong, wrong=wrong,
                     openings=loc_c.t_stop)
    row["loc_filt_confident_wrong_s"] = float(np.sum(wrong[t_move0:t_end]))
    fixed = np.flatnonzero(p[t_move0:t_end] >= FIX_P)
    if len(fixed):
        t_fix = t_move0 + int(fixed[0])
        row["loc_filt_time_to_fix_s"] = float(t_fix - t_move0)
        row["loc_filt_lost_s"] = float(np.sum(p[t_fix:t_end] < LOST_P))
    else:
        row["loc_filt_time_to_fix_s"] = row["loc_filt_lost_s"] = None
    row["loc_filt_openings"] = len(loc_c.t_stop)

    loc = stations.localise(res, f)
    row.update(_stop_scores(loc.t_stop[loc.path >= 0], stops))
    row["loc_ml_openings"] = len(loc.t_stop)

    for name in ("filt", "live", "smooth"):
        err = [abs(r[f"{name}_dist_err"]) for r in leg_rows]
        row[f"{name}_dist_err_mean_abs"] = float(np.mean(err)) if err else None
    for name in ("filt", "smooth"):
        row[f"{name}_missed_stops"] = float(sum(r[f"{name}_p_missed_stop"] for r in leg_rows if r[f"{name}_p_missed_stop"] is not None))
        row[f"{name}_false_stops"] = float(sum(r[f"{name}_p_false_stop"] for r in leg_rows if r[f"{name}_p_false_stop"] is not None))
    return row


def _stop_scores(t_real: np.ndarray, stops: list[load.Stop]) -> dict:
    """The localiser's stops (frames of the openings its most likely path takes for stops)
    against the labelled ones. Every labelled stop counts, whether or not the measurements
    could show it."""
    t = t_real + 0.5
    windows = [_window(st) for st in stops]
    inside = [(t >= w0) & (t < w1) for w0, w1 in windows]
    in_any = np.any(inside, axis=0) if len(t) else np.zeros(0, bool)
    between = (t >= windows[0][0]) & (t < windows[-1][1])
    return {"loc_ml_stops_missing": int(sum(not w.any() for w in inside)),
            "loc_ml_stops_spurious": int(sum(max(int(w.sum()) - 1, 0) for w in inside) + np.sum(between & ~in_any))}


def _nan_to_none(row: dict) -> dict:
    return {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in row.items()}


def summary_line(r: dict) -> str:
    fix = "never" if r["loc_filt_time_to_fix_s"] is None else f"{r['loc_filt_time_to_fix_s']:4.0f} s"
    lost = "" if r["loc_filt_lost_s"] is None else f", lost {r['loc_filt_lost_s']:3.0f} s"
    return (f"{r['variant']:28s} leg distance |err| filt {r['filt_dist_err_mean_abs']:6.1%} smooth {r['smooth_dist_err_mean_abs']:6.1%}"
            f"  stops missed {r['filt_missed_stops']:.2f}/{r['smooth_missed_stops']:.2f}"
            f" false {r['filt_false_stops']:.2f}/{r['smooth_false_stops']:.2f} (filt/smooth, expected)"
            f"  localiser: fix after {fix}{lost};"
            f" most likely path: stops missing {r['loc_ml_stops_missing']}/{r['stops']}, spurious {r['loc_ml_stops_spurious']}")
