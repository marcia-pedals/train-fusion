"""Stage `timetable`: the scheduled departure of every (trip, stop) as an interval
[dep_lo_s, dep_hi_s] that contains the true timetable time, in seconds after the GTFS
service-day origin (like trip_stops.sched_s).

- Static GTFS times S are whole minutes, truncated: [S, S + 60].
- 511 trip updates published before the trip starts repeat the timetable on a 6 s grid
  (0.1 min). A value t gives [t, t + 6]: of 1486 values (2026-11-07/08), none was a full
  minute or more after its static time, though rounding to the nearest 6 s would give
  that ~74 times and rounding up ~149 times. So 511 truncates, or its values are the
  timetable's own (exact): either way the true time is in [t, t + 6]. Assumes static and
  511 both derive from the same timetable (not static from 511's values).

Both are expected to describe exactly the same timetable, and that's checked: every
pre-departure trip update (every snapshot, every date) must give the same t for a stop,
and the static time must be t truncated to the minute. Anything else is a
TimetableMismatch (e.g. the timetable changed between the dates being built).

Written per static feed version (data/derived/static/<version>/timetable.parquet), using
the trip updates of the dates being built that use that version.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl

from train_fusion.common import paths
from train_fusion.common.timeutil import service_day_origin
from train_fusion.stages.decode.schemas import TU
from train_fusion.stages.static.stage import Static
from train_fusion.stages.timetable.schemas import TIMETABLE

STATIC_WIDTH_S = 60.0
TU_WIDTH_S = 6.0  # a 511 value t means [t, t + TU_WIDTH_S]
PRE_DEPARTURE_S = 600.0  # only snapshots this long before the trip's scheduled departure


def _shift(day: str, k: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=k)).isoformat()


def tu_schedule(day: str, static: Static) -> pl.DataFrame:
    """Pre-departure TU times of one archive date: (trip_id, stop_id, tu_s) with tu_s in
    seconds after the origin of the trip's own service day."""
    tu = TU.read(paths.day_dir(day) / "tu.parquet")
    ts = static.trip_stops.select("trip_id", "stop_id", "stop_idx", "sched_s")
    dep0 = ts.filter(pl.col("stop_idx") == 0).select("trip_id", pl.col("sched_s").alias("dep0_s"))
    j = (tu.select("trip_id", "stop_id", "t_epoch", pl.coalesce("dep_epoch", "arr_epoch").cast(pl.Float64).alias("x"))
         .drop_nulls("x").join(ts, on=["trip_id", "stop_id"]).join(dep0, on="trip_id"))
    if j.is_empty():
        return pl.DataFrame(schema={"trip_id": pl.String, "stop_id": pl.String, "tu_s": pl.Float64})
    # which service day a value belongs to: the one (of the archive date and its
    # neighbours) that puts it closest to the static time; trips past midnight need this
    origins = np.array([service_day_origin(_shift(day, k)) for k in (-1, 0, 1)])
    x, sched = j["x"].to_numpy(), j["sched_s"].to_numpy()
    k = np.argmin(np.abs(x[:, None] - origins[None, :] - sched[:, None]), axis=1)
    origin = origins[k]
    pre = j["t_epoch"].to_numpy() < origin + j["dep0_s"].to_numpy() - PRE_DEPARTURE_S
    return (j.with_columns(pl.Series("tu_s", x - origin)).filter(pl.Series(pre))
            .select("trip_id", "stop_id", "tu_s").unique())


class TimetableMismatch(ValueError):
    """Static GTFS and 511's trip updates don't describe the same timetable."""


def build(static: Static, days: list[str]) -> pl.DataFrame:
    parts = [tu_schedule(d, static).with_columns(pl.lit(d).alias("date")) for d in days]
    tu = pl.concat(parts) if parts else pl.DataFrame(schema={"trip_id": pl.String, "stop_id": pl.String,
                                                              "tu_s": pl.Float64, "date": pl.String})
    return combine(static.trip_stops, tu)


def combine(trip_stops: pl.DataFrame, tu: pl.DataFrame) -> pl.DataFrame:
    """The timetable from static trip_stops and the pre-departure trip-update values
    (trip_id, stop_id, tu_s, date); raises TimetableMismatch unless they agree exactly."""
    agg = tu.group_by("trip_id", "stop_id").agg(pl.col("tu_s").unique().sort().alias("values"),
                                                pl.col("date").n_unique().alias("n_tu_dates"))
    if len(bad := agg.filter(pl.col("values").list.len() > 1)):
        r = bad.row(0, named=True)
        raise TimetableMismatch(f"trip {r['trip_id']} stop {r['stop_id']}: trip updates give different "
                                f"scheduled times {r['values']} s ({len(bad)} such stops)")
    out = (trip_stops.select("trip_id", "stop_idx", "stop_id", "station_id", "sched_s")
           .join(agg.select("trip_id", "stop_id", pl.col("values").list.first().alias("tu_s"), "n_tu_dates"),
                 on=["trip_id", "stop_id"], how="left")
           .with_columns(pl.col("n_tu_dates").fill_null(0).cast(pl.Int64)))
    truncated = (pl.col("tu_s") // 60 * 60).cast(pl.Int64)
    if len(bad := out.filter(pl.col("tu_s").is_not_null() & (truncated != pl.col("sched_s")))):
        r = bad.row(0, named=True)
        raise TimetableMismatch(f"trip {r['trip_id']} stop {r['stop_id']}: static time {r['sched_s']} s isn't the "
                                f"trip updates' {r['tu_s']:.0f} s truncated to the minute ({len(bad)} such stops)")
    has_tu = pl.col("tu_s").is_not_null()
    return (out.with_columns(pl.when(has_tu).then("tu_s").otherwise(pl.col("sched_s").cast(pl.Float64)).alias("dep_lo_s"),
                             pl.when(has_tu).then(pl.col("tu_s") + TU_WIDTH_S)
                             .otherwise(pl.col("sched_s") + STATIC_WIDTH_S).cast(pl.Float64).alias("dep_hi_s"),
                             pl.when(has_tu).then(pl.lit("tu")).otherwise(pl.lit("static")).alias("source"))
            .rename({"sched_s": "static_s"})
            .select([c.name for c in TIMETABLE.cols])
            .sort("trip_id", "stop_idx"))


def load(static: Static) -> pl.DataFrame:
    """The timetable of a static feed version (written by run())."""
    return TIMETABLE.read(paths.static_dir(static.version) / "timetable.parquet")


def run(days: list[str]) -> dict:
    """Write one timetable per static feed version used by `days`."""
    by_version: dict[str, list[str]] = {}
    for d in days:
        by_version.setdefault(Static(d).version, []).append(d)
    info = {}
    for version, vdays in by_version.items():
        static = Static(vdays[-1])
        tt = build(static, vdays)
        TIMETABLE.write(tt, paths.static_dir(version) / "timetable.parquet")
        info[version] = {"rows": len(tt), "from_tu": int((tt["source"] == "tu").sum())}
    return info
