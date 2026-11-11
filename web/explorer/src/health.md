---
title: Pipeline health
---

# Pipeline health

How well the decode and linearize stages did, one service date at a time.

```js
import {rows} from "./components/arrow.js";
import {dirName} from "./components/feed.js";
const meta = await FileAttachment("data/meta.json").json();
const allSpans = rows(await FileAttachment("data/fetch_spans.parquet").parquet());
const allTrips = rows(await FileAttachment("data/rt_trips.parquet").parquet());
const allRuns = rows(await FileAttachment("data/rt_runs.parquet").parquet());
const allOffsets = rows(await FileAttachment("data/vp_offsets.parquet").parquet());
const allFunnel = rows(await FileAttachment("data/funnel.parquet").parquet());
const allPieces = rows(await FileAttachment("data/funnel_pieces.parquet").parquet());
import {funnel} from "./components/funnel.js";
```

```js
const date = view(Inputs.select(meta.dates, {label: "Service date", value: meta.dates.at(-1)}));
```

```js
// ---- shared bits
const FEEDS = ["vehiclepositions", "tripupdates"];
const FEED_NAME = {vehiclepositions: "VP", tripupdates: "TU"};
const FEED_COLOR = {vehiclepositions: "#2a78d6", tripupdates: "#eb6834"};  // categorical slots 1, 2
const KINDS = ["exact", "inexact", "empty", "failed"];
const KIND_LABEL = {exact: "exact content time", inexact: "content time ≤ 15 s late", empty: "empty feed (no trains)",
  failed: "failed fetch"};
const KIND_COLOR = ["#1c5cab", "#86b6ef", "#c3c2b7", "#d03b3b"];  // blue 550, blue 250, neutral, status critical
const toDate = (s) => new Date(s * 1000);
const hhmm = (s) => (s == null ? "–" : toDate(s).toLocaleTimeString("en-US", {hour: "2-digit", minute: "2-digit", hour12: false}));
const pct = (x) => (x == null ? "–" : `${Math.round(100 * x)}%`);
const dur = (s) => (s == null ? "–" : s < 120 ? `${Math.round(s)} s` : s < 7200 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
const sumDur = (xs) => d3.sum(xs, (d) => d.end_epoch - d.start_epoch);
```

```js
// ---- this date's data
const spans = allSpans.filter((d) => d.date === date);
const trips = allTrips.filter((d) => d.date === date);
const runs = allRuns.filter((d) => d.date === date);
const offsets = allOffsets.filter((d) => d.date === date);
const sched = d3.sort(trips.filter((t) => t.scheduled), (t) => t.sched_start_epoch);
// the time axis: the whole service day (03:00 to 03:00 local) plus anything outside it
const x0 = Math.min(meta.day_windows[date][0], d3.min(spans, (d) => d.start_epoch));
const x1 = Math.max(meta.day_windows[date][1], d3.max(spans, (d) => d.end_epoch));
// per feed: successful spans, and the gaps (no successful fetch) between the first and last fetch
const perFeed = Object.fromEntries(FEEDS.map((feed) => {
  const fs = spans.filter((d) => d.feed === feed);
  const good = d3.sort(fs.filter((d) => d.kind !== "failed"), (d) => d.start_epoch);
  const gaps = [];
  for (let i = 1; i < good.length; i++)
    if (good[i].start_epoch > good[i - 1].end_epoch + 1) gaps.push({feed, start_epoch: good[i - 1].end_epoch, end_epoch: good[i].start_epoch});
  const content = good.filter((d) => d.kind !== "empty");
  return [feed, {good, gaps, fetched_s: sumDur(good), failed: fs.filter((d) => d.kind === "failed").length,
    exact: sumDur(content.filter((d) => d.kind === "exact")) / sumDur(content),
    longestGap: d3.max(gaps, (g) => g.end_epoch - g.start_epoch)}];
}));
```

## From service hours to complete trips

As of when this date was built (${toDate(meta.built[date]).toLocaleString("en-US", {dateStyle: "medium", timeStyle: "short"})}).
The time axis is elapsed service time: when some scheduled trip is running (dashed lines mark
where it skips time with none). A trip's *fetched run* is the part of its scheduled run covered
by success or empty fetches; it's coloured by the share of that in which the fetched snapshot
had it. Hover for times.

