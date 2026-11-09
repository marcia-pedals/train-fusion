"""Tables served to the explorer (web/explorer), read from what the pipeline wrote to
data/derived. web/explorer/src/data/[table].parquet.py serves TABLES[<table>]() as
data/<table>.parquet; web/explorer/src/data/meta.json.py serves meta()."""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import date

import polars as pl

from train_fusion.common import paths
from train_fusion.stages.static.stage import Static


def _static() -> Static:
    """The static feed of the newest built date."""
    dates = paths.built_dates()
    if not dates:
        raise SystemExit("nothing built: run `just build <dates>` first")
    return Static(dates[-1])


def meta() -> dict:
    return {"dates": paths.built_dates(), "static_version": _static().version}


def date_trips() -> pl.DataFrame:
    """(date, trip_id): the trips that run on each built date (static calendar.txt and
    calendar_dates.txt, the same rule the pipeline uses)."""
    rows = [{"date": d, "trip_id": t} for d in paths.built_dates() for t in sorted(Static(d).active_trip_ids)]
    return pl.DataFrame(rows, schema={"date": pl.String, "trip_id": pl.String})


_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _dates_text(ymds: list[str]) -> str:
    """'20261127', ... -> 'Nov 27, Dec 24, Jan 27 2027' (year only when not the first's)."""
    ds = sorted(date(int(d[:4]), int(d[4:6]), int(d[6:])) for d in ymds)
    return ", ".join(d.strftime("%b %-d") + ("" if d.year == ds[0].year else f" {d.year}") for d in ds)


def _days_text(row: dict) -> str:
    """calendar.txt weekday flags -> 'Mon–Fri', 'Sat–Sun', 'Mon, Wed', ..."""
    on = [i for i, d in enumerate(_DAYS) if row[d] == "1"]
    if len(on) >= 2 and on == list(range(on[0], on[-1] + 1)):
        return f"{_DAYS[on[0]][:3].title()}–{_DAYS[on[-1]][:3].title()}"
    return ", ".join(_DAYS[i][:3].title() for i in on)


def services() -> pl.DataFrame:
    """(service_id, runs): when each service of the static feed runs, in words, from
    calendar.txt (weekdays) and calendar_dates.txt (added / removed dates)."""
    z = zipfile.ZipFile(_static().zip_path)
    read = lambda name: list(csv.DictReader(io.TextIOWrapper(z.open(name), "utf-8-sig"))) if name in z.namelist() else []
    cal = {r["service_id"]: r for r in read("calendar.txt")}
    added, removed = {}, {}
    for r in read("calendar_dates.txt"):
        (added if r["exception_type"] == "1" else removed).setdefault(r["service_id"], []).append(r["date"])
    rows = []
    for sid in sorted(set(cal) | set(added) | set(removed)):
        parts = [_days_text(cal[sid])] if sid in cal else []
        if sid in added:
            parts.append(("plus " if parts else "") + _dates_text(added[sid]))
        if sid in removed:
            parts.append("except " + _dates_text(removed[sid]))
        rows.append({"service_id": sid, "runs": ", ".join(parts)})
    return pl.DataFrame(rows)


def platforms() -> pl.DataFrame:
    """(stop_id, station_id, lat, lon): every platform (stop with a parent station) in the
    static feed's stops.txt."""
    z = zipfile.ZipFile(_static().zip_path)
    rows = [{"stop_id": r["stop_id"], "station_id": r["parent_station"], "lat": float(r["stop_lat"]),
             "lon": float(r["stop_lon"])}
            for r in csv.DictReader(io.TextIOWrapper(z.open("stops.txt"), "utf-8-sig")) if r.get("parent_station")]
    return pl.DataFrame(rows)


# what web/explorer/src/data/[table].parquet.py serves, as data/<name>.parquet
TABLES = {
    "trips": lambda: _static().trips,
    "trip_stops": lambda: _static().trip_stops,
    "shapes": lambda: _static().shapes,
    "stations": lambda: _static().stations,
    "shape_stations": lambda: _static().shape_stations,
    "date_trips": date_trips,
    "services": services,
    "platforms": platforms,
}
