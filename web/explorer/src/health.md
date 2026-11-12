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
