"""Stage `static`: static GTFS zip -> trips, trip_stops, shapes, stations, shape_stations.
Also the geometry helpers (projecting lat/lon onto a shape) used downstream."""
from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass
from datetime import date as Date
from functools import cached_property
from pathlib import Path

import numpy as np
import polars as pl
import shapely
from pyproj import Transformer

from train_fusion.common.paths import GTFS, static_dir
from train_fusion.common.timeutil import gtfs_seconds
from train_fusion.stages.static.schemas import (CALENDAR, CALENDAR_DATES, EXCLUDED_TRIPS, PLATFORMS, SHAPES,
                                               SHAPE_STATIONS, STATIONS, TRIPS, TRIP_STOPS)

STATION_ON_SHAPE_M = 100.0
# Out of scope for the whole project (corridor is San Francisco <-> Tamien): dropped here,
# so nothing downstream ever sees these trips, their stops or their shapes.
EXCLUDED_ROUTES = {"South County"}
_TO_UTM = Transformer.from_crs(4326, 32610, always_xy=True)  # UTM zone 10N


def to_utm(lat, lon):
    x, y = _TO_UTM.transform(np.asarray(lon, float), np.asarray(lat, float))
    return np.asarray(x), np.asarray(y)


# ------------------------------------------------------------------ choosing the zip
def all_zips() -> list[tuple[str, Path]]:
    out = []
    for d in sorted(GTFS.glob("*/")):
        for z in sorted(d.glob("*.zip")):
            out.append((d.name, z))
    return out


def zip_for_date(day: str) -> Path:
    """Newest zip fetched on or before `day`; the earliest zip if none is that old."""
    zips = all_zips()
    if not zips:
        raise FileNotFoundError(f"no static GTFS under {GTFS}")
    before = [z for d, z in zips if d <= day]
    return before[-1] if before else zips[0][1]


def _read(z: zipfile.ZipFile, name: str) -> list[dict]:
    if name not in z.namelist():
        return []
    return list(csv.DictReader(io.TextIOWrapper(z.open(name), "utf-8-sig")))


def feed_version(zip_path: Path) -> str:
    z = zipfile.ZipFile(zip_path)
    info = _read(z, "feed_info.txt")
    v = info[0].get("feed_version", "").strip() if info else ""
    if not v:
        v = hashlib.sha256(zip_path.read_bytes()).hexdigest()[:12]
    return v.replace("/", "_")


# ------------------------------------------------------------------ geometry
@dataclass
class ShapeGeom:
    shape_id: str
    line: shapely.LineString      # in UTM metres
    cum_utm: np.ndarray           # cumulative UTM length at each vertex
    dist_gtfs: np.ndarray         # shape_dist_traveled at each vertex

    def project(self, lat, lon):
        """(s_m, offset_m) arrays: distance along the shape (GTFS scale) and from it."""
        x, y = to_utm(lat, lon)
        pts = shapely.points(x, y)
        d = shapely.line_locate_point(self.line, pts)
        off = shapely.distance(self.line, pts)
        return np.interp(d, self.cum_utm, self.dist_gtfs), np.asarray(off)

    def point_at(self, s_m):
        """lat/lon at GTFS distance s_m (for drawing)."""
        d = np.interp(s_m, self.dist_gtfs, self.cum_utm)
        p = shapely.line_interpolate_point(self.line, d)
        x, y = shapely.get_x(p), shapely.get_y(p)
        lon, lat = _TO_UTM.transform(x, y, direction="INVERSE")
        return lat, lon


def shape_geoms(shapes: pl.DataFrame) -> dict[str, ShapeGeom]:
    out = {}
    for (sid,), g in shapes.sort("shape_id", "pt_seq").group_by("shape_id", maintain_order=True):
        x, y = to_utm(g["lat"].to_numpy(), g["lon"].to_numpy())
        seg = np.hypot(np.diff(x), np.diff(y))
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        dist = g["s_m"].to_numpy().astype(float)
        if not np.all(np.diff(dist) >= 0) or dist[-1] <= 0:  # fall back to UTM length
            dist = cum
        out[sid] = ShapeGeom(sid, shapely.LineString(np.column_stack([x, y])), cum, dist)
    return out


