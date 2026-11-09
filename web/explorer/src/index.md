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
const platforms = rows(await FileAttachment("data/platforms.parquet").parquet());
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
// one line per service (largest first), labelled by when it runs; a bar per direction, split by route
const byService = d3.sort(d3.groups(trips, (t) => t.service_id), ([, ts]) => -ts.length);
// cut a run-days list at the last whole item (", "-separated) that fits in n characters
const shorten = (text, n = 32) => {
  if (text.length <= n) return text;
  const cut = text.lastIndexOf(", ", n - 2);
  return `${cut > 0 ? text.slice(0, cut) : text.slice(0, n - 1)}, …`;
};
const serviceLabel = new Map(byService.map(([sid, ts]) => [sid, `${shorten(services.get(sid) ?? sid)} · ${distinctTrips(ts)}`]));
const serviceCounts = byService.flatMap(([sid, ts]) =>
  d3.rollups(ts, distinctTrips, (t) => t.route_id, (t) => t.direction_id)
    .flatMap(([route, ds]) => ds.map(([dir, n]) => ({service: serviceLabel.get(sid), runs: services.get(sid) ?? sid,
      route, direction: dirName(dir), n}))));
// the route legend, vertical, to the right of the chart
const routeLegend = html`<div style="display: flex; flex-direction: column; gap: 0.3rem; font: 12px var(--sans-serif); padding-top: 1.5rem;">
  ${ROUTES.map((r, i) => html`<div style="display: flex; align-items: center; gap: 0.4rem;">
    <span style="width: 12px; height: 12px; background: ${ROUTE_COLORS[i]}; border-radius: 2px;"></span>${r}</div>`)}</div>`;
display(html`<div style="display: flex; gap: 1.5rem; align-items: flex-start;">${Plot.plot({
  width: Math.min(width, 1100) - 140, height: 50 + 24 * byService.length, marginLeft: 250,
  x: {label: "trips", grid: true},
  y: {domain: byService.map(([sid]) => serviceLabel.get(sid)), label: null},
  fx: {domain: ["northbound", "southbound"], label: null},
  color: {domain: ROUTES, range: ROUTE_COLORS},
  marks: [
    Plot.barX(serviceCounts, {x: "n", y: "service", fx: "direction", fill: "route", order: ROUTES, inset: 0.5,
      title: (d) => `${d.runs}\n${d.route} ${d.direction}: ${d.n} trip${d.n === 1 ? "" : "s"}`, tip: true}),
    Plot.ruleX([0]),
  ],
})}${routeLegend}</div>`);
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
// The map, rotated so San Francisco -> Tamien runs left to right, as wide as the page. A
// "route km" axis on top has a tick per station (km from San Francisco along the line) with a
// dashed line down to the station. Line: every shape; dots: stations (hollow = not served);
// small dots: platforms.
function corridorMap(width) {
  const margin = {top: 150, right: 70, bottom: 20, left: 20};
  const shapeLines = d3.groups(shapes, (p) => p.shape_id).map(([, ps]) =>
    ({type: "LineString", coordinates: d3.sort(ps, (p) => p.pt_seq).map((p) => [p.lon, p.lat])}));
  const all = {type: "FeatureCollection", features: shapeLines.map((g) => ({type: "Feature", geometry: g}))};
  const at = (id) => { const s = stations.find((x) => x.station_id === id); return [s.lon, s.lat]; };
  // rotate so SF -> Tamien is horizontal, SF on the left
  const base = d3.geoMercator();
  const [a, b] = [base(at("san_francisco")), base(at("tamien"))];
  const projection = d3.geoMercator().angle(Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI)
    .fitWidth(width - margin.left - margin.right, all);
  const [[, y0], [, y1]] = d3.geoPath(projection).bounds(all);
  projection.translate([projection.translate()[0] + margin.left, projection.translate()[1] - y0 + margin.top]);
  const height = margin.top + (y1 - y0) + margin.bottom;
  const path = d3.geoPath(projection);
  const stationPts = onLine.map((s) => ({...s, xy: projection([s.lon, s.lat]), km: kmFromSF.get(s.station_id),
    served: servedStations.has(s.station_id)}));
  const axisY = 125;  // the angled station labels need ~110 px above the axis
  const svg = d3.create("svg").attr("width", width).attr("height", height).attr("viewBox", [0, 0, width, height])
    .style("font", "10px var(--sans-serif)").style("overflow", "visible");
  // route km axis, its ticks and the dashed lines down to the stations
  svg.append("text").attr("x", d3.max(stationPts, (s) => s.xy[0]) + 8).attr("y", axisY + 4)
    .attr("fill", "currentColor").text("route km");
  svg.append("line").attr("x1", d3.min(stationPts, (s) => s.xy[0])).attr("x2", d3.max(stationPts, (s) => s.xy[0]))
    .attr("y1", axisY).attr("y2", axisY).attr("stroke", "currentColor");
  const tick = svg.append("g").selectAll("g").data(stationPts.filter((s) => s.km != null)).join("g");
  tick.append("line").attr("x1", (s) => s.xy[0]).attr("x2", (s) => s.xy[0]).attr("y1", axisY).attr("y2", (s) => s.xy[1])
    .attr("stroke", "currentColor").attr("stroke-opacity", 0.3).attr("stroke-dasharray", "2,3");
  tick.append("line").attr("x1", (s) => s.xy[0]).attr("x2", (s) => s.xy[0]).attr("y1", axisY - 4).attr("y2", axisY)
    .attr("stroke", "currentColor");
  tick.append("text").attr("transform", (s) => `translate(${s.xy[0]},${axisY - 7}) rotate(-50)`)
    .attr("fill", "currentColor").text((s) => `${stationName.get(s.station_id)} ${s.km.toFixed(1)}`);
  // shapes, platforms, stations
  svg.append("g").selectAll("path").data(shapeLines).join("path").attr("d", path)
    .attr("fill", "none").attr("stroke", "currentColor").attr("stroke-opacity", 0.35).attr("stroke-width", 1.5);
  svg.append("g").selectAll("circle").data(stationPts).join("circle")
    .attr("cx", (s) => s.xy[0]).attr("cy", (s) => s.xy[1]).attr("r", 4).attr("stroke", "#2a78d6").attr("stroke-width", 1.5)
    .attr("fill", (s) => (s.served ? "#2a78d6" : "var(--theme-background)"))
    .append("title").text((s) => `${s.name}${s.served ? "" : " (not served)"}`);
  // platforms on top, so the ones a few px from their station show
  svg.append("g").selectAll("circle").data(platforms.filter((p) => onLine.some((s) => s.station_id === p.station_id))).join("circle")
    .attr("cx", (p) => projection([p.lon, p.lat])[0]).attr("cy", (p) => projection([p.lon, p.lat])[1]).attr("r", 2.5)
    .attr("fill", "#eb6834").append("title").text((p) => `platform ${p.stop_id} · ${stationName.get(p.station_id)}`);
  return svg.node();
}
display(resize((width) => corridorMap(width)));
```
