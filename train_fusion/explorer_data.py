"""Tables served to the explorer (web/explorer), read from what the pipeline wrote to
data/derived. web/explorer/src/data/[table].parquet.py serves TABLES[<table>]() as
data/<table>.parquet; web/explorer/src/data/meta.json.py serves meta()."""
from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl

from train_fusion.common import paths
from train_fusion.common.timeutil import service_day_origin
from train_fusion.stages.decode.schemas import FETCHES, TU, VP
from train_fusion.stages.linearize.schemas import VP_LIN
from train_fusion.stages.reference.stage import FAR_M
from train_fusion.stages.static.stage import DAYS, Static
from train_fusion.stages.timetable import stage as timetable_stage


def _static() -> Static:
    """The static feed of the newest built date."""
    dates = paths.built_dates()
    if not dates:
        raise SystemExit("nothing built: run `just build <dates>` first")
    return Static(dates[-1])


def meta() -> dict:
    """Built dates, the static feed version, the reference's far-fix threshold, and each
    date's service day as [start, end] epochs (03:00 to 03:00 local, see fetch.py) and when
    it was built."""
    dates = paths.built_dates()
    windows = {d: [service_day_origin(d) + 3 * 3600, service_day_origin(d) + 27 * 3600] for d in dates}
    return {"dates": dates, "static_version": _static().version, "far_m": FAR_M, "day_windows": windows,
            "built": {d: built_epoch(d) for d in dates}}


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
# beyond it (normally 60 s apart) there's a gap in the archive.
FETCH_COVERS_S = 120.0
# Breaks in a trip's presence in VP / TU longer than this split it into separate runs.
RUN_BREAK_S = 150.0


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


def fetch_spans() -> pl.DataFrame:
    """(date, feed, start_epoch, end_epoch, kind): every fetch, see _fetches."""
    return pl.concat(_fetches(d).select("date", "feed", "start_epoch", "end_epoch", "kind")
                     for d in paths.built_dates())


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


def _length(xs: Spans) -> float:
    return sum(b - a for a, b in xs)


def built_epoch(d: str) -> float:
    """When the date was last built (its fetches.parquet was written): what "elapsed" means."""
    return (paths.day_dir(d) / "fetches.parquet").stat().st_mtime


FUNNEL_FEEDS = {"vehiclepositions": (VP, "vp"), "tripupdates": (TU, "tu")}
COVER_BINS = 10


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


def funnel() -> pl.DataFrame:
    """See _funnel."""
    return _funnel()[0]


def funnel_pieces() -> pl.DataFrame:
    """See _funnel."""
    return _funnel()[1]


