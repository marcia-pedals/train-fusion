---
title: Timetable
---

# Timetable

Every scheduled departure is an interval [lo, hi] that contains the true timetable time
(stage `timetable`). Static GTFS gives whole minutes, truncated: [S, S + 60]. 511's trip
updates published before a trip starts repeat the timetable on a 6 s grid; 511 never
rounds past the static minute, so it truncates (or is exact): [t, t + 6]. The interval is
[t, t + 6] where 511 gives t, else [S, S + 60]; the build checks that every trip update
gives the same t for a stop and that S is t truncated to the minute.

```js
import {rows, timetableOffsets, timetableWidths, fitScatter, shortName} from "./components/refplots.js";
const tt = rows(await FileAttachment("data/timetable.parquet").parquet());
const meta = await FileAttachment("data/meta.json").json();
const fit = rows(await FileAttachment("data/ref_fit.parquet").parquet());
const patterns = rows(await FileAttachment("data/patterns.parquet").parquet());
const stationName = new Map(rows(await FileAttachment("data/stations.parquet").parquet()).map((s) => [s.station_id, shortName(s.name)]));
const narrowed = tt.filter((r) => r.source === "tu");
const trips = (rs) => new Set(rs.map((r) => r.trip_id)).size;
```

<div class="grid grid-cols-2">
  <div class="card"><h2>Stops (all trips in the feed)</h2><span class="big">${tt.length}</span><br><span class="small muted">${trips(tt)} trips</span></div>
  <div class="card"><h2>Narrowed by trip updates</h2><span class="big">${narrowed.length}</span><br><span class="small muted">${trips(narrowed)} trips; the rest only run on days not archived yet</span></div>
</div>

## Interval widths by route

```js
display(resize((width) => timetableWidths(tt, {width})))
```

## Where in the static minute the timetable really is

For narrowed stops: interval midpoint minus the static time. Everything falls inside
[0, 60] (dashed), which is why static is treated as truncated.

```js
display(resize((width) => timetableOffsets(tt, {width})))
```

## Fit: physics + dwell + padding

Trips with the same stations and static times relative to their first departure form a
**pattern**; its trips share their timetable (the build checks that trip updates give
them identical intervals). For every segment of every pattern: scheduled time = (1 + p)·R +
W + padding, with R the physical minimum run time, p a recovery allowance proportional to
it, W the dwell, and padding ≥ 0 (`train_fusion/stages/reference/timetable_fit.py`; the LP is in
`docs/reference.md`). The pattern's departure times are unknowns, each anywhere in its
interval, so wide intervals only add freedom, never false constraints. The fit minimises the total padding. Dots: (pattern, segment) (filled =
times narrowed by trip updates, hollow = static only; triangles = into a terminal, no W).
Blue: the fitted line for intermediate segments. Dashed: Δ = R.

<div class="grid grid-cols-3">
  <div class="card"><h2>Recovery allowance p</h2><span class="big">${(100 * meta.p).toFixed(0)}%</span><br><span class="small muted">fitted; not part of the reference</span></div>
  <div class="card"><h2>Dwell W</h2><span class="big">${meta.W_s.toFixed(0)} s</span><br><span class="small muted">at accel ${meta.accel_mps2} m/s²; the reference's dwell</span></div>
  <div class="card"><h2>Patterns</h2><span class="big">${meta.patterns}</span><br><span class="small muted">${meta.trips} trips; ${meta.paddings} paddings; ${meta.patterns_narrowed} patterns narrowed by trip updates</span></div>
</div>

```js
display(resize((width) => fitScatter(fit, meta.p, meta.W_s, {width})));
```

**Where the timetable keeps its slack** (padding beyond the fitted line, per pattern and segment):

```js
const segName = (id) => id.split(">").map((x) => stationName.get(x) ?? x).join(" → ");
const pat = new Map(patterns.map((p) => [p.pattern_id, p]));
const patName = (id) => { const p = pat.get(id); return `${id} · ${p.route_id} ${p.direction_id === 0 ? "N" : "S"} · ${stationName.get(p.from_station) ?? p.from_station} → ${stationName.get(p.to_station) ?? p.to_station}`; };
display(Inputs.table(d3.sort(fit, (r) => -r.pad_s).map((r) => ({pattern: patName(r.pattern_id),
  segment: segName(r.segment_id) + (r.final ? " (end)" : ""), trips: r.n_trips, narrowed: r.narrowed ? "yes" : "",
  "R (s)": r.phys_s, "model (s)": r.model_s, "scheduled (s)": r.sched_s, "padding (s)": r.pad_s})),
  {format: {"R (s)": (x) => x.toFixed(0), "model (s)": (x) => x.toFixed(0), "scheduled (s)": (x) => x.toFixed(0), "padding (s)": (x) => x.toFixed(0)}, rows: 12}));
```

**Patterns:**

```js
display(Inputs.table(patterns.map((p) => ({pattern: p.pattern_id, route: p.route_id, dir: p.direction_id === 0 ? "N" : "S",
  from: stationName.get(p.from_station) ?? p.from_station, to: stationName.get(p.to_station) ?? p.to_station,
  stops: p.n_stops, trips: p.n_trips,
  "total padding (s)": d3.sum(fit.filter((r) => r.pattern_id === p.pattern_id), (r) => r.pad_s)})),
  {format: {"total padding (s)": (x) => x.toFixed(0)}, rows: 19}));
```
