---
title: Trip vs reference
---

# Trip vs reference

One trip's reference trajectory (its segments and dwells chained, starting at the
scheduled departure from the origin) against its fixes. Delay = fix time − reference time
at the fix's position. If the reference really is the fastest possible run, delay should
only ever grow (by dwelling longer, being held for the timetable, or running slower) and
never fall, apart from timing noise.

```js
import {rows, tripTimeDistance, tripDelay, shortName, REF, OBS} from "./components/refplots.js";
const meta = await FileAttachment("data/meta.json").json();
const stationName = new Map(rows(await FileAttachment("data/stations.parquet").parquet()).map((s) => [s.station_id, s.name]));
const refTrips = rows(await FileAttachment("data/ref_trips.parquet").parquet());
const fixesAll = rows(await FileAttachment("data/ref_fixes.parquet").parquet());
const schedule = rows(await FileAttachment("data/schedule.parquet").parquet());
```

```js
const day = view(Inputs.select(meta.dates, {label: "Service date", value: meta.dates[0]}));
```

```js
const dayFixes = fixesAll.filter((f) => f.date === day);
const routeOf = new Map(schedule.filter((r) => r.date === day).map((r) => [r.trip_id, r.route_id]));
const firstDep = d3.rollup(schedule.filter((r) => r.date === day && r.stop_idx === 0), (v) => v[0].t_epoch, (r) => r.trip_id);
const trips = d3.sort([...new Set(dayFixes.filter((f) => f.zone === "line").map((f) => f.trip_id))], (t) => firstDep.get(t));
const nLine = d3.rollup(dayFixes.filter((f) => f.zone === "line"), (v) => v.length, (f) => f.trip_id);
const trip = view(Inputs.select(trips, {label: "Trip (with fixes)", value: d3.greatest(trips, (t) => nLine.get(t)),
  format: (t) => `${t} · ${routeOf.get(t)} · departs ${new Date(firstDep.get(t) * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}`}));
```

```js
const refRows = refTrips.filter((r) => r.date === day && r.trip_id === trip);
const fixes = dayFixes.filter((f) => f.trip_id === trip);
const stops = schedule.filter((r) => r.date === day && r.trip_id === trip)
  .map((r) => ({...r, name: stationName.get(r.station_id) ?? r.station_id}));
```

<span style="color:${REF}">■</span> reference anchored at the scheduled departure ·
<span style="color:${OBS}">●</span> fixes (hollow: repeated coordinate) · dashed grey: the
timetable, straight between stops.

```js
display(resize((width) => tripTimeDistance(refRows, stops, fixes, {width})));
```

## Delay along the trip

Only fixes between the end stops (300 m margin). Vertical lines are the trip's stops.

```js
display(resize((width) => tripDelay(fixes, stops, {width})));
```
