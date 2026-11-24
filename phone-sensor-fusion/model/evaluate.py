"""Score an inference result against the labels and the map.

The headline: for every leg between two consecutive door openings that the model found,
the distance it says the train travelled (the integral of its posterior mean speed)
against the map distance between the two stations the labels say those were.
"""

from __future__ import annotations

import numpy as np

from labels import load
from labels.frames import MOVING, OPEN_CHIME, UNKNOWN, FrameLabels
from model.infer import Result

MATCH_S = 5.0  # a detected door-open chime is a labelled one if it starts within this


def _onsets(mask: np.ndarray) -> np.ndarray:
    return np.flatnonzero(mask & ~np.r_[False, mask[:-1]])


def evaluate(rid: str, res: Result, lab: FrameLabels) -> dict:
    stops, s_of = load.stops(rid), load.station_s()
    found = _onsets(res.path_mode == OPEN_CHIME).astype(float)
    truth = np.array([st.t_open for st in stops])
    match = {}  # labelled stop index -> detected onset
    for i, t in enumerate(truth):
        d = np.abs(found - t)
        if len(d) and d.min() <= MATCH_S:
            match[i] = float(found[np.argmin(d)])
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


def summary_line(name: str, e: dict) -> str:
    return (f"{name:34s} stops {e['stops_found']:2d}/{e['stops_labelled']:2d} (+{e['stops_spurious']})"
            f"  leg distance error: mean |{e['leg_err_mean_abs']:6.1%}| max |{e['leg_err_max_abs']:6.1%}|"
            f" bias {e['leg_err_bias']:+6.1%}  online |{e['leg_err_mean_abs_filter']:6.1%}|"
            f"  speed rmse {e['speed_rmse']:5.2f} (online {e['speed_rmse_filter']:5.2f}) m/s"
            f"  modes {e['mode_accuracy']:.1%}")
