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
