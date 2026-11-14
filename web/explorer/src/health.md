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

## Trip-update hypotheses

What the pipeline assumes about how 511's trip updates for one trip evolve (stage `tu_checks`). Two have always held and stop the build if they don't: no predicted departure is in the past, and every stop has a departure, with an arrival only at departure − 60 s. The two below sometimes fail, so they're shown here. Only trip updates are judged: a trip without one while the check applies isn't counted, so missing, failed or empty fetches never count.

```js
import {checkCard} from "./components/checkdays.js";
const checks = rows(await FileAttachment("data/tu_checks.parquet").parquet());
const h1ViewInput = viewToggle();
const h1View = Generators.input(h1ViewInput);
const h2ViewInput = viewToggle();
const h2View = Generators.input(h2ViewInput);
// blue 700, status critical
const CHECK_COLORS = ["#1c5cab", "#d03b3b"];
```

```js
display(checkCard(checks.filter((r) => r.check === "H1"), {
  title: "H1: listed in full before departure",
  subtitle: "When seen 10 to 15 min before its scheduled departure, it lists every stop but the terminus at least once",
  toggle: h1ViewInput, view: h1View, width, unit: "trips", at: "window from",
  outcomes: ["ok", "failed"], colors: CHECK_COLORS, labels: {ok: "held", failed: "failed"},
  caption: "Stops are compared up to 90 min after the snapshot, about as far ahead as 511 lists them. When: a dot per failed trip at the start of its window, 15 min before departure.",
}));
```

```js
// each failure by its kind (stage tu_checks' H2_FAILURES)
display(checkCard(checks.filter((r) => r.check === "H2").map((r) => ({...r, outcome: r.failure ?? r.outcome})), {
  title: "H2: a snapshot lists the rest of the trip",
  subtitle: "From 15 min before departure, each snapshot of a trip lists its scheduled stops from some stop on, but the terminus",
  toggle: h2ViewInput, view: h2View, width, unit: "snapshots", at: "snapshot at",
  outcomes: ["ok", "first two swapped", "other two swapped", "ends early", "other"],
  // blue 700, then red, yellow, aqua (reference categorical slots) and gray for "other"
  colors: ["#1c5cab", "#e34948", "#eda100", "#1baf7a", "#8c8c8c"],
  labels: {ok: "held", "first two swapped": "failed: first two stops swapped", "other two swapped": "failed: two other stops swapped",
    "ends early": "failed: list ends early", other: "failed: other"},
  caption: "One count per snapshot of a trip (each content once, at its first appearance); stops are compared up to 90 min after it. When: a dot per failed snapshot.",
}));
```