```js
const funnelOf = (feed) => resize((w) => funnel(allFunnel.filter((r) => r.date === date && r.feed === feed),
  allPieces.filter((r) => r.date === date && r.feed === feed), w));
```

<div class="card"><h2>Vehicle positions</h2>${funnelOf("vehiclepositions")}</div>
<div class="card"><h2>Trip updates</h2>${funnelOf("tripupdates")}</div>

## Fetches and content times

```js
// two lines per card: VP, then TU
const twoFeeds = (f) => html`${FEEDS.map((feed) => html`<div><span class="muted">${FEED_NAME[feed]}</span> <span class="big">${f(perFeed[feed])}</span></div>`)}`;
```

<div class="grid grid-cols-4">
  <div class="card"><h2>Time with a successful fetch</h2>${twoFeeds((p) => dur(p.fetched_s))}</div>
  <div class="card"><h2>…of which exact content time</h2>${twoFeeds((p) => pct(p.exact))}</div>
  <div class="card"><h2>Failed fetches</h2>${twoFeeds((p) => p.failed)}</div>
  <div class="card"><h2>Longest gap</h2>${twoFeeds((p) => dur(p.longestGap))}</div>
</div>

Each fetch stands for the time until the next fetch of its feed, at most 2 min. Blank is a gap:
no fetch at all. "Exact" counts only time with content (not empty feeds).

```js
display(Plot.plot({
  width, height: 150, marginLeft: 40,
  x: {type: "time", domain: [toDate(x0), toDate(x1)], label: null, grid: true},
  y: {domain: FEEDS, tickFormat: (f) => FEED_NAME[f], label: null},
  color: {domain: KINDS, range: KIND_COLOR, tickFormat: (k) => KIND_LABEL[k], legend: true},
  marks: [
    Plot.rectX(spans, {x1: (d) => toDate(d.start_epoch), x2: (d) => toDate(d.end_epoch), y: "feed", fill: "kind",
      title: (d) => `${FEED_NAME[d.feed]} ${hhmm(d.start_epoch)}: ${KIND_LABEL[d.kind]}`, tip: true}),
    Plot.rectX(FEEDS.flatMap((f) => perFeed[f].gaps), {x1: (d) => toDate(d.start_epoch), x2: (d) => toDate(d.end_epoch),
      y: "feed", fill: "transparent", title: (d) => `${FEED_NAME[d.feed]} gap ${hhmm(d.start_epoch)}–${hhmm(d.end_epoch)} (${dur(d.end_epoch - d.start_epoch)})`, tip: true}),
  ],
}));
```

## Trips: scheduled vs. seen

```js
const shown = (feed) => sched.filter((t) => (feed === "vehiclepositions" ? t.vp_fixes : t.tu_snapshots) > 0);
const fetchedFor = (feed) => sched.filter((t) => (feed === "vehiclepositions" ? t.vp_fetch_frac : t.tu_fetch_frac) >= 0.5);
const seenOf = (feed) => {
  const fetched = fetchedFor(feed), seen = new Set(shown(feed).map((t) => t.trip_id));
  return html`<span class="big">${seen.size}</span> <span class="muted">of ${sched.length} scheduled</span><br>
    <span class="muted">${fetched.filter((t) => seen.has(t.trip_id)).length} of the ${fetched.length} with ≥ half their run fetched</span>`;
};
const unscheduled = trips.filter((t) => !t.scheduled);
```

<div class="grid grid-cols-4">
  <div class="card"><h2>Trips in VP</h2>${seenOf("vehiclepositions")}</div>
  <div class="card"><h2>Trips in TU</h2>${seenOf("tripupdates")}</div>
  <div class="card"><h2>In RT but not scheduled</h2><span class="big">${unscheduled.length}</span>
    ${unscheduled.length ? html`<br><span class="muted">${unscheduled.map((t) => t.trip_id).join(", ")}</span>` : ""}</div>
</div>

One row per scheduled trip: its scheduled run (gray), and when it was in the VP feed (top
half) and the TU feed (bottom half), broken where it was missing for more than 2.5 min.
Shaded columns are fetch gaps.

