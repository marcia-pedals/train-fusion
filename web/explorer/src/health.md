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

What the pipeline assumes about how 511's trip updates for one trip evolve (stage `tu_checks`). Two have always held and stop the build if they don't: no predicted departure is in the past, and every stop has a departure, with an arrival only at departure − 60 s. The two below fail for a few trips a day, so they're shown here: one count per scheduled trip. A trip is only judged when it has a trip update while the check applies, so missing, failed or empty fetches never count, and trips without one aren't shown.

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
  toggle: h1ViewInput, view: h1View, width, failures: "failures",
  outcomes: ["ok", "failed"], colors: CHECK_COLORS, labels: {ok: "held", failed: "failed"},
  caption: "Stops are compared up to 90 min after the snapshot, about as far ahead as 511 lists them. When: a dot per trip at the start of its window, 15 min before departure.",
}));
```

```js
display(checkCard(checks.filter((r) => r.check === "H2"), {
  title: "H2: stops only drop off the front",
  subtitle: "From 15 min before departure, each snapshot lists the previous one's stops, or those minus a prefix",
  toggle: h2ViewInput, view: h2View, width, failures: "non-monotone changes",
  outcomes: ["ok", "failed"], colors: CHECK_COLORS, labels: {ok: "held", failed: "failed"},
  caption: "Snapshots without the trip are allowed in between. When: a dot per trip at its first failure, or 15 min before departure if none.",
}));
```
