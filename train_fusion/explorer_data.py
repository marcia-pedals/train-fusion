"""Tables served to the explorer (web/explorer), read from what the pipeline wrote to
data/derived. web/explorer/src/data/[table].parquet.py serves TABLES[<table>]() as
data/<table>.parquet; web/explorer/src/data/meta.json.py serves meta()."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import polars as pl

from train_fusion.common import paths
from train_fusion.common.timeutil import TZ, service_day_origin
from train_fusion.stages.decode.schemas import FETCHES
from train_fusion.stages.static.stage import DAYS, Static
from train_fusion.stages.timetable import stage as timetable_stage
from train_fusion.stages.tu_checks.schemas import TU_CHECKS


def _static() -> Static:
    """The static feed of the newest built date."""
    dates = paths.built_dates()
    if not dates:
        raise SystemExit("nothing built: run `just build <dates>` first")
    return Static(dates[-1])


def meta() -> dict:
    """Built dates and the static feed version."""
    return {"dates": paths.built_dates(), "static_version": _static().version}


def date_trips() -> pl.DataFrame:
    """(date, trip_id): the trips that run on each built date (static calendar.txt and
    calendar_dates.txt, the same rule the pipeline uses)."""
    rows = [{"date": d, "trip_id": t} for d in paths.built_dates() for t in sorted(Static(d).active_trip_ids)]
    return pl.DataFrame(rows, schema={"date": pl.String, "trip_id": pl.String})


def _dates_text(isos: list[str]) -> str:
    """'2026-11-27', ... -> 'Nov 27, Dec 24, Jan 27 2027' (year only when not the first's)."""
    ds = sorted(date.fromisoformat(d) for d in isos)
    return ", ".join(d.strftime("%b %-d") + ("" if d.year == ds[0].year else f" {d.year}") for d in ds)


def _days_text(row: dict) -> str:
    """calendar weekday flags -> 'Mon–Fri', 'Sat–Sun', 'Mon, Wed', ..."""
    on = [i for i, d in enumerate(DAYS) if row[d]]
    if len(on) >= 2 and on == list(range(on[0], on[-1] + 1)):
        return f"{DAYS[on[0]][:3].title()}–{DAYS[on[-1]][:3].title()}"
    return ", ".join(DAYS[i][:3].title() for i in on)


def services() -> pl.DataFrame:
    """(service_id, runs): when each service of the static feed runs, in words, from the
    static stage's calendar (weekdays) and calendar_dates (added / removed dates)."""
    st = _static()
    cal = {r["service_id"]: r for r in st.calendar.iter_rows(named=True)}
    added, removed = {}, {}
    for sid, d, add in st.calendar_dates.select("service_id", "date", "added").iter_rows():
        (added if add else removed).setdefault(sid, []).append(d)
    rows = []
    for sid in sorted(set(cal) | set(added) | set(removed)):
        parts = [_days_text(cal[sid])] if sid in cal else []
        if sid in added:
            parts.append(("plus " if parts else "") + _dates_text(added[sid]))
        if sid in removed:
            parts.append("except " + _dates_text(removed[sid]))
        rows.append({"service_id": sid, "runs": ", ".join(parts)})
    return pl.DataFrame(rows)


def timetable() -> pl.DataFrame:
    """(trip_id, stop_idx, station_id): each served stop's static GTFS time gtfs_s (whole
    minutes) and, where 511's pre-departure trip updates gave one, its GTFS-RT time rt_s
    (6 s grid; the true time is in [rt_s, rt_s + 6]); and the time since the trip's previous
    stop for each (rt_delta_s only where both stops have an rt_s). Seconds after the
    service-day origin."""
    tt = timetable_stage.load(_static()).sort("trip_id", "stop_idx")
    prev = lambda c: pl.col(c) - pl.col(c).shift(1).over("trip_id")
    return (tt.select("trip_id", "stop_idx", "station_id", pl.col("static_s").cast(pl.Float64).alias("gtfs_s"),
                      pl.when(pl.col("source") == "tu").then(pl.col("dep_lo_s")).alias("rt_s"))
            .with_columns(prev("gtfs_s").alias("gtfs_delta_s"), prev("rt_s").alias("rt_delta_s")))


# ------------------------------------------------------------------ pipeline health
# A fetch stands for the time until the next one of its feed, but for no more than this:
# beyond it there's a gap in the archive. Fetches are normally 60 s apart, 120-123 s when the
# fetcher skips a slot to make room for a parity check (fetchers/gtfs/fetch.py).
FETCH_COVERS_S = 150.0


def _fetches(d: str) -> pl.DataFrame:
    """One date's fetches with the time each stands for (start_epoch, end_epoch: until the next
    fetch of its feed, at most FETCH_COVERS_S) and its kind: "exact" / "inexact" content time
    (inexact: the raw stamp, up to 15 s late), "empty" (a valid feed with no entities, so no
    stamp: no trains running), or "failed" (an HTTP error, network error or unparseable body)."""
    f = FETCHES.read(paths.day_dir(d) / "fetches.parquet").sort("feed", "request_epoch")
    return f.with_columns(
        pl.lit(d).alias("date"),
        pl.col("request_epoch").alias("start_epoch"),
        pl.min_horizontal(pl.col("request_epoch").shift(-1).over("feed"),
                          pl.col("request_epoch") + FETCH_COVERS_S)
        .fill_null(pl.col("request_epoch") + FETCH_COVERS_S).alias("end_epoch"),
        pl.when(pl.col("ok")).then(pl.when(pl.col("content_exact")).then(pl.lit("exact")).otherwise(pl.lit("inexact")))
        .when((pl.col("status") == 200) & pl.col("content_sha256").is_not_null() & (pl.col("n_entities") == 0))
        .then(pl.lit("empty")).otherwise(pl.lit("failed")).alias("kind"),
    )


# ---- interval lists: sorted, disjoint [(start, end)]
Spans = list[tuple[float, float]]


def _union(spans) -> Spans:
    out: Spans = []
    for a, b in sorted(spans):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _intersect(xs: Spans, ys: Spans) -> Spans:
    out, i, j = [], 0, 0
    while i < len(xs) and j < len(ys):
        a, b = max(xs[i][0], ys[j][0]), min(xs[i][1], ys[j][1])
        if a < b:
            out.append((a, b))
        if xs[i][1] < ys[j][1]:
            i += 1
        else:
            j += 1
    return out


def _subtract(xs: Spans, ys: Spans) -> Spans:
    """xs minus ys."""
    out = []
    for a, b in xs:
        for c, e in ys:
            if e <= a or c >= b:
                continue
            if c > a:
                out.append((a, c))
            a = max(a, e)
        if a < b:
            out.append((a, b))
    return out


def built_epoch(d: str) -> float:
    """When the date was last built (its fetches.parquet was written): what "elapsed" means."""
    return (paths.day_dir(d) / "fetches.parquet").stat().st_mtime


FEEDS = ["vehiclepositions", "tripupdates"]


def _elapsed_runs(d: str, st: Static) -> tuple[dict[str, Spans], Spans]:
    """Each of the date's scheduled trips' run (first to last scheduled stop, up to when the
    date was built; trips not started by then left out), and their union: elapsed service time."""
    origin, built = service_day_origin(d), built_epoch(d)
    runs = {}
    for trip in st.active_trip_ids:
        stops = st.stops_by_trip.get(trip)
        if stops is not None:
            lo, hi = origin + stops["sched_s"].min(), min(origin + stops["sched_s"].max(), built)
            if lo < hi:
                runs[trip] = [(lo, hi)]
    return runs, _union(r[0] for r in runs.values())


FETCH_STATUSES = ["fresh", "unknown stamp", "failed", "missing"]
STATUS_SCHEMA = {"date": pl.String, "feed": pl.String, "status": pl.String, "start_epoch": pl.Float64,
                 "end_epoch": pl.Float64, "start_s": pl.Float64, "end_s": pl.Float64}


def _status_rows(d: str, feed: str, parts: list[tuple[float, float, str]]) -> list[dict]:
    """(start, end, status) parts of one date and feed -> rows of STATUS_SCHEMA, adjacent parts of
    one status merged into runs. start_s / end_s: seconds after the service-day origin, to line
    days up."""
    origin = service_day_origin(d)
    merged: list[list] = []
    for a, b, status in sorted(parts):
        if merged and merged[-1][2] == status and a - merged[-1][1] < 1e-6:
            merged[-1][1] = b
        else:
            merged.append([a, b, status])
    return [{"date": d, "feed": feed, "status": status, "start_epoch": a, "end_epoch": b,
             "start_s": a - origin, "end_s": b - origin} for a, b, status in merged]


def fetch_status() -> pl.DataFrame:
    """(date, feed, status, start_epoch, end_epoch, start_s, end_s): elapsed service time (see
    _elapsed_runs) split by the fetch standing for it (see _fetches), merged into runs of one
    status. Statuses: "fresh" (successful, exact content time; or an empty feed: no trains, so
    nothing stale), "unknown stamp" (successful, but only the raw stamp, so the content time is
    unknown, up to 15 s earlier), "failed", "missing" (no fetch)."""
    kind_status = {"exact": "fresh", "inexact": "unknown stamp", "empty": "fresh", "failed": "failed"}
    rows = []
    for d in paths.built_dates():
        _, service = _elapsed_runs(d, Static(d))
        fetches = _fetches(d)
        for feed in FEEDS:
            f = fetches.filter(pl.col("feed") == feed)
            parts = []
            for status in FETCH_STATUSES[:-1]:
                kinds = [k for k, v in kind_status.items() if v == status]
                spans = f.filter(pl.col("kind").is_in(kinds)).select("start_epoch", "end_epoch").iter_rows()
                parts += [(a, b, status) for a, b in _intersect(_union(spans), service)]
            parts += [(a, b, "missing") for a, b in _subtract(service, _union((a, b) for a, b, _ in parts))]
            rows += _status_rows(d, feed, parts)
    return pl.DataFrame(rows, schema=STATUS_SCHEMA)


PRESENCE_STATUSES = ["present", "absent as expected", "present unexpectedly", "absent unexpectedly"]


# VP is empty overnight: 511 drops every vehicle from it just after local midnight (seen
# 2026-11-07..10: data with content at 00:00:10, empty by 00:00:28). For this long after
# midnight either is expected.
VP_EMPTIES_WITHIN_S = 30.0


def data_presence() -> pl.DataFrame:
    """(date, feed, status, start_epoch, end_epoch, start_s, end_s): elapsed service time with a
    successful fetch (see fetch_status) split by whether the fetched feed had any data (entities;
    an empty feed has none) against whether it was expected to. That's judged per fetch, by the
    time its content is from (content time; request time for an empty feed, which has no stamp):
    VP is expected empty after local midnight (either within VP_EMPTIES_WITHIN_S of it),
    everything else to have data.
    Statuses: "present" (expected and there), "absent as expected", "present unexpectedly",
    "absent unexpectedly"."""
    state = {(True, True): "present", (False, False): "absent as expected",
             (False, True): "present unexpectedly", (True, False): "absent unexpectedly"}
    rows = []
    for d in paths.built_dates():
        _, service = _elapsed_runs(d, Static(d))
        midnight = datetime.combine(date.fromisoformat(d) + timedelta(days=1), time(0), TZ).timestamp()
        f = (_fetches(d).filter(pl.col("kind").is_in(["exact", "inexact", "empty"]))
             .with_columns((pl.col("kind") != "empty").alias("has_data"),
                           pl.col("content_epoch").fill_null(pl.col("request_epoch")).alias("as_of")))
        for feed in FEEDS:
            vp = feed == "vehiclepositions"
            by_status: dict[str, list] = {}
            for a, b, has_data, as_of in (f.filter(pl.col("feed") == feed)
                                          .select("start_epoch", "end_epoch", "has_data", "as_of").iter_rows()):
                expect = (has_data if midnight <= as_of < midnight + VP_EMPTIES_WITHIN_S else as_of < midnight) if vp else True
                by_status.setdefault(state[(expect, has_data)], []).append((a, b))
            parts = [(a, b, st) for st, spans in by_status.items() for a, b in _intersect(_union(spans), service)]
            rows += _status_rows(d, feed, parts)
    return pl.DataFrame(rows, schema=STATUS_SCHEMA)


def tu_checks() -> pl.DataFrame:
    """Stage tu_checks' per-trip outcomes of H1 and H2 on every built date, plus at_s:
    at_epoch in seconds after the service-day origin, to line days up."""
    parts = []
    for d in paths.built_dates():
        p = paths.day_dir(d) / "tu_checks.parquet"
        if not p.exists():
            raise SystemExit(f"{d} was built before stage tu_checks existed: run `just build` again")
        parts.append(TU_CHECKS.read(p).with_columns((pl.col("at_epoch") - service_day_origin(d)).alias("at_s")))
    return pl.concat(parts)


# what web/explorer/src/data/[table].parquet.py serves, as data/<name>.parquet
TABLES = {
    "trips": lambda: _static().trips,
    "trip_stops": lambda: _static().trip_stops,
    "shapes": lambda: _static().shapes,
    "stations": lambda: _static().stations,
    "shape_stations": lambda: _static().shape_stations,
    "date_trips": date_trips,
    "services": services,
    "platforms": lambda: _static().platforms,
    "timetable": timetable,
    "fetch_status": fetch_status,
    "data_presence": data_presence,
    "tu_checks": tu_checks,
}
