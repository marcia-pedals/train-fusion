"""Track speed limits (track/speed_limits.toml) placed on GTFS shapes.

The TOML lists restrictions below a default limit, each running between two points given
as (station, offset in metres along the line, positive towards San Francisco). On a shape
those become positions s: northbound shapes (direction 0) run towards SF, so an offset
adds to the station's s; southbound shapes subtract it.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from train_fusion.common import paths

MPH = 0.44704  # m/s


@dataclass(frozen=True)
class Point:
    station: str
    offset_m: float


@dataclass(frozen=True)
class Restriction:
    mph: float
    start: Point  # southern end
    end: Point    # northern end


@dataclass(frozen=True)
class SpeedLimits:
    default_mph: float
    restrictions: list[Restriction]


def load_speed_limits(path: Path = paths.SPEED_LIMITS) -> SpeedLimits:
    raw = tomllib.loads(path.read_text())
    pt = lambda d: Point(str(d["station"]), float(d["offset_m"]))
    return SpeedLimits(float(raw["default_mph"]),
                       [Restriction(float(r["mph"]), pt(r["from"]), pt(r["to"])) for r in raw.get("restriction", [])])


@dataclass(frozen=True)
class ShapeLimits:
    """Piecewise-constant speed limit along one shape: mps[i] on [edges[i], edges[i + 1])."""
    edges: np.ndarray
    mps: np.ndarray

    def at(self, s) -> np.ndarray:
        i = np.clip(np.searchsorted(self.edges, s, side="right") - 1, 0, len(self.mps) - 1)
        return self.mps[i]


def on_shape(limits: SpeedLimits, shape_stations: pl.DataFrame, direction_id: int, length_m: float) -> ShapeLimits:
    """Speed limits along a shape, given its projected stations (shape_stations rows of
    that shape) and its direction. Restrictions whose stations aren't on the shape are
    skipped; parts beyond the shape's ends are clipped."""
    pos = dict(shape_stations.select("station_id", "s_m").iter_rows())
    sign = 1.0 if direction_id == 0 else -1.0
    spans = []  # (a, b, mph) on this shape
    for r in limits.restrictions:
        if r.start.station in pos and r.end.station in pos:
            a, b = sorted((pos[r.start.station] + sign * r.start.offset_m, pos[r.end.station] + sign * r.end.offset_m))
            a, b = max(a, 0.0), min(b, length_m)
            if b > a:
                spans.append((a, b, r.mph))
    # cut the shape at every span end; each piece gets the lowest limit covering it
    cuts = np.unique([0.0, length_m, *[x for a, b, _ in spans for x in (a, b)]])
    mid = (cuts[:-1] + cuts[1:]) / 2
    mph = np.full(len(mid), limits.default_mph)
    for a, b, m in spans:
        inside = (mid >= a) & (mid < b)
        mph[inside] = np.minimum(mph[inside], m)
    keep = np.concatenate([[True], mph[1:] != mph[:-1]])  # merge equal neighbours
    return ShapeLimits(np.append(cuts[:-1][keep], length_m), mph[keep] * MPH)