```js
const tripOrder = [...sched, ...unscheduled].map((t) => t.trip_id);
const tripById = new Map(trips.map((t) => [t.trip_id, t]));
const tripTitle = (t) => [
  `trip ${t.trip_id}${t.route_id ? ` · ${t.route_id} ${dirName(t.direction_id)}` : " · not scheduled"}`,
  t.scheduled ? `scheduled ${hhmm(t.sched_start_epoch)}–${hhmm(t.sched_end_epoch)}` : null,
  `VP: ${t.vp_fixes} fixes, ${pct(t.vp_route_frac)} of the route, longest gap ${dur(t.vp_max_gap_s)}`,
  `TU: ${t.tu_snapshots} snapshots, ${pct(t.tu_stops_frac)} of stops predicted, longest gap ${dur(t.tu_max_gap_s)}`,
].filter(Boolean).join("\n");
const ROW = 11;
display(Plot.plot({
  width, height: 50 + ROW * tripOrder.length, marginLeft: 40,
  x: {type: "time", domain: [toDate(x0), toDate(x1)], label: null, grid: true, axis: "top"},
  y: {domain: tripOrder, label: null, axis: null},
  color: {domain: ["scheduled", "in VP", "in TU"], range: ["#c3c2b7", FEED_COLOR.vehiclepositions, FEED_COLOR.tripupdates], legend: true},
  marks: [
    Plot.axisY({tickSize: 0, fontSize: 9}),
    // fetch gaps of either feed, across all rows
    Plot.rectX(FEEDS.flatMap((f) => perFeed[f].gaps), {x1: (d) => toDate(d.start_epoch), x2: (d) => toDate(d.end_epoch),
      fill: "#d03b3b", fillOpacity: 0.08}),
    Plot.barX(sched, {x1: (t) => toDate(t.sched_start_epoch), x2: (t) => toDate(t.sched_end_epoch), y: "trip_id",
      fill: "#c3c2b7", fillOpacity: 0.6, insetTop: 1, insetBottom: 1, title: tripTitle, tip: true}),
    // VP in the top half of the row, TU in the bottom half (+30 s so a single snapshot shows)
    ...FEEDS.map((feed) => Plot.barX(runs.filter((r) => r.feed === feed && tripById.has(r.trip_id)), {
      x1: (r) => toDate(r.start_epoch), x2: (r) => toDate(r.end_epoch + 30), y: "trip_id", fill: FEED_COLOR[feed],
      insetTop: feed === "vehiclepositions" ? 2 : ROW / 2, insetBottom: feed === "vehiclepositions" ? ROW / 2 : 2})),
  ],
}));
```

## Completeness of the trips that showed up

```js
const inVP = sched.filter((t) => t.vp_fixes > 0);
const inTU = sched.filter((t) => t.tu_snapshots > 0);
const median = (xs, f) => d3.median(xs, f);
const hist = (data, value, {label, color, thresholds, format = (x) => x}) => Plot.plot({
  width: Math.min(width, 440), height: 180, marginBottom: 35,
  x: {label, tickFormat: format}, y: {label: "trips", grid: true},
  marks: [
    Plot.rectY(data, Plot.binX({y: "count"}, {x: value, thresholds, fill: color, inset: 1, tip: true})),
    Plot.ruleY([0]),
  ],
});
```

<div class="grid grid-cols-4">
  <div class="card"><h2>VP: median share of the route</h2><span class="big">${pct(median(inVP, (t) => t.vp_route_frac))}</span></div>
  <div class="card"><h2>VP: median longest gap</h2><span class="big">${dur(median(inVP, (t) => t.vp_max_gap_s))}</span></div>
  <div class="card"><h2>TU: median share of stops predicted</h2><span class="big">${pct(median(inTU, (t) => t.tu_stops_frac))}</span></div>
  <div class="card"><h2>TU: median first seen before departure</h2><span class="big">${dur(median(inTU, (t) => t.tu_lead_s))}</span></div>
</div>

