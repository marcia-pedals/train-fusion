# Viewer

A local web app over what the pipeline writes. Two pages:

- **Results** (`#/results?run=&vs=`): the evaluation's tables (`../model/evaluate.py`,
  `../model/schemas.py`), one ride or one leg per row, to search and sort; "Columns" picks
  which columns show (remembered per table). A row links to the ride page, for the whole
  ride or zoomed to the leg. Every pipeline run is
  kept (`../pipeline.py`); the page shows the newest unless "Run" picks another. Pick a baseline under "vs" and each
  metric also shows its value there, green where this run is better, red where worse, grey
  where the change is too small to count (`diff` in `src/results/columns.ts` says how much
  is enough), "=" where it is the same; "Changed only" keeps the rows with a real change,
  and "Sort by change" sorts the metric columns by how much they moved.
- **Ride** (`#/ride/<id>?run=&t0=&t1=&t=`): one ride in detail, as one run's inference saw
  it (`../model/ride_view.py` writes it; by default the newest run's), on one time axis: the audio and accelerometer spectrograms with the labelled door
  chimes as lines (green opening, red closing; solid where the chime starts, dashed where
  it ends); the filtered and smoothed beliefs over speed (m/s) and the most likely path's speed; mode
  (labelled, filtered, smoothed, and the most likely path, whose entries into the door-open
  chime are the localiser's door openings); and the live
  localiser's probability for the truth and for its own best guess, with the confidently
  wrong stretches in red. The left column gives each track's value at the mouse, or at
  the cursor. Zoom: pinch, ⌘/ctrl + scroll, + and −; pan: drag, scroll sideways, or the
  whole-ride strip at the top; click to put the cursor, space to play the audio from it
  (1–8×, from the left edge if there is no cursor), ← → to move it, "Clear cursor" to
  remove it, 0 for the whole ride. The speed beliefs' values are the mean and the central 95% interval.
  "Hide identifying info" (remembered) leaves nothing on the page about the ride beyond the
  section in view: no date or first and last stations, no whole-ride strip, and times
  counted from the view's left edge (so the view reads 0:00–its length). The address still has the recording id. The address keeps the view and cursor.

React + TypeScript, built and served by Vite. Local only: what it shows names recordings,
so it is never built for or deployed anywhere.

```sh
python -m pipeline                  # from phone-sensor-fusion/: a run, in data/phone-sensor-fusion/eval/runs/<run>/
python -m model.schemas             # src/generated/tables.ts: the tables' types and column descriptions
cd viewer && npm install && npm run dev     # http://127.0.0.1:3040
npm run check                       # type-check
```

(`just psf-view` from the repo root does the last two.) Open pages reload when a run
finishes.

## How it is put together

- `vite.config.ts`: the dev server, which also serves `data/phone-sensor-fusion/` under
  `/data/`, the list of finished runs as `/runs.json`, and a recording's audio file under `/audio/` (nothing else of the raw
  recording), both with HTTP range requests, which seeking needs.
- `src/App.tsx`: the pages, chosen by the address (`src/lib/hash.ts`).
- `src/ride/`: the ride page: `RidePage.tsx` (state, interaction, the list of tracks),
  `draw.ts` (canvas drawing: spectrogram tiles, lines, bands, stacked probabilities),
  `timeline.ts` (time axis and colours), `Canvas.tsx`.
- `src/lib/`: loading (`data.ts`: Parquet via hyparquet, JSON), number formats, station
  codes (`stations.ts`) and how a ride is named on screen (`rides.ts`: its date and first and
  last stations, from the recording id and the labelled legs).
- `src/components/Tooltip.tsx`: the app's tooltip; give any element a `data-tip` (not a
  `title`).
- `src/results/`: the page, its table (TanStack Table: sorting), the column list
  (`columns.ts`: header, format, and when a value shows amber or red) and the menu that
  hides columns (`ColumnPicker.tsx`).
- `src/generated/`: written by `python -m model.schemas`, not checked in.