def _funnel() -> tuple[pl.DataFrame, pl.DataFrame]:
    """How much survives each step from service hours to complete trips, per feed, as
    (date, feed, stage, segment, value, n) totals and, for drawing the steps in time order,
    (date, feed, stage, segment, x0, x1, start_epoch, end_epoch, label) pieces.

    Totals, by stage:
    - service: elapsed service time (union of the scheduled runs of the date's trips, up to
      when the date was built), value in s, n = scheduled trips with an elapsed run;
    - fetches: that time by the kind of fetch standing for it ("success" = exact or inexact,
      "empty", "failed", "no fetch"), value in s, n = fetches overlapping it;
    - trips: scheduled trips whose elapsed run overlaps success / empty fetches, "seen" (at least
      one observation in the feed that date) or "not seen"; plus "no fetch during run" (trips
      with an elapsed run but no success / empty fetch during it, not part of the funnel);
    - coverage: seen trips by the share of their elapsed run's success / empty fetch time in
      which the fetched snapshot had them, in COVER_BINS bins ("0", "1", ..., "9": bin k is
      [k/10, (k+1)/10), 100% in bin 9).

    Pieces, in time order, by stage: "service": the elapsed service spans; "fetches": runs of
    one fetch result (x in s of elapsed service time, so time with no trip scheduled is left
    out); "trips": every trip with an elapsed run (x: its scheduled run, up to the build, on the
    same axis), as "seen" (with its coverage share), "not seen" or "no fetch during run",
    with its route and direction."""
    rows, pieces = [], []
    for d in paths.built_dates():
        st, origin, built = Static(d), service_day_origin(d), built_epoch(d)
        runs = {}
        for trip in st.active_trip_ids:
            stops = st.stops_by_trip.get(trip)
            if stops is not None:
                lo, hi = origin + stops["sched_s"].min(), min(origin + stops["sched_s"].max(), built)
                if lo < hi:
                    runs[trip] = [(lo, hi)]
        service = _union(r[0] for r in runs.values())
        fetches = _fetches(d)
        for feed, (table, name) in FUNNEL_FEEDS.items():
            f = fetches.filter(pl.col("feed") == feed)
            obs = table.read(paths.day_dir(d) / f"{name}.parquet")
            # which trips each fetched snapshot had (duplicates: the kept fetch with the same content)
            trips_in_file = {k[0]: set(g["trip_id"]) for k, g in obs.group_by("file")}
            kept = {sha: file for sha, file in f.filter(pl.col("ok") & ~pl.col("duplicate"))
                    .select("content_sha256", "file").iter_rows()}
            by_kind = {"success": [], "empty": [], "failed": []}
            n_kind = {k: 0 for k in by_kind}  # fetches overlapping elapsed service time
            had = {}  # trip -> spans of fetches whose snapshot had it
            for kind, a, b, sha in f.select("kind", "start_epoch", "end_epoch", "content_sha256").iter_rows():
                k = "success" if kind in ("exact", "inexact") else kind
                by_kind[k].append((a, b))
                n_kind[k] += bool(_intersect([(a, b)], service))
                if k == "success":
                    for trip in trips_in_file.get(kept.get(sha), ()):
                        had.setdefault(trip, []).append((a, b))
            spans = {k: _intersect(_union(v), service) for k, v in by_kind.items()}
            # a failed fetch's time isn't covered by a later good one (spans don't overlap per feed)
            fetched = _union(spans["success"] + spans["empty"])
            no_fetch = _length(service) - _length(fetched) - _length(spans["failed"])
            base = {"date": d, "feed": feed}
            rows.append({**base, "stage": "service", "segment": "elapsed", "value": _length(service), "n": len(runs)})
            for k in ("success", "empty", "failed"):
                rows.append({**base, "stage": "fetches", "segment": k, "value": _length(spans[k]), "n": n_kind[k]})
            rows.append({**base, "stage": "fetches", "segment": "no fetch", "value": max(no_fetch, 0.0), "n": 0})
            seen_in_feed = set(obs["trip_id"])
            counts = {"seen": 0, "not seen": 0, "no fetch during run": 0}
            bins = [0] * COVER_BINS
            trip_kind = {}  # trip -> "seen" / "not seen" (trips with a fetched run only)
            trip_share = {}  # seen trip -> share of its fetched run it's in
            for trip, run in runs.items():
                denom = _length(_intersect(fetched, run))
                if denom <= 0:
                    counts["no fetch during run"] += 1
                    continue
                trip_kind[trip] = "seen" if trip in seen_in_feed else "not seen"
                counts[trip_kind[trip]] += 1
                if trip not in seen_in_feed:
                    continue
                frac = _length(_intersect(_union(had.get(trip, [])), run)) / denom
                trip_share[trip] = frac
                bins[min(int(frac * COVER_BINS), COVER_BINS - 1)] += 1
            for k, v in counts.items():
                rows.append({**base, "stage": "trips", "segment": k, "value": float(v), "n": v})
            for k, v in enumerate(bins):
                rows.append({**base, "stage": "coverage", "segment": str(k), "value": float(v), "n": v})
            # pieces: service spans and fetch results along elapsed service time, then the trips
            x = 0.0
            for a, b in service:
                pieces.append({**base, "stage": "service", "segment": "elapsed", "x0": x, "x1": x + b - a,
                               "start_epoch": a, "end_epoch": b, "label": None, "share": None,
                               "route_id": None, "direction_id": None})
                parts = [(c, e, k) for k in ("success", "empty", "failed") for c, e in _intersect(spans[k], [(a, b)])]
                parts += [(c, e, "no fetch") for c, e in _subtract([(a, b)], _union((c, e) for c, e, _ in parts))]
                for c, e, k in sorted(parts):
                    prev = pieces[-1]
                    if prev["stage"] == "fetches" and prev["segment"] == k and abs(prev["x1"] - (x + c - a)) < 1e-6:
                        prev.update(x1=x + e - a, end_epoch=e)
                    else:
                        pieces.append({**base, "stage": "fetches", "segment": k, "x0": x + c - a, "x1": x + e - a,
                                       "start_epoch": c, "end_epoch": e, "label": None, "share": None,
                                       "route_id": None, "direction_id": None})
                x += b - a
            def on_axis(t: float) -> float:  # epoch -> s of elapsed service time before it
                return sum(max(0.0, min(b, t) - a) for a, b in service)

            info = {r["trip_id"]: r for r in st.trips.select("trip_id", "route_id", "direction_id").iter_rows(named=True)}
            for trip in sorted(runs, key=lambda t: runs[t][0]):
                (lo, hi), = runs[trip]
                pieces.append({**base, "stage": "trips", "segment": trip_kind.get(trip, "no fetch during run"),
                               "x0": on_axis(lo), "x1": on_axis(hi), "start_epoch": lo, "end_epoch": hi,
                               "label": trip, "share": trip_share.get(trip),
                               "route_id": info[trip]["route_id"], "direction_id": info[trip]["direction_id"]})
    return pl.DataFrame(rows), pl.DataFrame(pieces, schema_overrides={"label": pl.String, "share": pl.Float64,
                                                                "route_id": pl.String, "direction_id": pl.Int64})