<div class="grid grid-cols-2">
  <div class="card">
    <h2>VP: share of the route (first to last stop) its fixes span</h2>
    ${hist(inVP, "vp_route_frac", {label: "share of route", color: FEED_COLOR.vehiclepositions, thresholds: d3.range(0, 1.0001, 0.05), format: pct})}
  </div>
  <div class="card">
    <h2>VP: longest gap between a trip's fixes</h2>
    ${hist(inVP, (t) => t.vp_max_gap_s / 60, {label: "minutes", color: FEED_COLOR.vehiclepositions, thresholds: 30})}
  </div>
  <div class="card">
    <h2>TU: share of the trip's scheduled stops ever predicted</h2>
    ${hist(inTU, "tu_stops_frac", {label: "share of stops", color: FEED_COLOR.tripupdates, thresholds: d3.range(0, 1.0001, 0.05), format: pct})}
  </div>
  <div class="card">
    <h2>TU: longest gap between snapshots with the trip</h2>
    ${hist(inTU, (t) => t.tu_max_gap_s / 60, {label: "minutes", color: FEED_COLOR.tripupdates, thresholds: 30})}
  </div>
</div>

## Linearize: VP fixes' distance from their trip's shape

```js
const BUCKETS = [[0, 5], [5, 10], [10, 20], [20, 50], [50, 100], [100, meta.far_m], [meta.far_m, Infinity]];
const bucketName = ([a, b]) => (b === Infinity ? `> ${a} m` : `${a}–${b} m`);
const counts = BUCKETS.map((b) => ({bucket: bucketName(b), far: b[0] >= meta.far_m,
  n: offsets.filter((o) => o.offset_m >= b[0] && o.offset_m < b[1]).length}));
const far = offsets.filter((o) => o.offset_m > meta.far_m);
```

<div class="grid grid-cols-4">
  <div class="card"><h2>Fixes</h2><span class="big">${offsets.length}</span></div>
  <div class="card"><h2>Median distance</h2><span class="big">${(d3.median(offsets, (o) => o.offset_m) ?? 0).toFixed(1)} m</span></div>
  <div class="card"><h2>Further than ${meta.far_m} m (ignored by the reference)</h2><span class="big">${far.length}</span>
    <span class="muted">${(100 * far.length / Math.max(offsets.length, 1)).toFixed(2)}%${far.length ? ` · trips ${[...new Set(far.map((o) => o.trip_id))].join(", ")}` : ""}</span></div>
</div>

```js
display(Plot.plot({
  width: Math.min(width, 640), height: 200,
  x: {domain: counts.map((c) => c.bucket), label: "distance from shape", tickSize: 0},
  y: {label: "fixes", grid: true},
  color: {domain: [false, true], range: [FEED_COLOR.vehiclepositions, "#d03b3b"]},
  marks: [
    Plot.barY(counts, {x: "bucket", y: "n", fill: "far", inset: 2, tip: true, title: (c) => `${c.bucket}: ${c.n} fixes`}),
    Plot.text(counts, {x: "bucket", y: "n", text: "n", dy: -6, fill: "currentColor"}),
    Plot.ruleY([0]),
  ],
}));
```

## All trips

```js
display(Inputs.table([...sched, ...unscheduled], {
  columns: ["trip_id", "route_id", "direction_id", "sched_start_epoch", "sched_end_epoch", "vp_fetch_frac", "vp_fixes",
    "vp_route_frac", "vp_max_gap_s", "vp_far_fixes", "tu_fetch_frac", "tu_snapshots", "tu_stops_frac", "tu_extra_stops",
    "tu_lead_s", "tu_max_gap_s"],
  header: {trip_id: "trip", route_id: "route", direction_id: "dir", sched_start_epoch: "sched start", sched_end_epoch: "sched end",
    vp_fetch_frac: "VP fetched", vp_fixes: "VP fixes", vp_route_frac: "VP route", vp_max_gap_s: "VP max gap",
    vp_far_fixes: `VP > ${meta.far_m} m`, tu_fetch_frac: "TU fetched", tu_snapshots: "TU snaps", tu_stops_frac: "TU stops",
    tu_extra_stops: "TU unsched. stops", tu_lead_s: "TU lead", tu_max_gap_s: "TU max gap"},
  format: {direction_id: (d) => (d == null ? "" : dirName(d).slice(0, 5)), sched_start_epoch: hhmm, sched_end_epoch: hhmm,
    vp_fetch_frac: pct, vp_route_frac: pct, tu_fetch_frac: pct, tu_stops_frac: pct,
    vp_max_gap_s: dur, tu_max_gap_s: dur, tu_lead_s: dur},
  rows: 20,
}));
```
