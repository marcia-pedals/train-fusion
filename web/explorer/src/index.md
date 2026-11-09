---
title: Static feed
---

# Static feed ${meta.static_version}

```js
import {rows} from "./components/arrow.js";
import {ROUTES, ROUTE_COLORS, dirName, shortStationName, distinctTripCounter, routeKm} from "./components/feed.js";
const meta = await FileAttachment("data/meta.json").json();
const trips = rows(await FileAttachment("data/trips.parquet").parquet());
const tripStops = rows(await FileAttachment("data/trip_stops.parquet").parquet());
const shapes = rows(await FileAttachment("data/shapes.parquet").parquet());
const stations = rows(await FileAttachment("data/stations.parquet").parquet());
const shapeStations = rows(await FileAttachment("data/shape_stations.parquet").parquet());
const services = new Map(rows(await FileAttachment("data/services.parquet").parquet()).map((s) => [s.service_id, s.runs]));
const platforms = rows(await FileAttachment("data/platforms.parquet").parquet());
```

```js
const stationName = new Map(stations.map((s) => [s.station_id, shortStationName(s.name)]));
const stopsOf = d3.group(tripStops, (r) => r.trip_id);
const distinctTrips = distinctTripCounter(stopsOf);
const servedStations = new Set(tripStops.map((r) => r.station_id));
const {refShape, kmFromSF} = routeKm(shapeStations);
```

```js
const onLine = stations.filter((s) => shapeStations.some((r) => r.station_id === s.station_id));  // within 100 m of a shape
```

## Trips per service

```js
// one group of rows per service (largest first, labelled by when it runs),
// one row per route the service has; northbound above southbound, on the same rows
const byService = d3.sort(d3.groups(trips, (t) => t.service_id), ([, ts]) => -ts.length);
// cut a run-days list at the last whole item (", "-separated) that fits in n characters
const shorten = (text, n = 32) => {
  if (text.length <= n) return text;
  const cut = text.lastIndexOf(", ", n - 2);
  return `${cut > 0 ? text.slice(0, cut) : text.slice(0, n - 1)}, …`;
};
const serviceRows = byService.flatMap(([sid, ts]) => {
  const routes = ROUTES.filter((r) => ts.some((t) => t.route_id === r));
  return routes.map((route, i) => ({sid, route, row: `${sid}|${route}`, first: i === 0, last: i === routes.length - 1,
    service: shorten(services.get(sid) ?? sid)}));
});
const serviceCounts = byService.flatMap(([sid, ts]) =>
  d3.rollups(ts, distinctTrips, (t) => t.route_id, (t) => t.direction_id)
    .flatMap(([route, ds]) => ds.map(([dir, n]) => ({row: `${sid}|${route}`, runs: services.get(sid) ?? sid,
      route, direction: dirName(dir), n}))));
const rowRoute = new Map(serviceRows.map((r) => [r.row, r.route]));
display(Plot.plot({
  width: Math.min(width, 900), height: 70 + 2 * 18 * serviceRows.length, marginLeft: 330, marginRight: 90,
  x: {label: "trips", grid: true, domain: [0, 1.1 * d3.max(serviceCounts, (d) => d.n)]},  // room for the counts
  y: {domain: serviceRows.map((r) => r.row), label: null, tickFormat: (row) => rowRoute.get(row), tickSize: 0},
  fy: {domain: ["northbound", "southbound"], label: null},
  color: {domain: ROUTES, range: ROUTE_COLORS},
  marks: [
    // the service label once per group, left of its route labels; a faint line between groups
    Plot.text(serviceRows.filter((r) => r.first), {y: "row", text: "service", frameAnchor: "left", dx: -105,
      textAnchor: "end", fill: "currentColor", fontWeight: 600}),
    Plot.ruleY(serviceRows.filter((r) => r.last), {y: "row", dy: 9, stroke: "currentColor", strokeOpacity: 0.12, insetLeft: -320}),
    Plot.barX(serviceCounts, {x: "n", y: "row", fy: "direction", fill: "route", inset: 0.5,
      title: (d) => `${d.runs}\n${d.route} ${d.direction}: ${d.n} trip${d.n === 1 ? "" : "s"}`, tip: true}),
    Plot.text(serviceCounts, {x: "n", y: "row", fy: "direction", text: "n", dx: 4, textAnchor: "start", fill: "currentColor"}),
    Plot.ruleX([0]),
  ],
}));
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
