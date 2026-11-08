"""Tables shaped for the explorer (web/explorer), read from what the pipeline wrote.
The data loaders in web/explorer/src/data/ are one-line wrappers around these functions;
per-date tables cover all built dates (with a `date` column)."""
from __future__ import annotations

import json

import polars as pl

from train_fusion.common import paths
from train_fusion.common.timeutil import service_day_origin
from train_fusion.stages.reference.schemas import (REF_CRUISE, REF_FIT, REF_FIXES, REF_KNOTS, REF_LIMITS,
                                                   REF_PAIRS, REF_SEGMENTS, REF_TRIPS)
from train_fusion.stages.reference.stage import CRUISE_MARGIN_M
from train_fusion.stages.static.stage import Static
from train_fusion.stages.timetable import stage as timetable_stage


def _dates() -> list[str]:
    return sorted(p.name for p in paths.REFERENCE.iterdir() if p.is_dir())


def _per_date(table) -> pl.DataFrame:
    parts = [table.read(paths.REFERENCE / d / f"{table.name}.parquet") for d in _dates()]
    return pl.concat(parts) if parts else table.empty()


def meta() -> dict:
    return {"dates": _dates(), "cruise_margin_m": CRUISE_MARGIN_M,
            **json.loads((paths.REFERENCE / "reference.json").read_text())}


def stations() -> pl.DataFrame:
    return Static(paths.built_dates()[-1]).stations.select("station_id", "name")


def patterns() -> pl.DataFrame:
    """One row per trip pattern: route, direction, end stations, stops and trips."""
    st = Static(paths.built_dates()[-1])
    ends = (st.trip_stops.sort("stop_idx").group_by("trip_id")
            .agg(pl.col("station_id").first().alias("from_station"), pl.col("station_id").last().alias("to_station"),
                 pl.len().alias("n_stops")))
    return (st.trips.join(ends, on="trip_id")
            .group_by("pattern_id")
            .agg(pl.col("route_id").first(), pl.col("direction_id").first(), pl.col("from_station").first(),
                 pl.col("to_station").first(), pl.col("n_stops").first(), pl.len().alias("n_trips"))
            .sort("pattern_id"))


def ref_segments() -> pl.DataFrame:
    return REF_SEGMENTS.read(paths.REFERENCE / "ref_segments.parquet")


def ref_knots() -> pl.DataFrame:
    return REF_KNOTS.read(paths.REFERENCE / "ref_knots.parquet")


def ref_limits() -> pl.DataFrame:
    return REF_LIMITS.read(paths.REFERENCE / "ref_limits.parquet")


def ref_cruise() -> pl.DataFrame:
    return REF_CRUISE.read(paths.REFERENCE / "ref_cruise.parquet")


def ref_fit() -> pl.DataFrame:
    return REF_FIT.read(paths.REFERENCE / "ref_fit.parquet")


def ref_trips() -> pl.DataFrame:
    return _per_date(REF_TRIPS)


def ref_fixes() -> pl.DataFrame:
    return _per_date(REF_FIXES)


def ref_pairs() -> pl.DataFrame:
    return _per_date(REF_PAIRS)


def timetable() -> pl.DataFrame:
    """The timetable intervals of the newest built date's static feed, with route and direction."""
    st = Static(paths.built_dates()[-1])
    return timetable_stage.load(st).join(st.trips.select("trip_id", "route_id", "direction_id"), on="trip_id")


def schedule() -> pl.DataFrame:
    """Scheduled stops of each date's active trips, with route and direction."""
    out = []
    for day in _dates():
        st = Static(day)
        ts = (st.trip_stops.filter(pl.col("trip_id").is_in(list(st.active_trip_ids)))
              .join(st.trips.select("trip_id", "route_id", "direction_id"), on="trip_id"))
        out.append(ts.select(pl.lit(day).alias("date"), "trip_id", "route_id", "direction_id", "stop_idx",
                             "station_id", "s_m",
                             (pl.col("sched_s") + service_day_origin(day)).cast(pl.Float64).alias("t_epoch")))
    return pl.concat(out).sort("date", "trip_id", "stop_idx")


# what web/explorer/src/data/[table].parquet.py serves, as data/<name>.parquet
TABLES = {f.__name__: f for f in (stations, schedule, timetable, patterns, ref_segments, ref_knots, ref_limits, ref_cruise,
                                  ref_fit, ref_trips, ref_fixes, ref_pairs)}
