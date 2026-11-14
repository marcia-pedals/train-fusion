---
title: Pipeline health
---

# Pipeline health

```js
import {rows} from "./components/arrow.js";
import {statusCard, viewToggle} from "./components/statusdays.js";
const fetchViewInput = viewToggle();
const fetchView = Generators.input(fetchViewInput);
const presenceViewInput = viewToggle();
const presenceView = Generators.input(presenceViewInput);
const fetchStatus = rows(await FileAttachment("data/fetch_status.parquet").parquet());
const presence = rows(await FileAttachment("data/data_presence.parquet").parquet());
```

```js
display(statusCard(fetchStatus, {
  title: "Fetcher health", subtitle: "Each day's elapsed service time, by its latest fetch",
  toggle: fetchViewInput, view: fetchView, width,
  statuses: ["fresh", "unknown stamp", "failed", "missing"],
  // blue 700, blue 250, status critical, status serious
  colors: ["#1c5cab", "#86b6ef", "#d03b3b", "#ec835a"],
  labels: {fresh: "successful: fresh content", "unknown stamp": "successful: unknown content stamp",
    failed: "failed fetch", missing: "missing fetch"},
  caption: "Each moment takes the status of the latest fetch within the previous 2.5 min (none: missing).",
}));
```

```js
display(statusCard(presence, {
  title: "Data presence", subtitle: "Service time with a successful fetch, by whether the feed had any data",
  toggle: presenceViewInput, view: presenceView, width,
  statuses: ["present", "absent as expected", "present unexpectedly", "absent unexpectedly"],
  // blue 700, blue 250 (both as expected), status serious, status critical
  colors: ["#1c5cab", "#86b6ef", "#ec835a", "#d03b3b"],
  labels: {present: "data, as expected", "absent as expected": "no data, as expected",
    "present unexpectedly": "data, expected none", "absent unexpectedly": "no data, expected some"},
  caption: "VP is expected empty after midnight.",
}));
```

## Trip updates

How 511's trip updates hold up, trip by trip (stage `tu_checks`).

```js
import {checkCard} from "./components/checkdays.js";
const checks = rows(await FileAttachment("data/tu_checks.parquet").parquet());
const keptViewInput = viewToggle();
const keptView = Generators.input(keptViewInput);
const listedViewInput = viewToggle();
const listedView = Generators.input(listedViewInput);
const h1ViewInput = viewToggle();
const h1View = Generators.input(h1ViewInput);
```

```js
// each discarded entity by why (stage tu_checks' KEPT_FAILURES)
display(checkCard(checks.filter((r) => r.check === "kept").map((r) => ({...r, outcome: r.failure ?? r.outcome})), {
  title: "Discarded trip updates",
  subtitle: "Left out of the checks below",
  toggle: keptViewInput, view: keptView, width, unit: "trip updates", at: "at",
  outcomes: ["ok", "ghost", "not in schedule"],
  // blue 700, then yellow (expected: 511's nightly ghosts) and dark red (not expected)
  colors: ["#1c5cab", "#f2b705", "#9e1b1b"],
  labels: {ok: "kept", ghost: "ghost (all times over 1 h old)", "not in schedule": "trip not in schedule"},
  caption: "South County not counted.",
}));
```

```js
// each failure by how many trip updates the trip is missing from (stage tu_checks' LISTED_FAILURES)
display(checkCard(checks.filter((r) => r.check === "listed").map((r) => ({...r, outcome: r.failure ?? r.outcome})), {
  title: "Trips listed in trip updates",
  subtitle: "In every non-empty trip update from 15 min before departure until its last stop",
  toggle: listedViewInput, view: listedView, width, unit: "trips", at: "first missing at",
  outcomes: ["ok", "missing 1-10", "missing more", "never listed"],
  // blue 700, then yellow to dark red as more is missing
  colors: ["#1c5cab", "#f2b705", "#f28c28", "#9e1b1b"],
  labels: {ok: "in all", "missing 1-10": "missing from 1–10", "missing more": "missing from over 10", "never listed": "missing from all"},
  caption: "",
}));
```

```js
// each failure by its kind (stage tu_checks' H1_FAILURES)
display(checkCard(checks.filter((r) => r.check === "H1").map((r) => ({...r, outcome: r.failure ?? r.outcome})), {
  title: "H1: each trip update lists the rest of the trip",
  subtitle: "Remaining stops in schedule order; until 10 min before departure, from the origin",
  toggle: h1ViewInput, view: h1View, width, unit: "snapshots", at: "snapshot at",
  outcomes: ["ok", "first two swapped", "other two swapped", "ends early", "not from origin", "other"],
  // blue 700, then failures from yellow (the most expected: the train is at one of the two stops) to
  // dark red (the least), lightness falling with severity
  colors: ["#1c5cab", "#f2b705", "#f29a2e", "#e8712a", "#cf4520", "#9e1b1b"],
  labels: {ok: "held", "first two swapped": "first two swapped", "other two swapped": "other two swapped",
    "ends early": "ends early", "not from origin": "not from origin", other: "other"},
  caption: "",
}));
```
