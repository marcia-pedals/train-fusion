---
title: Static feed
---

# Static feed ${meta.static_version}

```js
import {rows} from "./components/arrow.js";
import {groupedCheckbox} from "./components/patternpicker.js";
import {stationCode} from "./components/stations.js";
const meta = await FileAttachment("data/meta.json").json();
const trips = rows(await FileAttachment("data/trips.parquet").parquet());
const tripStops = rows(await FileAttachment("data/trip_stops.parquet").parquet());
const shapes = rows(await FileAttachment("data/shapes.parquet").parquet());
const stations = rows(await FileAttachment("data/stations.parquet").parquet());
const shapeStations = rows(await FileAttachment("data/shape_stations.parquet").parquet());
const dateTrips = rows(await FileAttachment("data/date_trips.parquet").parquet());
const services = new Map(rows(await FileAttachment("data/services.parquet").parquet()).map((s) => [s.service_id, s.runs]));
```

```js
const stationName = new Map(stations.map((s) => [s.station_id, s.name.replace(/ Caltrain( Station)?$/, "").replace(/ Station$/, "")]));
const stopsOf = d3.group(tripStops, (r) => r.trip_id);
// trips with the same first-departure and last-arrival times count once
const distinctTrips = (ts) => new Set(ts.map((t) => {
  const st = stopsOf.get(t.trip_id);
  return `${d3.min(st, (r) => r.sched_s)}-${d3.max(st, (r) => r.sched_s)}`;
})).size;
const servedStations = new Set(tripStops.map((r) => r.station_id));
const onLine = stations.filter((s) => shapeStations.some((r) => r.station_id === s.station_id));  // within 100 m of a shape
const dirName = (d) => (d === 0 ? "northbound" : "southbound");
// routes in a fixed order, each with a fixed colour (validated categorical palette, slots 1-4)
const ROUTES = ["Local Weekday", "Local Weekend", "Limited", "Express"];
const ROUTE_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"];
```

## Trips per service

```js
// one panel per service, largest first: when it runs, and its trips by route and direction
const byService = d3.sort(d3.groups(trips, (t) => t.service_id), ([, ts]) => -ts.length);
const serviceRows = (ts) => d3.rollups(ts, distinctTrips, (t) => t.route_id, (t) => t.direction_id)
  .flatMap(([route, ds]) => ds.map(([dir, n]) => ({route, row: `${route} ${dir === 0 ? "N" : "S"}`, dir, n})));
// one scale for every panel, so bar lengths compare across services
const maxServiceCount = d3.max(byService, ([, ts]) => d3.max(serviceRows(ts), (r) => r.n));
// the same rows in every panel (every route in the feed, both directions), so panels line up
const serviceRowOrder = ROUTES.filter((r) => trips.some((t) => t.route_id === r)).flatMap((r) => [`${r} N`, `${r} S`]);
function serviceChart(ts) {
  const rows = serviceRows(ts);
  const order = serviceRowOrder;
  return Plot.plot({
    width: 300, height: 30 + 20 * order.length, marginLeft: 120, marginRight: 30,
    x: {axis: null, domain: [0, maxServiceCount]}, y: {domain: order, label: null},
    color: {domain: ROUTES, range: ROUTE_COLORS},
    marks: [
      Plot.barX(rows, {y: "row", x: "n", fill: "route", inset: 0.5}),
      Plot.text(rows, {y: "row", x: "n", text: "n", dx: 4, textAnchor: "start", fill: "currentColor"}),
      Plot.ruleX([0]),
    ],
  });
}
display(html`<div class="grid grid-cols-3">${byService.map(([sid, ts]) => html`<div class="card">
  <h2 style="min-height: 3.9em;">${services.get(sid) ?? sid}</h2>
  <div class="small muted" style="margin-bottom: 0.4rem;">${distinctTrips(ts)} trip${distinctTrips(ts) === 1 ? "" : "s"}</div>
  ${serviceChart(ts)}</div>`)}</div>`);
```

## Time-distance diagram of the trip patterns

```js
// common distance axis: station positions on the shape that has the most stations and runs SF <-> Tamien
const byShape = d3.group(shapeStations, (r) => r.shape_id);
const refShape = d3.greatest([...byShape.entries()].filter(([, rs]) =>
  rs.some((r) => r.station_id === "san_francisco") && rs.some((r) => r.station_id === "tamien")), ([, rs]) => rs.length)[1];
const sfS = refShape.find((r) => r.station_id === "san_francisco").s_m;
const kmFromSF = new Map(refShape.map((r) => [r.station_id, Math.abs(r.s_m - sfS) / 1000]));
const patternLines = d3.groups(trips, (t) => t.pattern_id).flatMap(([pattern, ts]) => {
  const stops = d3.sort(stopsOf.get(ts[0].trip_id), (r) => r.stop_idx);
  // time zero: San Francisco southbound, San Jose Diridon northbound, else the first stop
  const zeroStation = ts[0].direction_id === 0 ? "sj_diridon" : "san_francisco";
  const zero = stops.find((r) => r.station_id === zeroStation) ?? stops[0];
  return stops.map((r) => ({pattern, route: ts[0].route_id, direction: dirName(ts[0].direction_id), trips: distinctTrips(ts),
    station: stationName.get(r.station_id), code: stationCode(r.station_id),
    min: (r.sched_s - zero.sched_s) / 60, km: kmFromSF.get(r.station_id)}));
});
const axisStations = d3.sort(refShape.filter((r) => servedStations.has(r.station_id)), (r) => kmFromSF.get(r.station_id));
```

