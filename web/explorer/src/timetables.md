---
title: GTFS vs GTFS-RT pre-departure timetables
---

<style>#observablehq-main h1 { max-width: none; }</style>

# GTFS vs GTFS-RT pre‑departure timetables

```js
import {rows} from "./components/arrow.js";
import {dirName, shortStationName, routeKm} from "./components/feed.js";
import {timetableTable} from "./components/timetable.js";
import {filterBar, segmented, select, pair} from "./components/controls.js";
const timetable = rows(await FileAttachment("data/timetable.parquet").parquet());
const trips = rows(await FileAttachment("data/trips.parquet").parquet());
const stations = rows(await FileAttachment("data/stations.parquet").parquet());
const shapeStations = rows(await FileAttachment("data/shape_stations.parquet").parquet());
const services = new Map(rows(await FileAttachment("data/services.parquet").parquet()).map((s) => [s.service_id, s.runs]));
```

```js
const stationName = new Map(stations.map((s) => [s.station_id, shortStationName(s.name)]));
const {kmFromSF} = routeKm(shapeStations);
const firstStop = new Map(timetable.filter((r) => r.stop_idx === 0).map((r) => [r.trip_id, r]));
const tripList = trips.filter((t) => firstStop.has(t.trip_id))
  .map((t) => ({trip: t.trip_id, service: t.service_id, direction: dirName(t.direction_id), dep_s: firstStop.get(t.trip_id).gtfs_s}));
const hm = (s) => `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}`;
const serviceTrips = d3.rollup(tripList, (v) => v.length, (r) => r.service);
```

```js
// defaults: the southbound weekday trains 110 to 510 (110, 408, 112, 510)
const runs = (s) => services.get(s) ?? s;
const kind = (s) => (/^Mon–Fri/.test(runs(s)) ? 0 : /^Sat–Sun/.test(runs(s)) ? 1 : 2);
const serviceName = (s) => ["Weekdays", "Weekends"][kind(s)]
  ?? (runs(s).split(", ").length > 2 ? runs(s).split(", ").slice(0, 2).join(", ") + ", …" : runs(s));
const serviceOrder = d3.sort(serviceTrips.keys(), (s) => kind(s), (s) => -serviceTrips.get(s));
const serviceInput = segmented(serviceOrder.filter((s) => kind(s) < 2), {label: serviceName, title: runs,
  more: serviceOrder.filter((s) => kind(s) === 2), moreLabel: "Other"});
const directionInput = segmented(["southbound", "northbound"], {label: (d) => (d === "southbound" ? "SB" : "NB"), title: (d) => d});
const tripSlot = document.createElement("div");  // filled with the first / last trip dropdowns below
display(filterBar([[["Service", serviceInput], ["Direction", directionInput], ["Trips", tripSlot]]]));
const service = Generators.input(serviceInput);
const direction = Generators.input(directionInput);
// the last first / last trips picked, kept when switching service or direction if they're there
const picked = {first: "110", last: "510"};
```

```js
const candidates = d3.sort(tripList.filter((r) => r.service === service && r.direction === direction), (r) => r.dep_s);
const options = candidates.map((r) => ({key: r.trip, label: `${r.trip} · ${hm(r.dep_s)}`}));
const has = (t) => candidates.some((r) => r.trip === t);
const firstInput = select(options, has(picked.first) ? picked.first : candidates[0]?.trip, "first trip");
const lastInput = select(options, has(picked.last) ? picked.last : candidates[Math.min(3, candidates.length - 1)]?.trip, "last trip");
tripSlot.replaceChildren(pair(firstInput, lastInput));
const first = Generators.input(firstInput);
const last = Generators.input(lastInput);
```

```js
picked.first = first; picked.last = last;
const [i, j] = d3.extent([first, last], (t) => candidates.findIndex((r) => r.trip === t));
const shown = i == null || i < 0 ? [] : candidates.slice(i, j + 1).map((r) => r.trip);
const lineOrder = d3.sort([...stationName], ([st]) => (direction === "southbound" ? 1 : -1) * (kmFromSF.get(st) ?? 0));
const shownSet = new Set(shown);
display(shown.length === 0
  ? html`<p class="muted">No ${direction} trips in this service.</p>`
  : timetableTable(timetable.filter((r) => shownSet.has(r.trip_id)), shown, lineOrder));
```