# ------------------------------------------------------------------ build
def build(zip_path: Path) -> dict[str, pl.DataFrame]:
    z = zipfile.ZipFile(zip_path)
    trips = pl.DataFrame([{k: r[k] for k in ("trip_id", "route_id", "service_id", "direction_id", "shape_id")}
                          for r in _read(z, "trips.txt")]).with_columns(pl.col("direction_id").cast(pl.Int64))
    excluded_trips = trips.filter(pl.col("route_id").is_in(list(EXCLUDED_ROUTES))).select("trip_id", "route_id")
    trips = trips.filter(~pl.col("route_id").is_in(list(EXCLUDED_ROUTES)))
    kept_trips, kept_shapes = set(trips["trip_id"]), set(trips["shape_id"])
    stops = {r["stop_id"]: r for r in _read(z, "stops.txt")}
    stations = pl.DataFrame([{"station_id": r["stop_id"], "name": r["stop_name"],
                              "lat": float(r["stop_lat"]), "lon": float(r["stop_lon"])}
                             for r in stops.values() if r.get("location_type") == "1"])
    rows = []
    for r in _read(z, "stop_times.txt"):
        if r["trip_id"] not in kept_trips:
            continue
        st = stops[r["stop_id"]]
        rows.append({"trip_id": r["trip_id"], "stop_seq": int(r["stop_sequence"]), "stop_id": r["stop_id"],
                     "station_id": st.get("parent_station") or r["stop_id"],
                     "sched_s": gtfs_seconds(r["departure_time"] or r["arrival_time"]),
                     "s_m": float(r["shape_dist_traveled"] or "nan")})
    trip_stops = (pl.DataFrame(rows).sort("trip_id", "stop_seq")
                  .with_columns(pl.int_range(pl.len()).over("trip_id").alias("stop_idx")))
    shapes = pl.DataFrame([{"shape_id": r["shape_id"], "pt_seq": int(r["shape_pt_sequence"]),
                            "lat": float(r["shape_pt_lat"]), "lon": float(r["shape_pt_lon"]),
                            "s_m": float(r["shape_dist_traveled"] or "nan")} for r in _read(z, "shapes.txt")
                           if r["shape_id"] in kept_shapes])

    geoms = shape_geoms(shapes)
    served = trip_stops.join(trips.select("trip_id", "shape_id"), on="trip_id")
    served_pairs = set(zip(served["shape_id"], served["station_id"]))
    ss_rows = []
    for sid, g in geoms.items():
        s, off = g.project(stations["lat"].to_numpy(), stations["lon"].to_numpy())
        for st_id, sm, o in zip(stations["station_id"], s, off):
            if o <= STATION_ON_SHAPE_M:
                ss_rows.append({"shape_id": sid, "station_id": st_id, "s_m": float(sm), "offset_m": float(o),
                                "served_by_any": (sid, st_id) in served_pairs})
    shape_stations = pl.DataFrame(ss_rows).sort("shape_id", "s_m")

    trips = trips.join(trip_patterns(trips, trip_stops), on="trip_id")
    platforms = pl.DataFrame([{"stop_id": r["stop_id"], "station_id": r["parent_station"], "lat": float(r["stop_lat"]),
                               "lon": float(r["stop_lon"])} for r in stops.values() if r.get("parent_station")])
    iso = lambda ymd: f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
    calendar = pl.DataFrame([{"service_id": r["service_id"], **{d: r[d] == "1" for d in DAYS},
                              "start_date": iso(r["start_date"]), "end_date": iso(r["end_date"])}
                             for r in _read(z, "calendar.txt")], schema=CALENDAR.polars_schema())
    calendar_dates = pl.DataFrame([{"service_id": r["service_id"], "date": iso(r["date"]), "added": r["exception_type"] == "1"}
                                   for r in _read(z, "calendar_dates.txt")], schema=CALENDAR_DATES.polars_schema())
    return {"trips": trips, "trip_stops": trip_stops, "shapes": shapes, "stations": stations,
            "shape_stations": shape_stations, "platforms": platforms, "calendar": calendar,
            "calendar_dates": calendar_dates, "excluded_trips": excluded_trips}


