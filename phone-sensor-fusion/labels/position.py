"""Labelled position along the line, per second: each leg's labelled speed integrated from
its origin station (labels/build_speed.py scales every leg to the map distance, so it
arrives exactly at the next one). nan off the legs and where the leg has no speed label.
For exploration and scoring only."""

import numpy as np

from labels import load
from labels.frames import frame_labels


def position(rid: str) -> tuple[np.ndarray, int]:
    """(s_m per second [T], direction: +1 southbound (s increasing), -1 northbound)."""
    lab = frame_labels(rid)
    stops, s_of = load.stops(rid), load.station_s()
    direction = 1 if load.journey(rid)["direction"] == "south" else -1
    pos = np.full(len(lab.v), np.nan)
    for st in stops:  # standing at a platform
        a = st.t_still
        b = st.t_leave if np.isfinite(st.t_leave) else st.t_open_end
        pos[int(a):int(np.ceil(b))] = s_of[st.station]
    for p, q in zip(stops[:-1], stops[1:]):
        m = np.flatnonzero(lab.leg == f"{p.station} > {q.station}")
        if len(m) and lab.confident[m].mean() > 0.3:  # a leg with a usable speed label
            pos[m] = s_of[p.station] + direction * np.cumsum(lab.v[m])
    return pos, direction
