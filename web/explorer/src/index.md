---
title: Static feed
---

# Static feed

What the `static` stage made of Caltrain's static GTFS feed **${meta.static_version}**
(the one used by the newest built date; built dates: ${meta.dates.join(", ")}). South
County trips are dropped.

```js
import {rows} from "./components/arrow.js";
const meta = await FileAttachment("data/meta.json").json();
const trips = rows(await FileAttachment("data/trips.parquet").parquet());
const tripStops = rows(await FileAttachment("data/trip_stops.parquet").parquet());
const shapes = rows(await FileAttachment("data/shapes.parquet").parquet());
const stations = rows(await FileAttachment("data/stations.parquet").parquet());
const shapeStations = rows(await FileAttachment("data/shape_stations.parquet").parquet());
const dateTrips = rows(await FileAttachment("data/date_trips.parquet").parquet());
```

```js
const stationName = new Map(stations.map((s) => [s.station_id, s.name.replace(/ Caltrain( Station)?$/, "").replace(/ Station$/, "")]));
const servedStations = new Set(tripStops.map((r) => r.station_id));
const onLine = stations.filter((s) => shapeStations.some((r) => r.station_id === s.station_id));  // within 100 m of a shape
const dirName = (d) => (d === 0 ? "northbound" : "southbound");
// routes in a fixed order, each with a fixed colour (validated categorical palette, slots 1-4)
const ROUTES = ["Local Weekday", "Local Weekend", "Limited", "Express"];
const ROUTE_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"];
```

<div class="grid grid-cols-4">
  <div class="card"><h2>Trips</h2><span class="big">${trips.length}</span><br><span class="small muted">${tripStops.length} served stops</span></div>
  <div class="card"><h2>Trip patterns</h2><span class="big">${new Set(trips.map((t) => t.pattern_id)).size}</span><br><span class="small muted">same stations and relative static times</span></div>
  <div class="card"><h2>Stations served</h2><span class="big">${servedStations.size}</span><br><span class="small muted">of ${stations.length} in the feed</span></div>
  <div class="card"><h2>Shapes</h2><span class="big">${new Set(shapes.map((s) => s.shape_id)).size}</span><br><span class="small muted">${shapes.length} points</span></div>
</div>

## Trips per built date

The trips that run on each built date (by the static feed's calendar), by route and
direction.

```js
const routeOf = new Map(trips.map((t) => [t.trip_id, t]));
const dayName = (d) => new Date(`${d}T12:00:00`).toLocaleDateString("en-US", {weekday: "short"});
const perDate = d3.rollups(dateTrips, (v) => v.length, (r) => r.date, (r) => routeOf.get(r.trip_id).route_id,
    (r) => routeOf.get(r.trip_id).direction_id)
  .flatMap(([date, rs]) => rs.flatMap(([route, ds]) => ds.map(([dir, n]) => ({date: `${date} ${dayName(date)}`, route, direction: dirName(dir), n}))));
const totals = d3.rollups(perDate, (v) => d3.sum(v, (r) => r.n), (r) => r.date, (r) => r.direction)
  .flatMap(([date, ds]) => ds.map(([direction, n]) => ({date, direction, n})));
display(Plot.plot({
  width: Math.min(width, 720), height: 70 * new Set(perDate.map((r) => r.date)).size + 80, marginLeft: 110, marginRight: 90,
  x: {label: "trips", grid: true},
  y: {label: null},
  fy: {domain: ["northbound", "southbound"], label: null},
  color: {domain: ROUTES, range: ROUTE_COLORS, legend: true},
  marks: [
    Plot.barX(perDate, {y: "date", x: "n", fy: "direction", fill: "route", inset: 0.5, tip: true}),
    Plot.text(totals, {y: "date", x: "n", fy: "direction", text: "n", dx: 4, textAnchor: "start", fill: "currentColor"}),
    Plot.ruleX([0]),
  ],
}));
```

## Shapes and stations

Every shape's polyline, and the stations within 100 m of a shape (filled: served by some
trip; hollow: on the line but not served).

```js
display(Plot.plot({
  width: Math.min(width, 640), height: 640,
  projection: {type: "mercator", inset: 20, insetRight: 140,
    domain: {type: "MultiPoint", coordinates: onLine.map((s) => [s.lon, s.lat])}},
  marks: [
    Plot.line(shapes, {x: "lon", y: "lat", z: "shape_id", stroke: "currentColor", strokeOpacity: 0.35, strokeWidth: 1.5}),
    Plot.dot(onLine, {x: "lon", y: "lat", r: 4, stroke: "#2a78d6", strokeWidth: 1.5,
      fill: (s) => (servedStations.has(s.station_id) ? "#2a78d6" : "var(--theme-background)"),
      title: (s) => `${s.name}${servedStations.has(s.station_id) ? "" : " (not served)"}`}),
    Plot.text(onLine, {x: "lon", y: "lat", text: (s) => stationName.get(s.station_id), dx: 8, textAnchor: "start", fontSize: 10, fill: "currentColor"}),
  ],
}));
```

## Trip patterns

Trips with the same direction, served stations and static times relative to their first
departure.

```js
const stopsOf = d3.group(tripStops, (r) => r.trip_id);
const patterns = d3.rollups(trips, (v) => {
  const first = d3.sort(stopsOf.get(v[0].trip_id), (r) => r.stop_idx);
  const deps = v.map((t) => d3.min(stopsOf.get(t.trip_id), (r) => r.sched_s));
  const hhmm = (s) => `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}`;
  return {route: v[0].route_id, direction: dirName(v[0].direction_id),
    from: stationName.get(first[0].station_id), to: stationName.get(first.at(-1).station_id), stops: first.length,
    trips: v.length, "first departure": hhmm(d3.min(deps)), "last departure": hhmm(d3.max(deps)),
    "run time (min)": Math.round((first.at(-1).sched_s - first[0].sched_s) / 60)};
}, (t) => t.pattern_id).map(([pattern, r]) => ({pattern, ...r}));
display(Inputs.table(d3.sort(patterns, (p) => p.pattern), {rows: 20}));
```
