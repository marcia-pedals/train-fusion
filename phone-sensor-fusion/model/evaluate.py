"""Score an inference result against the labels and the map.

The headline: for every leg between two consecutive door openings that the model found,
the distance it says the train travelled (the integral of its posterior mean speed)
against the map distance between the two stations the labels say those were.
"""

from __future__ import annotations

import numpy as np

from labels import load
from labels.frames import MOVING, OPEN, OPEN_CHIME, UNKNOWN, FrameLabels
from model.infer import Result
from model.states import OFF_MODE

MATCH_S = 5.0  # a detected door-open chime is a labelled one if it starts within this


def _onsets(mask: np.ndarray) -> np.ndarray:
    return np.flatnonzero(mask & ~np.r_[False, mask[:-1]])


def evaluate(rid: str, res: Result, lab: FrameLabels) -> dict:
    stops, s_of = load.stops(rid), load.station_s()
    # a stop = the path entering the door cycle (the door-open chime, or "doors open"), or
    # getting on; it matches a labelled stop if it starts within MATCH_S of its chime or,
    # getting on, before the next stop (the train stands until it leaves: no distance lost)
    door = np.isin(res.path_mode, [OPEN_CHIME, OPEN])
    aboard = np.flatnonzero(res.path_mode != OFF_MODE)
    found = np.union1d(_onsets(door), aboard[:1] if len(aboard) and aboard[0] > 0 else []).astype(float)
    truth = np.array([st.t_open for st in stops])
    match = {}  # labelled stop index -> detected onset
    for i, t in enumerate(truth):
        # boarding happens only at a standing train: before the next stop's chime, it's this stop
        end = stops[1].t_open if i == 0 and len(stops) > 1 and np.isfinite(stops[1].t_open) else t + MATCH_S
        ok = (found >= t - MATCH_S) & (found <= end) if np.isfinite(t) else np.zeros(len(found), bool)
        if ok.any():
            match[i] = float(found[ok][np.argmin(np.abs(found[ok] - t))])
    legs = []
    for i in range(len(stops) - 1):
        if i in match and i + 1 in match:
            a, b = int(match[i]), int(match[i + 1])
            true_m = abs(s_of[stops[i].station] - s_of[stops[i + 1].station])
            legs.append({"leg": f"{stops[i].station} > {stops[i + 1].station}", "map_m": true_m,
                         "model_m": float(res.distance[b] - res.distance[a]),
                         "filter_m": float(res.distance_filt[b] - res.distance_filt[a])})
    err = np.array([l["model_m"] / l["map_m"] - 1 for l in legs])
    errf = np.array([l["filter_m"] / l["map_m"] - 1 for l in legs])
    ok = lab.confident & np.isfinite(lab.v)
    known = lab.mode != UNKNOWN
    # wheel-stop / wheel-start times, where labelled
    moving = res.path_mode == MOVING
    arr_err, dep_err = [], []
    for st in stops:
        if np.isfinite(st.t_arrive) and (~moving).any():
            cand = _onsets(~moving)
            arr_err.append(float(cand[np.argmin(np.abs(cand - st.t_arrive))] - st.t_arrive))
        if np.isfinite(st.t_depart) and moving.any():
            cand = _onsets(moving)
            dep_err.append(float(cand[np.argmin(np.abs(cand - st.t_depart))] - st.t_depart))
    return {
        "legs": legs, "matched_stops": sorted(match),
        # a stop whose door-open chime isn't in the recording (boarded with the doors open) can't be found
        "stops_labelled": int(np.isfinite(truth).sum()), "stops_found": len(match), "stops_spurious": int(len(found) - len(match)),
        "leg_err_mean_abs": float(np.mean(np.abs(err))) if len(err) else np.nan,
        "leg_err_max_abs": float(np.max(np.abs(err))) if len(err) else np.nan,
        "leg_err_bias": float(np.mean(err)) if len(err) else np.nan,
        "leg_err_mean_abs_filter": float(np.mean(np.abs(errf))) if len(errf) else np.nan,
        "total_map_m": float(sum(l["map_m"] for l in legs)),
        "total_model_m": float(sum(l["model_m"] for l in legs)),
        "speed_rmse": float(np.sqrt(np.mean((res.v - lab.v)[ok] ** 2))),
        "speed_rmse_filter": float(np.sqrt(np.mean((res.v_filt - lab.v)[ok] ** 2))),
        "mode_accuracy": float(np.mean(res.path_mode[known] == lab.mode[known])),
        "arrive_err_s": arr_err, "depart_err_s": dep_err,
    }


ZONE_S = 60  # boarding and alighting times don't matter: seconds this close to one aren't scored


def presence(res: Result, lab: FrameLabels, rid: str) -> dict:
    """On or off the train, away from boarding and alighting: seconds where P(on) >= 0.5 says
    the wrong thing (for the live filter, any fixed lags, the smoother), and door openings the
    Viterbi path finds while the rider is off the train (spurious stops before / after the ride)."""
    T = len(lab.on_train)
    c = np.arange(T) + 0.5
    zone = np.zeros(T, bool)
    for a, b, where in load.presence(rid):
        if where == "unknown":
            zone |= (c >= a - ZONE_S) & (c < b + ZONE_S)
    first = load.stops(rid)[0]  # the boarding train's doors, open before boarding
    if np.isfinite(first.t_close_end):
        zone |= (c >= (first.t_open if np.isfinite(first.t_open) else 0)) & (c < first.t_close_end)
    scored = (lab.on_train != UNKNOWN) & ~zone
    on, off = scored & (lab.on_train == 1), scored & (lab.on_train == 0)
    out = {"n_on": int(on.sum()), "n_off": int(off.sum()),
           "stops_off_train": int(np.sum(off & (res.path_mode == OPEN_CHIME) & ~np.r_[False, res.path_mode[:-1] == OPEN_CHIME]))}
    for name, p_on in (("filter", res.p_on_filt), *((f"lag{L}", q) for L, q in res.p_on_lag.items()), ("smooth", res.p_on)):
        if p_on is not None:
            out[name] = {"off_called_on": int(np.sum(off & (p_on >= 0.5))), "on_called_off": int(np.sum(on & (p_on < 0.5)))}
    return out


def summary_line(name: str, e: dict) -> str:
    return (f"{name:34s} stops {e['stops_found']:2d}/{e['stops_labelled']:2d} (+{e['stops_spurious']})"
            f"  leg distance error: mean |{e['leg_err_mean_abs']:6.1%}| max |{e['leg_err_max_abs']:6.1%}|"
            f" bias {e['leg_err_bias']:+6.1%}  online |{e['leg_err_mean_abs_filter']:6.1%}|"
            f"  speed rmse {e['speed_rmse']:5.2f} (online {e['speed_rmse_filter']:5.2f}) m/s"
            f"  modes {e['mode_accuracy']:.1%}")
