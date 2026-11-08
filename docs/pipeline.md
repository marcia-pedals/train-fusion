# The pipeline

Raw archive → tables. Every table and column is declared in
the `schemas.py` of the stage that writes it.

## Principles

- **Stages are plain functions from tables to tables.** No orchestration framework.
- **Derived data is disposable.** Everything under `data/derived/` is rebuilt from the raw
  archive (`data/rt`, `data/gtfs`) and the hand-maintained inputs in the repo.
- **Schemas live with their stage.** Each stage is a directory
  `train_fusion/stages/<stage>/` with its code (`stage.py`) and the tables it writes
  (`schemas.py`, plain Python, via `train_fusion/common/schema_dsl.py`), which give the polars
  schemas used when writing (cast strictly, fail loudly). Shared code is in
  `train_fusion/common/` (paths, the schema language, time helpers).
- **Python computes; the browser draws.** The explorer only reads files the pipeline
  wrote; its TypeScript types are generated from the stages' schemas (`just types`, into
  `web/packages/types/tables.ts`, not checked in; `just explore` regenerates them).
- All times are epoch seconds; units are column-name suffixes.

## Stages

One command derives everything: `just build <dates>` (or `just build --last-week`, every
archived date among the 7 days ending today) wipes `data/derived` and runs all the stages
below for exactly those service dates (`train_fusion/build.py`, run with `python -m`).

| Stage | Command | Writes |
|---|---|---|
| static | `just build` | `data/derived/static/<feed_version>/`: trips, trip_stops, shapes, stations, shape_stations |
| decode | `just build` | `data/derived/<date>/`: vp, tu |
| linearize | `just build` | `data/derived/<date>/vp_lin.parquet` |
| timetable | `just build` | `data/derived/static/<feed_version>/timetable.parquet` |
| reference | `just build` | `data/derived/reference/`: ref_segments, ref_knots, ref_limits, ref_cruise, ref_fit, reference.json; `<date>/`: ref_trips, ref_fixes, ref_pairs |

- **static**: the static GTFS zip for a date is the newest fetched on or before it. GTFS
  times are seconds after the service-day origin (local noon − 12 h); static arrival ==
  departure, whole minutes. South County trips are dropped (`EXCLUDED_ROUTES`): the
  project covers San Francisco ↔ Tamien only.
- **decode**: each snapshot is timed by when its content first appeared (511 re-stamps
  every ~15 s but changes content every ~30 s; see [fetcher.md](fetcher.md)).
  `content_exact = false` means the time may be up to 15 s late. Snapshots repeating
  earlier content are dropped.
- **linearize**: each VP fix is projected onto its trip's shape (UTM 10N), giving s (m
  along the shape, matching the stops' `shape_dist_traveled`) and its distance from it.
- **timetable**: every scheduled departure as an interval [`dep_lo_s`, `dep_hi_s`]
  holding the true timetable time: [t, t + 6] from 511's pre-departure trip updates, else
  [S, S + 60] from static GTFS. Uses the trip updates of the dates being built, and stops
  the build unless they all give the same t for a stop and S is t truncated to the minute.
- **reference**: see [reference.md](reference.md).

## Hand-maintained input

`track/speed_limits.toml`: track speed limits SF ↔ Tamien, as restrictions below a 79 mph
default, each anchored to stations (offsets in m, positive towards SF). Taken once from
OpenStreetMap; edit by hand. `train_fusion.stages.reference.track.on_shape()` turns it into a
piecewise-constant limit along any shape.

## Explorer

`just explore` → http://127.0.0.1:3030 (Observable Framework, `web/explorer/`), over
whatever `just build` last wrote. One parameterized data loader,
`web/explorer/src/data/[table].parquet.py`, serves every `data/<table>.parquet` from
`train_fusion.explorer_data.TABLES` (plus `meta.json.py`); adding a table for a page is one
entry there. Pages:

- **Static feed**: what the static stage made of the GTFS feed (counts, the trips running
  on each built date by route and direction, a map of shapes and stations, the trip
  patterns).
