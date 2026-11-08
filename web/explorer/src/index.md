---
title: Reference segments
---

# Reference segments

The **reference** is the fastest a train can possibly run: one curve per pair of
consecutive served stops (from "starts moving" to "stops moving"), the minimum-time run
under the track speed limits at ${meta.accel_mps2} m/s², plus a dwell of
${meta.W_s.toFixed(0)} s at every stop, fitted to the timetable (see the Timetable page).
Chained along a stop pattern it's a trip's reference trajectory; delay is measured
against it. Details: `docs/reference.md`.

**How it's checked:** between any two fixes of a trip, the train can't be faster than the
reference, so *excess* = (actual time between the fixes) − (reference time between their
positions) should be ≥ 0, up to timing noise. Only **cruise fixes** are used: where the
reference runs at constant speed for ${meta.cruise_margin_m} m either side. Between two
of those, the time doesn't depend on where exactly trains stop (or where the GPS antenna
sits on the train). Each cruise fix is paired with the trip's next one, however many
stops lie between. Repeated coordinates (standing trains, or stale positions), fixes near
the end stops and fixes with uncertain timestamps are left out.

```js
import {rows, segmentCurve, speedProfile, excessHist, traversals, shortName} from "./components/refplots.js";
const meta = await FileAttachment("data/meta.json").json();
const stationName = new Map(rows(await FileAttachment("data/stations.parquet").parquet()).map((s) => [s.station_id, shortName(s.name)]));
const segments = rows(await FileAttachment("data/ref_segments.parquet").parquet());
const knots = rows(await FileAttachment("data/ref_knots.parquet").parquet());
const limits = rows(await FileAttachment("data/ref_limits.parquet").parquet());
const cruise = rows(await FileAttachment("data/ref_cruise.parquet").parquet());
const fixes = rows(await FileAttachment("data/ref_fixes.parquet").parquet());
const pairs = rows(await FileAttachment("data/ref_pairs.parquet").parquet());
const schedule = rows(await FileAttachment("data/schedule.parquet").parquet());
const segName = (s) => `${stationName.get(s.from_station) ?? s.from_station} → ${stationName.get(s.to_station) ?? s.to_station}`;
```

```js
const within = pairs.filter((p) => p.stops === 0);
const oneStop = pairs.filter((p) => p.stops === 1);
const multiStop = pairs.filter((p) => p.stops >= 2);
// for the speed chart: every two consecutive usable fixes in the same segment (not just cruise fixes)
const steps = d3.groups(fixes.filter((f) => f.content_exact && !f.repeat && f.zone === "line"), (f) => `${f.date} ${f.trip_id}`)
  .flatMap(([, g]) => d3.pairs(d3.sort(g, (f) => f.t_epoch)).filter(([a, b]) => a.run_idx === b.run_idx).map(([a, b]) => ({
    date: a.date, trip_id: a.trip_id, segment_id: a.segment_id, s0_m: a.s_m, s1_m: b.s_m, p0: a.p_m, p1: b.p_m,
    dt_s: b.t_epoch - a.t_epoch, excess_s: (b.t_epoch - a.t_epoch) - (b.tau_s - a.tau_s)})));
const pct = (x) => `${(100 * x).toFixed(0)}%`;
const card = (label, k) => {
  const s = meta.summary[k] ?? {};
  return html`<div class="card"><h2>${label}: excess < −15 s</h2><span class="big">${pct(s["below_-15s"] ?? 0)}</span><br><span class="small muted">of ${s.n ?? 0} pairs · excess < 0: ${pct(s.below_0s ?? 0)} · minimum ${s.min_s} s · 5th pct ${s.q05_s} s · median ${s.q50_s} s</span></div>`;
};
```

<div class="grid grid-cols-4">
  <div class="card"><h2>Reference</h2><span class="small">accel ${meta.accel_mps2} m/s² · dwell ${meta.W_s.toFixed(1)} s · ${segments.length} segments</span><br><span class="small muted">static feed ${meta.static_version} · dates ${meta.dates.join(", ")}</span></div>
  ${card("Within a segment", "within")}
  ${card("Across 1 stop", "1 stop")}
  ${card("Across 2+ stops", "2+ stops")}
</div>

## Excess over all pairs

Excess = actual minus reference time between the two fixes (clipped to −150…250 s).
Orange bars are pairs where the train ran *faster* than the reference says is possible.
Within a segment that tests the curve; across one stop it tests the curves plus the whole
stop (braking, dwell, accelerating); 2+ stops covers segments too short to have cruise
fixes of their own.