def trip_patterns(trips: pl.DataFrame, trip_stops: pl.DataFrame) -> pl.DataFrame:
    """(trip_id, pattern_id). A pattern is a direction, a sequence of served stations and the
    static times relative to the first departure: trips of a pattern are constant offsets
    of each other (at the static timetable's one-minute resolution). Ids are p01, p02, ...
    by direction, then most trips first."""
    key = (trip_stops.sort("trip_id", "stop_idx").group_by("trip_id", maintain_order=True)
           .agg(pl.col("station_id"), pl.col("sched_s") - pl.col("sched_s").first())
           .join(trips.select("trip_id", "direction_id"), on="trip_id")
           .with_columns(pl.concat_str(pl.col("direction_id").cast(pl.String), pl.col("station_id").list.join(">"),
                                       pl.col("sched_s").cast(pl.List(pl.String)).list.join(","), separator="|")
                         .alias("key")))
    ranked = (key.group_by("key").agg(pl.col("direction_id").first(), pl.len().alias("n"), pl.col("trip_id").min())
              .sort("direction_id", "n", "trip_id", descending=[False, True, False])
              .with_columns(pl.format("p{}", pl.int_range(1, pl.len() + 1).cast(pl.String).str.zfill(2)).alias("pattern_id")))
    return key.join(ranked.select("key", "pattern_id"), on="key").select("trip_id", "pattern_id")


TABLES = {"trips": TRIPS, "trip_stops": TRIP_STOPS, "shapes": SHAPES, "stations": STATIONS,
          "shape_stations": SHAPE_STATIONS, "platforms": PLATFORMS, "calendar": CALENDAR,
          "calendar_dates": CALENDAR_DATES, "excluded_trips": EXCLUDED_TRIPS}
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def run(zip_path: Path) -> str:
    """Build and write the static tables for one zip; returns the feed version."""
    v = feed_version(zip_path)
    out = static_dir(v)
    tabs = build(zip_path)
    for name, t in TABLES.items():
        t.write(tabs[name], out / f"{name}.parquet")
    (out / "source.txt").write_text(str(zip_path) + "\n")
    return v


# ------------------------------------------------------------------ loading
class Static:
    """Static tables for one service date, plus calendar and geometry helpers."""

    def __init__(self, day: str):
        self.day = day
        self.zip_path = zip_for_date(day)
        self.version = feed_version(self.zip_path)
        d = static_dir(self.version)
        if not (d / "trips.parquet").exists():
            run(self.zip_path)
        for name, t in TABLES.items():
            setattr(self, name, t.read(d / f"{name}.parquet"))

    @cached_property
    def geoms(self) -> dict[str, ShapeGeom]:
        return shape_geoms(self.shapes)

    @cached_property
    def shape_of_trip(self) -> dict[str, str]:
        return dict(self.trips.select("trip_id", "shape_id").iter_rows())

    @cached_property
    def direction_of_trip(self) -> dict[str, int]:
        return dict(self.trips.select("trip_id", "direction_id").iter_rows())

    @cached_property
    def active_trip_ids(self) -> set[str]:
        day, dow = self.day, DAYS[Date.fromisoformat(self.day).weekday()]
        active = set(self.calendar.filter(pl.col(dow) & (pl.col("start_date") <= day) & (day <= pl.col("end_date")))
                     ["service_id"])
        for sid, added in self.calendar_dates.filter(pl.col("date") == day).select("service_id", "added").iter_rows():
            (active.add if added else active.discard)(sid)
        return set(self.trips.filter(pl.col("service_id").is_in(list(active)))["trip_id"])

    def stops_of(self, trip_id: str) -> pl.DataFrame:
        return self.trip_stops.filter(pl.col("trip_id") == trip_id).sort("stop_idx")

    @cached_property
    def stops_by_trip(self) -> dict[str, pl.DataFrame]:
        return {k[0]: g.sort("stop_idx") for k, g in self.trip_stops.group_by("trip_id")}

    @cached_property
    def stations_by_shape(self) -> dict[str, pl.DataFrame]:
        return {k[0]: g.sort("s_m") for k, g in self.shape_stations.group_by("shape_id")}
