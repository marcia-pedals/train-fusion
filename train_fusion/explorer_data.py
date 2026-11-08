"""Tables served to the explorer (web/explorer), read from what the pipeline wrote to
data/derived. web/explorer/src/data/[table].parquet.py serves TABLES[<table>]() as
data/<table>.parquet; web/explorer/src/data/meta.json.py serves meta()."""
from __future__ import annotations

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


# what web/explorer/src/data/[table].parquet.py serves, as data/<name>.parquet
TABLES = {
    "trips": lambda: _static().trips,
    "trip_stops": lambda: _static().trip_stops,
    "shapes": lambda: _static().shapes,
    "stations": lambda: _static().stations,
    "shape_stations": lambda: _static().shape_stations,
    "date_trips": date_trips,
}
