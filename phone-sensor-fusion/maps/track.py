"""Read the track model that maps/build_track.py wrote to maps/data/."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import cache

import numpy as np
from scipy import ndimage

from common import paths

DATA = paths.HERE / "maps" / "data"
DECLINATION_DEG = 12.9  # magnetic north is this far east of true north on the Peninsula (2026)


@dataclass(frozen=True)
class Track:
    s: np.ndarray  # m along the line from San Francisco, every 10 m
    curvature: np.ndarray  # 1/m at s; positive = turning left when s increases (southbound)
    heading: np.ndarray  # compass degrees of travel when s increases
    stations: tuple[str, ...]  # north to south
    station_s: np.ndarray  # m
    elev_s: np.ndarray = None  # m along the line, every 50 m
    elev: np.ndarray = None  # m above sea level there

    def curvature_at(self, s):
        return np.interp(s, self.s, self.curvature)

    def heading_at(self, s, direction=1):
        """Compass heading of travel (degrees): southbound for direction +1, else northbound."""
        h = np.radians(self.heading)
        east, north = np.interp(s, self.s, np.sin(h)), np.interp(s, self.s, np.cos(h))
        return (np.degrees(np.arctan2(east, north)) + (0 if direction > 0 else 180)) % 360

    def elevation_at(self, s):
        """Track elevation (m): maps/data/elevation.csv (maps/fetch_elevation.py) after a
        350 m median filter, because the bare-earth DEM drops into every road underpass
        that the track bridges."""
        return np.interp(s, self.elev_s, self.elev)


@cache
def load() -> Track:
    prof = np.loadtxt(DATA / "profile.csv", delimiter=",", skiprows=1)
    rows = list(csv.DictReader((DATA / "stations.csv").open()))
    e = np.loadtxt(DATA / "elevation.csv", delimiter=",", skiprows=1)
    return Track(elev_s=e[:, 0], elev=ndimage.median_filter(e[:, 1], 7, mode="nearest"),
                 s=prof[:, 0], curvature=prof[:, 4], heading=prof[:, 3],
                 stations=tuple(r["station"] for r in rows),
                 station_s=np.array([float(r["s_m"]) for r in rows]))