<div class="grid grid-cols-3">
  <div>${resize((width) => excessHist(within, {width, label: "within a segment"}))}</div>
  <div>${resize((width) => excessHist(oneStop, {width, label: "across 1 stop"}))}</div>
  <div>${resize((width) => excessHist(multiStop, {width, label: "across 2+ stops"}))}</div>
</div>

## Segments

Pick a segment in the table (sorted by share with excess < −15 s). "Pairs" are cruise
pairs within the segment; short segments have none.

```js
const table = segments.map((s) => {
  const w = within.filter((p) => p.segment_id === s.segment_id);
  return {segment: segName(s), segment_id: s.segment_id, dir: s.direction_id === 0 ? "N" : "S",
    "length (m)": s.length_m, "run (s)": s.run_s, "v max (m/s)": s.v_max_mps,
    trips: new Set(fixes.filter((f) => f.segment_id === s.segment_id).map((f) => `${f.date} ${f.trip_id}`)).size,
    pairs: w.length, "excess < 0": w.length ? d3.mean(w, (p) => p.excess_s < 0) : null,
    "excess < −15 s": w.length ? d3.mean(w, (p) => p.excess_s < -15) : null,
    "min excess (s)": w.length ? d3.min(w, (p) => p.excess_s) : null,
    "median excess (s)": w.length ? d3.median(w, (p) => p.excess_s) : null};
});
const sorted = d3.sort(table, (r) => -(r["excess < −15 s"] ?? -1), (r) => -r.pairs);
const picked = view(Inputs.table(sorted, {multiple: false, required: true, value: sorted[0], rows: 12,
  columns: ["segment", "dir", "length (m)", "run (s)", "v max (m/s)", "trips", "pairs", "excess < 0", "excess < −15 s", "min excess (s)", "median excess (s)"],
  format: {"length (m)": (x) => x.toFixed(0), "run (s)": (x) => x.toFixed(0), "v max (m/s)": (x) => x.toFixed(1),
    "median excess (s)": (x) => (x == null ? "" : x.toFixed(0)), "min excess (s)": (x) => (x == null ? "" : x.toFixed(0)),
    "excess < 0": (x) => (x == null ? "" : pct(x)), "excess < −15 s": (x) => (x == null ? "" : pct(x))}}));
```

```js
const seg = segments.find((s) => s.segment_id === picked.segment_id);
const segKnots = knots.filter((k) => k.segment_id === seg.segment_id);
const segFixes = fixes.filter((f) => f.segment_id === seg.segment_id && f.zone !== "origin");
const segSteps = steps.filter((p) => p.segment_id === seg.segment_id);
const segPairs = within.filter((p) => p.segment_id === seg.segment_id);
// scheduled departure from this segment's start stop, per (date, trip)
const schedDep = new Map(schedule.filter((r) => r.station_id === seg.from_station).map((r) => [`${r.date} ${r.trip_id}`, r.t_epoch]));
const align = view(Inputs.radio(new Map([["aligned to the curve", "fit"], ["from the scheduled departure", "schedule"]]),
  {label: "Time axis", value: "fit"}));
```

### ${segName(seg)} <span class="muted small">${seg.length_m.toFixed(0)} m · reference run ${seg.run_s.toFixed(0)} s at up to ${seg.v_max_mps.toFixed(1)} m/s</span>

Blue: the reference curve (dots = its 21 knots). Orange: each run's fixes in this segment
(hollow = repeated coordinate; faded = not a cruise fix). "Aligned to the curve" shifts
each run so it sits on the curve on average, which shows *shape* differences; "from the
scheduled departure" shows where runs really are relative to the timetable (dwells and
late departures included).

```js
display(resize((width) => segmentCurve(segKnots, traversals(segFixes, schedDep, align, seg.length_m), seg, {width, align})));
```

**Speed.** Blue: the reference's speed. Dashed: the track speed limit
(`track/speed_limits.toml`). Shaded: where cruise fixes can be (constant reference speed,
${meta.cruise_margin_m} m margin). Orange strokes: average speed between two consecutive
fixes, spanning the stretch between them. Where orange is above blue over a stretch, trains
covered it faster than the reference. The histogram is this segment's cruise pairs.

```js
display(resize((width) => speedProfile(segKnots, segSteps, seg, limits.filter((l) => l.segment_id === seg.segment_id),
  cruise.filter((c) => c.segment_id === seg.segment_id), meta.cruise_margin_m, {width})));
display(resize((width) => excessHist(segPairs, {width, label: "this segment"})));
```