def _runs(t: np.ndarray) -> list[tuple[float, float]]:
    """Sorted times -> [(start, end)] runs, split where consecutive times are > RUN_BREAK_S apart."""
    cuts = np.flatnonzero(np.diff(t) > RUN_BREAK_S)
    return list(zip(t[np.r_[0, cuts + 1]], t[np.r_[cuts, len(t) - 1]]))


def _covered(spans: list[tuple[float, float]], lo: float, hi: float) -> float:
    """Fraction of [lo, hi] covered by (disjoint) spans."""
    return sum(max(0.0, min(b, hi) - max(a, lo)) for a, b in spans) / max(hi - lo, 1.0)


def _max_gap(t: np.ndarray) -> float | None:
    return float(np.diff(t).max()) if len(t) > 1 else None


def rt_trips() -> pl.DataFrame:
    """One row per (date, trip_id) that was scheduled on the date or showed up in its VP / TU,
    with how much of it the RT feeds covered:
    - sched_*: scheduled first departure / last arrival; *_fetch_frac: share of that span the
      feed's successful fetches cover (null for unscheduled trips);
    - vp_*: fixes, share of the trip's route between its first and last stop the fixes span,
      longest gap between fixes, fixes more than FAR_M from the shape;
    - tu_*: snapshots it's in, share of its scheduled stops ever predicted, predicted stops
      not in its schedule, first appearance before the scheduled departure, longest gap."""
    rows = []
    spans = fetch_spans().filter(pl.col("kind") != "failed")
    for d in paths.built_dates():
        st, origin = Static(d), service_day_origin(d)
        feed_spans = {feed: list(g.select("start_epoch", "end_epoch").iter_rows())
                      for (feed,), g in spans.filter(pl.col("date") == d).group_by("feed")}
        vl = VP_LIN.read(paths.day_dir(d) / "vp_lin.parquet")
        tu = TU.read(paths.day_dir(d) / "tu.parquet")
        vp_by = {k[0]: g.sort("t_epoch") for k, g in vl.group_by("trip_id")}
        tu_by = {k[0]: g for k, g in tu.group_by("trip_id")}
        trips = {r["trip_id"]: r for r in st.trips.iter_rows(named=True)}
        active = st.active_trip_ids
        for trip in sorted(active | set(vp_by) | set(tu_by)):
            info = trips.get(trip, {})
            stops = st.stops_by_trip.get(trip)
            r = {"date": d, "trip_id": trip, "scheduled": trip in active,
                 "route_id": info.get("route_id"), "direction_id": info.get("direction_id"),
                 "sched_start_epoch": None, "sched_end_epoch": None, "vp_fetch_frac": None, "tu_fetch_frac": None,
                 "vp_fixes": 0, "vp_route_frac": None, "vp_max_gap_s": None, "vp_far_fixes": 0,
                 "tu_snapshots": 0, "tu_stops_frac": None, "tu_extra_stops": 0, "tu_lead_s": None,
                 "tu_max_gap_s": None}
            if stops is not None:
                lo, hi = origin + stops["sched_s"].min(), origin + stops["sched_s"].max()
                r.update(sched_start_epoch=lo, sched_end_epoch=hi,
                         vp_fetch_frac=_covered(feed_spans.get("vehiclepositions", []), lo, hi),
                         tu_fetch_frac=_covered(feed_spans.get("tripupdates", []), lo, hi))
            if (v := vp_by.get(trip)) is not None:
                t = v["t_epoch"].to_numpy()
                r.update(vp_fixes=len(v), vp_max_gap_s=_max_gap(t),
                         vp_far_fixes=int((v["offset_m"] > FAR_M).sum()))
                if stops is not None:
                    s0, s1 = stops["s_m"].min(), stops["s_m"].max()
                    near = v.filter(pl.col("offset_m") <= FAR_M)["s_m"].clip(s0, s1)
                    r["vp_route_frac"] = (near.max() - near.min()) / (s1 - s0) if len(near) and s1 > s0 else 0.0
            if (u := tu_by.get(trip)) is not None:
                t = np.unique(u["t_epoch"].to_numpy())
                seen = set(u["stop_id"])
                r.update(tu_snapshots=len(t), tu_max_gap_s=_max_gap(t))
                if stops is not None:
                    planned = set(stops["stop_id"])
                    r.update(tu_stops_frac=len(seen & planned) / len(planned), tu_extra_stops=len(seen - planned),
                             tu_lead_s=r["sched_start_epoch"] - t[0])
            rows.append(r)
    return pl.DataFrame(rows, infer_schema_length=None)


