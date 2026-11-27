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

## No tooltips unless asked

Never add tooltips (`data-tip`, `title`, hover popups) to any UI (the viewers, the explorer,
the app) unless explicitly asked for that tooltip. If a control needs explaining, its label or
the visible text next to it should do it.

## The phone app's inference is experimental and stands alone

`ios/TrainRecorder/Inference/` re-implements `phone-sensor-fusion`'s features and forward
filter in Swift. For now:

- **Don't try to share code between the app and the Python model.** The only link is
  `ios/TrainRecorder/model.json`, written by `python -m app.export` (in
  `phone-sensor-fusion/`). Changing the Python model means re-exporting and, if the
  computation changed, porting the change by hand; `python -m app.compare` checks the port.
- **Don't worry about backwards compatibility on the app side**: not of `model.json`, not
  of dumps, not of how things are loaded. Break and throw away freely.

Recordings (the files a recording consists of, `docs/recorder.md`) are different: those
are data we keep, and their format should only grow.

## Private data stays out of git

The phone recordings are of one person's train rides. Which trains they took, when, and
between which stations is private, and so is anything said near the phone. None of it goes
into a file git sees: not code, not docs, not comments, not file names, not figures, not
commit messages.

Everything of that kind lives under the gitignored `data/`:

- recordings (`data/recordings/raw`) and whisper transcripts;
- labels (`data/recordings/labels`): made by hand, not rebuildable. Backed up both ways
  with the bucket's `labels/` by `just sync-recordings`: sync after labelling;
- anything named after a recording or drawn from one: the leave-one-out parameters
  (`data/phone-sensor-fusion/params`), plots, viewer exports, replay dumps.

In files git sees:

- **No recording ids.** An id is the ride's start time. Scripts take ids as arguments or
  find them (`paths.recording_ids()`, the labels directory); they don't hard-code them.
- **No dates, times of day, trip or car numbers, origins or destinations of a ride**, and
  no named station in connection with a ride ("the leg into X"). Station names as facts
  about the line (the map, the model's station list, a made-up example) are fine.
- **Rides are called A, B, C, …** in docs and comments. The key is
  `data/recordings/labels/RIDES.md`; add to it when a ride gets labelled.
- **No quotes from transcripts**, of announcements or of people.
- **No figures made from a recording.** Say which command draws it instead.
- Only `phone-sensor-fusion/params/all.json` is checked in: it is fitted on every ride
  and names none.

Before committing, look at what is being added for these (and for anything large):
`git diff --cached --stat`, and grep the diff for recording ids (`20\d{6}T\d{6}Z`), dates
and station names.
