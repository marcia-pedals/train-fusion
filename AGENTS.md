# Notes for agents

How the code is organised is in [README.md](README.md) and [docs/pipeline.md](docs/pipeline.md).
One rule that's easy to break without noticing:

## Only the source stages read the raw archive

The raw archive (`data/gtfs`: static GTFS zips; `data/rt`: GTFS-RT snapshots and fetch logs)
is read by exactly two stages:

- `static` reads static GTFS zips;
- `decode` reads GTFS-RT snapshots and the fetcher's logs.

Every other stage, and the explorer's data loaders (`train_fusion/explorer_data.py`), read
only tables that earlier stages wrote to `data/derived` (declared in each stage's
`schemas.py`). Never open a zip, a `.pb` file or a fetch log anywhere else.

If a later stage needs something from the source that no table has yet, add it to a source
stage's tables instead of going back to the source. For example:

- which static feed applies to a date is decided by `static` and written to `feed_versions`;
  `Static(day)` reads that, never the zips;
- South County trips are out of scope: `static` drops them from the schedule and writes their
  trip_ids to `excluded_trips`, and `decode` drops their RT entities. No later stage needs to
  know South County exists.

Listing which dates the archive has (`paths.archived_dates()`) is fine: that's choosing what to
build, not reading data.
