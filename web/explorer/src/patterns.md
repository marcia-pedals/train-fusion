---
title: Trip patterns
---

# Trip patterns

```js
import {rows} from "./components/arrow.js";
import {ROUTES, ROUTE_COLORS, dirName, shortStationName, distinctTripCounter, routeKm} from "./components/feed.js";
import {groupedCheckbox} from "./components/patternpicker.js";
import {stationCode} from "./components/stations.js";
const trips = rows(await FileAttachment("data/trips.parquet").parquet());
const tripStops = rows(await FileAttachment("data/trip_stops.parquet").parquet());
const stations = rows(await FileAttachment("data/stations.parquet").parquet());
const shapeStations = rows(await FileAttachment("data/shape_stations.parquet").parquet());
const dateTrips = rows(await FileAttachment("data/date_trips.parquet").parquet());
const services = new Map(rows(await FileAttachment("data/services.parquet").parquet()).map((s) => [s.service_id, s.runs]));
```

```js
const stationName = new Map(stations.map((s) => [s.station_id, shortStationName(s.name)]));
const stopsOf = d3.group(tripStops, (r) => r.trip_id);
const distinctTrips = distinctTripCounter(stopsOf);
const servedStations = new Set(tripStops.map((r) => r.station_id));
const {refShape, kmFromSF} = routeKm(shapeStations);
```

<div class="grid grid-cols-4">
  <div class="card"><h2>Trip patterns</h2><span class="big">${new Set(trips.map((t) => t.pattern_id)).size}</span></div>
</div>

```js
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