def rt_runs() -> pl.DataFrame:
    """(date, trip_id, feed, start_epoch, end_epoch): stretches of time a trip is in a feed,
    split where it's missing for more than RUN_BREAK_S."""
    rows = []
    for d in paths.built_dates():
        for feed, df in (("vehiclepositions", VP_LIN.read(paths.day_dir(d) / "vp_lin.parquet")),
                         ("tripupdates", TU.read(paths.day_dir(d) / "tu.parquet"))):
            for (trip,), g in df.group_by("trip_id"):
                for a, b in _runs(np.unique(g["t_epoch"].to_numpy())):
                    rows.append({"date": d, "trip_id": trip, "feed": feed, "start_epoch": a, "end_epoch": b})
    return pl.DataFrame(rows)


def vp_offsets() -> pl.DataFrame:
    """(date, trip_id, offset_m): every VP fix's distance from its trip's shape (linearize)."""
    return pl.concat(VP_LIN.read(paths.day_dir(d) / "vp_lin.parquet").select(pl.lit(d).alias("date"), "trip_id", "offset_m")
                     for d in paths.built_dates())


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
    "fetch_spans": fetch_spans,
    "funnel": funnel,
    "funnel_pieces": funnel_pieces,
    "rt_trips": rt_trips,
    "rt_runs": rt_runs,
    "vp_offsets": vp_offsets,
}
