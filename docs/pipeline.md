# The pipeline

Raw archive → tables. Every table and column is declared in
the `schemas.py` of the stage that writes it.

## Principles

- **Stages are plain functions from tables to tables.** No orchestration framework.
- **Derived data is disposable.** Everything under `data/derived/` is rebuilt from the raw
  archive (`data/rt`, `data/gtfs`: a local copy of the GCS bucket, updated only when you run
  `just sync`, see [deploy.md](deploy.md)) and the hand-maintained inputs in the repo.
  Builds never sync, so you iterate on a fixed snapshot until you choose to update it.
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
| static | `just build` | `data/derived/static/<feed_version>/`: trips, trip_stops, shapes, stations, shape_stations, platforms, calendar, calendar_dates |
| decode | `just build` | `data/derived/<date>/`: fetches, vp, tu |
| tu_checks | `just build` | `data/derived/<date>/tu_checks.parquet` |
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
  earlier content are dropped. `fetches` keeps every request (status, content time,
  exactness, whether it was a duplicate) for the Pipeline health page.
- **tu_checks**: checks what we assume about how a trip's trip updates evolve (all in terms
  of departures): H1, if seen 10-15 min before its scheduled departure, it lists every stop
  but the terminus; H2, from then on each snapshot lists a suffix of its scheduled stops but
  the terminus; H3, no departure is in the past; H4, every stop has a departure, and an
  arrival only at departure - 60 s. H3 and H4 have always held: a violation stops the build.
  H1 and H2 sometimes fail: each trip's H1 outcome and each snapshot's H2 outcome are written
  for the Pipeline health page, along with whether each trip was missing from any non-empty
  trip update it was expected in ("listed"). Stop lists are compared up to 90 min ahead (511's listing
  horizon); only trip updates are judged, so missing or failed fetches never count as
  failures. Details in the stage's docstring.
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

- **Static feed**: what the static stage made of the GTFS feed (one line per service: when
  it runs and its trips by route and direction; a map of shapes, stations and platforms,
  rotated so the line runs left to right, with a route-km axis).
  - **Trip patterns**: a time-distance diagram of the trip patterns, with a picker to show /
    hide routes and patterns and to select one (or click a dot) to list its trips and when
    each runs.
- **Timetables**: pick a service, a direction and a first and last trip; a table with a
  row per station and, per trip, its static GTFS time and its GTFS-RT time from 511's
  pre-departure trip updates (stage `timetable`), with the time since the previous stop in
  a slim row between stations (`explorer_data.timetable`).
- **Pipeline health**: every built date at a glance, a row per date with a bar each for VP
  and TU, as totals or over the day (03:00 to 03:00). *Fetcher health*
  (`explorer_data.fetch_status`): each date's elapsed service time (union of scheduled runs,
  up to the build) by the latest fetch within the previous 2.5 min: successful with fresh
  content (exact content time, or an empty feed), successful with an unknown content stamp
  (the raw stamp only), failed, or missing. *Data presence* (`explorer_data.data_presence`):
  the time with a successful fetch by whether the feed had any entities against whether it
  was expected to (VP is empty from just after midnight; everything else should have data),
  judged per fetch by its content time. *Trip-update hypotheses* (`explorer_data.tu_checks`):
  stage tu_checks' listed, H1 and H2, a card each (listed and H1 by trip, H2 by snapshot; listed
  and H2 failures by kind): the share that held or failed, or a dot per failure.
