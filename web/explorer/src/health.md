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

What the pipeline assumes about how 511's trip updates for one trip evolve (stage `tu_checks`). Two have always held and stop the build if they don't: no predicted departure is in the past, and every stop has a departure, with an arrival only at departure − 60 s. Two more sometimes fail, so they're shown below (H1, H2). They only judge trip updates that have the trip, so missing, failed or empty fetches never count against them; the first two cards show which trip updates the checks discard, and when trips are missing from them.

```js
import {checkCard} from "./components/checkdays.js";
const checks = rows(await FileAttachment("data/tu_checks.parquet").parquet());
const h1ViewInput = viewToggle();
const h1View = Generators.input(h1ViewInput);
const keptViewInput = viewToggle();
const keptView = Generators.input(keptViewInput);
const listedViewInput = viewToggle();
const listedView = Generators.input(listedViewInput);
const h2ViewInput = viewToggle();
const h2View = Generators.input(h2ViewInput);
// blue 700, status critical
const CHECK_COLORS = ["#1c5cab", "#d03b3b"];
```

```js
// each discarded entity by why (stage tu_checks' KEPT_FAILURES)
display(checkCard(checks.filter((r) => r.check === "kept").map((r) => ({...r, outcome: r.failure ?? r.outcome})), {
  title: "Discarded trip updates",
  subtitle: "Trip-update entities the checks below leave out",
  toggle: keptViewInput, view: keptView, width, unit: "trip updates", at: "at",
  outcomes: ["ok", "ghost", "not in schedule"],
  // blue 700, then yellow (expected: 511's nightly ghosts) and dark red (not expected)
  colors: ["#1c5cab", "#f2b705", "#9e1b1b"],
  labels: {ok: "kept", ghost: "discarded: ghost (every time over 1 h old)", "not in schedule": "discarded: trip not in the static schedule"},
  caption: "One count per trip in each trip update (each content once, at its first appearance). Ghosts: late-night trips 511 brings back around 03:00 with times from before. South County trips are out of the project's scope and not counted at all. When: a dot per trip update with discarded trips, with how many if more than one.",
}));
```

```js
// each failure by how many trip updates the trip is missing from (stage tu_checks' LISTED_FAILURES)
display(checkCard(checks.filter((r) => r.check === "listed").map((r) => ({...r, outcome: r.failure ?? r.outcome})), {
  title: "Trips listed in trip updates",
  subtitle: "Each trip in every non-empty trip update from 15 min before its scheduled departure until it's due to leave its last listed stop",
  toggle: listedViewInput, view: listedView, width, unit: "trips", at: "first missing at",
  outcomes: ["ok", "missing 1-10", "missing more", "never listed"],
  // blue 700, then yellow to dark red as more is missing
  colors: ["#1c5cab", "#f2b705", "#f28c28", "#9e1b1b"],
  labels: {ok: "in all", "missing 1-10": "missing from 1 to 10", "missing more": "missing from more than 10, listed at least once",
    "never listed": "missing from all"},
  caption: "Expected until 2 min before the scheduled departure of its last listed stop (the trip leaves the list once it has left it, which can be a little early). Each trip update counted once, at its first appearance; completely empty ones aren't counted (see Data presence). When: a dot per trip update at least one trip first went missing from, with how many trips if more than one.",
}));
```

```js
display(checkCard(checks.filter((r) => r.check === "H1"), {
  title: "H1: listed in full before departure",
  subtitle: "When seen 10 to 15 min before its scheduled departure, it lists every stop but the terminus at least once",
  toggle: h1ViewInput, view: h1View, width, unit: "trips", at: "window from",
  outcomes: ["ok", "failed"], colors: CHECK_COLORS, labels: {ok: "held", failed: "failed"},
  caption: "Stops are compared up to 90 min after the snapshot, about as far ahead as 511 lists them. When: a dot at the start of a failing trip's window (15 min before its departure), with how many trips if more than one.",
}));
```

```js
// each failure by its kind (stage tu_checks' H2_FAILURES)
display(checkCard(checks.filter((r) => r.check === "H2").map((r) => ({...r, outcome: r.failure ?? r.outcome})), {
  title: "H2: a snapshot lists the rest of the trip",
  subtitle: "From 15 min before departure, each snapshot of a trip lists its scheduled stops from some stop on, but the terminus",
  toggle: h2ViewInput, view: h2View, width, unit: "snapshots", at: "snapshot at",
  outcomes: ["ok", "first two swapped", "other two swapped", "ends early", "other"],
  // blue 700, then failures from yellow (the most expected: the train is at one of the two stops) to
  // dark red (the least), lightness falling with severity
  colors: ["#1c5cab", "#f2b705", "#f28c28", "#dc4f1e", "#9e1b1b"],
  labels: {ok: "held", "first two swapped": "failed: first two stops swapped", "other two swapped": "failed: two other stops swapped",
    "ends early": "failed: list ends early", other: "failed: other"},
  caption: "One count per snapshot of a trip (each content once, at its first appearance); stops are compared up to 90 min after it. When: a dot per snapshot (and kind) where trips failed, with how many trips if more than one.",
}));
```
