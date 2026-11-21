"""The labels as per-frame arrays (frame n = recording time [n, n+1) s, judged at its
middle), which is what the feature and model fitting code consume."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from common.recording import Recording
from labels import load

# train modes, in the order the model uses them
MOVING, PRE, OPEN_CHIME, OPEN, CLOSE_CHIME, POST = range(6)
MODE_NAMES = ["moving", "stopped, doors not yet open", "door-open chime", "doors open",
              "door-close chime", "stopped, doors closed"]
MODE_KEYS = ["moving", "pre", "open_chime", "open", "close_chime", "post"]  # as written in modes.csv
UNKNOWN = -1
PHONE_STATES = ("resting", "held", "handled")


@dataclass
class FrameLabels:
    v: np.ndarray  # speed m/s, nan where unlabelled
    confident: np.ndarray  # bool: v is trustworthy
    mode: np.ndarray  # the TRAIN's mode: one of the modes above, or UNKNOWN
    on_train: np.ndarray  # the phone's owner: 1 on the train, 0 off it, UNKNOWN (presence.csv)
    phone: np.ndarray  # 0 resting, 1 held, 2 handled (features/phone_state.py)
    leg: np.ndarray  # str: "A > B" on the way from A to B, "" otherwise
    source: np.ndarray  # str: where v comes from: "dwell", "accel", "tone" (labels/build_speed.py), ""


def frame_labels(rid: str) -> FrameLabels:
    T = int(Recording(rid).duration)
    c = np.arange(T) + 0.5
    sp = load.table(rid, "speed.csv")
    idx = sp["t"].astype(int)
    v = np.full(T, np.nan)
    conf = np.zeros(T, bool)
    leg = np.full(T, "", dtype=object)
    source = np.full(T, "", dtype=object)
    v[idx], conf[idx], leg[idx], source[idx] = sp["v_mps"], sp["confident"] > 0, sp["leg"], sp["source"]
    mode = np.full(T, UNKNOWN)
    stops = load.stops(rid)
    for i, st in enumerate(stops):
        closes = np.isfinite(st.t_close)  # not heard at the last stop when the rider walks away
        mode[(c >= st.t_open) & (c < st.t_open_end)] = OPEN_CHIME
        mode[(c >= st.t_open_end) & (c < (st.t_close if closes else T))] = OPEN
        if closes:
            mode[(c >= st.t_close) & (c < st.t_close_end)] = CLOSE_CHIME
        if np.isfinite(st.t_arrive):
            mode[(c >= st.t_arrive) & (c < st.t_open)] = PRE
        if np.isfinite(st.t_depart):
            mode[(c >= st.t_close_end) & (c < st.t_depart)] = POST
            nxt = stops[i + 1]
            if np.isfinite(nxt.t_arrive):
                mode[(c >= st.t_depart) & (c < nxt.t_arrive)] = MOVING
    # where wheel-stop / wheel-start aren't labelled, only clearly moving frames are known
    mode[(mode == UNKNOWN) & conf & (v > 1.0)] = MOVING
    # stretches stops.csv can't describe (doors opened and closed again during a long hold)
    for a, b, name in load.modes(rid):
        mode[(c >= a) & (c < b)] = UNKNOWN if name == "unknown" else MODE_KEYS.index(name)
    on_train = np.full(T, UNKNOWN)
    for a, b, where in load.presence(rid):
        on_train[(c >= a) & (c < b)] = UNKNOWN if where == "unknown" else int(where == "train")  # any other place: off
    # Off the train, the train's speed and mode are none of the phone's business (and
    # unknowable once it has left); only the door chimes heard from the platform stay.
    off = on_train == 0
    conf[off] = False
    mode[off & (mode != OPEN_CHIME) & (mode != CLOSE_CHIME)] = UNKNOWN
    phone = np.full(T, PHONE_STATES.index("handled"))
    for a, b, state in load.phone(rid):
        phone[(c >= a) & (c < b)] = PHONE_STATES.index(state)
    return FrameLabels(v, conf, mode, on_train, phone, leg, source)