```js
const patternInfo = d3.sort(d3.rollups(patternLines, (v) => v, (l) => l.pattern), ([p]) => p).map(([pattern, v]) => ({pattern,
  route: v[0].route, label: `${pattern} · ${v[0].direction === "northbound" ? "N" : "S"} · ${v[0].code} → ${v.at(-1).code} (${v[0].trips})`}));
const picker = groupedCheckbox(ROUTES.filter((r) => patternInfo.some((p) => p.route === r)).map((r) => ({
  label: r, color: ROUTE_COLORS[ROUTES.indexOf(r)],
  items: patternInfo.filter((p) => p.route === r).map((p) => ({key: p.pattern, label: p.label}))})),
  {label: "Routes and patterns (distinct trips)", vertical: true,
   onSelect: (key) => { picker.show(key); pinned.value = pinned.value === key ? null : key; }});
const shownPatterns = Generators.input(picker);
// the selected pattern: picked by clicking its name in the picker or a dot on the diagram
// (either again to clear); kept while toggling the picker or resizing
const pinned = Mutable(null);
const pin = (pattern) => (pinned.value = pattern);
const patternLabel = new Map(patternInfo.map((p) => [p.pattern, p.label]));
```

```js
picker.setSelected(pinned);  // keep the picker's highlight in step with the selection
```

```js
function patternDiagram(width, shownLines, selected) {
  // with a selection, every pattern is dimmed and the selected one is drawn again on top, thicker
  const dim = selected == null ? 0.8 : 0.2;
  const sel = shownLines.filter((l) => l.pattern === selected);
  const tipText = (l) => `${l.route}\n${patternLabel.get(l.pattern)}\n${l.station} / ${Math.round(l.min)} min / ${Math.round(l.km)} km`;
  const plot = Plot.plot({
    width, height: 600, marginRight: 130,
    x: {label: "minutes since San Francisco (southbound) / San Jose Diridon (northbound)", grid: true,
        domain: d3.extent(patternLines, (l) => l.min)},
    y: {reverse: true, label: "km from San Francisco", grid: false},
    color: {domain: ROUTES, range: ROUTE_COLORS},  // the route checkboxes on the left are the legend
    marks: [
      Plot.ruleY(axisStations, {y: (r) => kmFromSF.get(r.station_id), stroke: "currentColor", strokeOpacity: 0.08}),
      Plot.axisY({anchor: "left", label: "km from San Francisco"}),
      Plot.axisY(axisStations.map((r) => kmFromSF.get(r.station_id)), {anchor: "right", label: null, tickSize: 0, fontSize: 10,
        tickFormat: (km) => stationName.get(axisStations.find((r) => kmFromSF.get(r.station_id) === km).station_id)}),
      Plot.line(shownLines, {x: "min", y: "km", z: "pattern", stroke: "route", strokeWidth: 1.5, strokeOpacity: dim}),
      Plot.line(sel, {x: "min", y: "km", stroke: "route", strokeWidth: 3}),
      // tooltips only on the dots you can pick: all of them, or only the selected pattern's
      Plot.dot(shownLines, {x: "min", y: "km", fill: "route", fillOpacity: selected == null ? 1 : 0.35, r: 2.5,
        ...(selected == null ? {title: tipText, tip: true} : {})}),
      Plot.dot(sel, {x: "min", y: "km", fill: "route", r: 3.5, ...(selected == null ? {} : {title: tipText, tip: true})}),
    ],
  });
  // clicking a dot selects its pattern, or clears the selection if it's already selected
  plot.addEventListener("click", () => { if (plot.value) pin(plot.value.pattern === selected ? null : plot.value.pattern); });
  return plot;
}
```

<div style="display: grid; grid-template-columns: 220px 1fr; gap: 1rem; align-items: start;">
  <div>${picker}</div>
  <div>${resize((width) => patternDiagram(width, patternLines.filter((l) => shownPatterns.includes(l.pattern)), pinned))}</div>
</div>

```js
const hhmmss = (s) => `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}`;
const builtDatesOf = d3.rollup(dateTrips, (v) => v.map((r) => r.date).join(", "), (r) => r.trip_id);
const pinnedTrips = pinned == null ? [] : d3.sort(trips.filter((t) => t.pattern_id === pinned).map((t) => {
  const st = d3.sort(stopsOf.get(t.trip_id), (r) => r.stop_idx);
  return {trip: t.trip_id, departs: `${hhmmss(st[0].sched_s)} ${stationCode(st[0].station_id)}`,
    arrives: `${hhmmss(st.at(-1).sched_s)} ${stationCode(st.at(-1).station_id)}`, dep_s: st[0].sched_s,
    service: t.service_id, runs: services.get(t.service_id) ?? "", "ran on built dates": builtDatesOf.get(t.trip_id) ?? ""};
}), (r) => r.dep_s);
```

```js
display(pinned == null
  ? html`<p class="muted">Click a pattern's name on the left, or a dot on the diagram, to list its trips.</p>`
  : html`<h3>Trips of ${patternLabel.get(pinned)}</h3>${Inputs.table(pinnedTrips, {
      columns: ["trip", "departs", "arrives", "service", "runs", "ran on built dates"],
      width: {trip: 60, departs: 80, arrives: 80, service: 60, "ran on built dates": 170}, rows: 20})}`);
```

## Shapes and stations

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
