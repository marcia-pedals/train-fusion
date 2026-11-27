"""Read the label files (labels/README.md describes them)."""

from __future__ import annotations

import csv
import re
import tomllib
from dataclasses import dataclass

import numpy as np

from common import paths


def _f(v: str) -> float:
    return float(v) if v else np.nan


@dataclass
class Stop:
    station: str
    t_arrive: float  # wheels stop (nan = not labelled)
    t_open: float  # door-open chime starts
    t_open_end: float
    t_close: float  # door-close chime starts
    t_close_end: float
    t_depart: float  # wheels start

    # A stop where the train stood but this car's doors stayed shut (only some cars open
    # at a short platform) has no chimes, only t_arrive and t_depart.
    @property
    def doors(self) -> bool:
        return bool(np.isfinite(self.t_open) or np.isfinite(self.t_close))  # either chime heard

    @property
    def t_still(self) -> float:
        """From when the train is known to stand: wheels stop, else the door-open chime."""
        return self.t_arrive if np.isfinite(self.t_arrive) or not self.doors else self.t_open

    @property
    def t_leave(self) -> float:
        """Until when it is known to stand: wheels start, else the end of the door-close chime."""
        return self.t_depart if np.isfinite(self.t_depart) or not self.doors else self.t_close_end


def journey(rid: str) -> dict:
    return tomllib.loads((paths.LABELS / rid / "journey.toml").read_text())


def stops(rid: str) -> list[Stop]:
    rows = csv.DictReader((paths.LABELS / rid / "stops.csv").open())
    return [Stop(r["station"], *(_f(r[k]) for k in
                 ("t_arrive", "t_open", "t_open_end", "t_close", "t_close_end", "t_depart")))
            for r in rows]


def phone(rid: str) -> list[tuple[float, float, str]]:
    rows = csv.DictReader((paths.LABELS / rid / "phone.csv").open())
    return [(float(r["t0"]), float(r["t1"]), r["state"]) for r in rows]


def presence(rid: str) -> list[tuple[float, float, str]]:
    """Intervals of where the phone's owner is: "train", "platform" (or any other place off
    the train), "unknown" (boarding / alighting somewhere in the interval)."""
    rows = csv.DictReader((paths.LABELS / rid / "presence.csv").open())
    return [(float(r["t0"]), float(r["t1"]), r["where"]) for r in rows]


def modes(rid: str) -> list[tuple[float, float, str]]:
    """Optional modes.csv: the train's mode over intervals that stops.csv (one opening and one
    closing per station) can't describe, e.g. doors reopening during a long hold. `mode` is a
    labels.frames.MODE_KEYS name or "unknown"; these override what stops.csv implies."""
    path = paths.LABELS / rid / "modes.csv"
    if not path.exists():
        return []
    return [(float(r["t0"]), float(r["t1"]), r["mode"]) for r in csv.DictReader(path.open())]


def table(rid: str, name: str) -> dict[str, np.ndarray]:
    """A numeric/str CSV in <labels>/<id>/ as columns."""
    rows = list(csv.DictReader((paths.LABELS / rid / name).open()))
    out = {}
    for k in rows[0]:
        col = [r[k] for r in rows]
        try:
            out[k] = np.array([float(v) for v in col])
        except ValueError:
            out[k] = np.array(col)
    return out


def station_s() -> dict[str, float]:
    """Station -> distance along the line (m), from the map."""
    rows = csv.DictReader((paths.HERE / "maps" / "data" / "stations.csv").open())
    return {r["station"]: float(r["s_m"]) for r in rows}


def ride_names() -> dict[str, str]:
    """Recording id -> the letter the docs call that ride (RIDES.md in the labels; rides
    not in it yet are left out)."""
    path = paths.LABELS / "RIDES.md"
    if not path.exists():
        return {}
    rows = re.findall(r"^\|\s*([A-Z]+)\s*\|\s*(\d{8}T\d{6}Z)\s*\|", path.read_text(), re.M)
    return {rid: name for name, rid in rows}
