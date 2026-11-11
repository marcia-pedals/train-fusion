# The reference

The **reference** is the fastest a train could possibly run a given stop pattern. Code:
`train_fusion/stages/reference/` (`stage.py`, with `physics.py`, `track.py` and
`timetable_fit.py`; the timetable comes from `train_fusion/stages/timetable/`).

How each choice below was arrived at, and the alternatives that were measured and
dropped (schedule-derived curves, constant dwells, other fits and evaluations), is on the
`reference-experiments` branch (its `docs/reference.md`).

## Definition

- **Segment curve**, keyed by (station 1 → station 2), two consecutive *served* stops.
  21 knots (p, t), evenly spaced in time: the first is (0, 0), the moment the train starts
  moving at station 1; the last is (L, T), the moment it stops at station 2. Time is linear
  between knots. p is the distance from station 1; a trip's own stop spacing rescales it
  (shapes agree to within a few metres).
- **Dwell** W, the same at every station: from stopping to starting to move again.
- A trip's **reference trajectory** chains its segment curves, with W at each intermediate
  stop. It starts at τ = 0 when the train starts moving at the origin; τ(s) is the
  reference time to reach position s (for a position exactly at a stop, the arrival there).

## The segment curves: physics

The minimum-time run from rest to rest (`physics.py`): accelerate and brake at
**1.0 m/s²** (Caltrain KISS gross starting acceleration, Stadler data sheet), never above
the track speed limit. On a 5 m grid the speed is the lowest of the limit, the
acceleration curve from the start and the braking curve back from the stop; time per cell
is exact for constant acceleration.

Speed limits are hardcoded in `track/speed_limits.toml` (79 mph default plus 11
restrictions, each anchored to stations, offsets positive towards SF), taken from
OpenStreetMap `maxspeed` tags on the Peninsula Subdivision (main-line limit where tracks
differ) by `experiments/osm_speed_limits.py`. Crowd-edited, not an official Caltrain
source; our GPS speeds agree with it.

## The dwell: fitted to the timetable

**Timetable as intervals** (stage `timetable`). Static GTFS times are whole minutes,
truncated: [S, S + 60]. 511's trip updates published before a trip starts repeat the
timetable on a 6 s grid: [t, t + 6]. (Of 1486 such values none was a full minute after
its static time; rounding to the nearest 6 s or up would have produced ~74 / ~149. So 511
truncates or is exact; this assumes static and 511 come from the same timetable.) The
interval is [t, t + 6] where 511 gives t, else [S, S + 60]: 511 never lists a trip's final
stop, and trips not seen running yet only have static times. The build checks that the
two describe the same timetable exactly: every trip update gives the same t for a stop,
and S is t truncated to the minute; otherwise it stops.

**Trip patterns.** Trips with the same direction, served stations and static times
relative to their first departure form a pattern (`trips.pattern_id`): at the static
timetable's one-minute resolution they're constant offsets of each other, and they're
expected to share their timetable below a minute too. The build checks that, stop by
stop: relative to each trip's static first departure, every trip of a pattern that 511's
trip updates narrowed at a stop must have exactly the same interval there, and that's the
pattern's interval [lo<sub>g,i</sub>, hi<sub>g,i</sub>]. At a stop no trip was narrowed
at, the static interval is used, the same for all its trips by definition. Trips are
narrowed at different stops: pre-departure updates list the stops in roughly the next 90
minutes and never the terminal, so a date archived only up to some time (or a gap in
fetches) leaves later trips partly at static minutes. If narrowed intervals differ, the
build stops, naming the pattern, stop and trips.

**Fit** (`timetable_fit.py`), a linear program:

```math
\begin{aligned}
\text{minimize}\quad & \sum_{g}\sum_{i=0}^{n_g-2} \varepsilon_{g,i} \\
\text{subject to}\quad & D_{g,i+1} - D_{g,i} = (1+p)\,R_{s(g,i)} + (1-f_{g,i})\,W + \varepsilon_{g,i} && \forall g,\ 0 \le i \le n_g-2 \\
& \varepsilon_{g,i} \ge 0 && \forall g,\ 0 \le i \le n_g-2 \\
& \mathrm{lo}_{g,i} \le D_{g,i} \le \mathrm{hi}_{g,i} && \forall g,\ 0 \le i \le n_g-1
\end{aligned}
```

Variables:
- W: the dwell (s), the same at every stop. This is the reference's dwell.
- p: a recovery allowance, a fraction of running time (reported, not part of the reference).
- D<sub>g,i</sub>: pattern g's scheduled departure from its i-th served stop, relative to
  its first static departure (s).
- ε<sub>g,i</sub>: the padding pattern g puts on its segment from stop i to stop i+1 (s):
  scheduled time beyond running at (1+p)·R and dwelling W.

Constants:
- g ranges over the trip patterns in the static feed; n<sub>g</sub> is the pattern's
  number of served stops.
- s(g, i): the segment from pattern g's i-th to its (i+1)-th served stop.
- R<sub>s</sub>: segment s's physical minimum run time (the segment curve's T, in s).
- f<sub>g,i</sub> = 1 if stop i+1 is the pattern's final stop (its time is an arrival, so
  no dwell follows), else 0.
- lo<sub>g,i</sub>, hi<sub>g,i</sub>: the pattern's timetable interval for
  D<sub>g,i</sub>.

On 2026-11-07/08: **W = 42 s, p = 11%** from 248 trips in 19 patterns (378 paddings; 4
patterns narrowed by 511's trip updates).

## Evaluation against GPS

For two fixes of a trip at (t₀, s₀) and (t₁, s₁):

```
excess = (t₁ − t₀) − (τ(s₁) − τ(s₀))
```

the actual time between them minus the reference time between their positions. No train
can be faster than the reference, so excess should be ≥ 0, up to timing noise.

**Only cruise fixes are used**, so the check doesn't depend on where exactly trains
stop. Stopped trains stand 20–250 m from the GTFS stop point (and the GPS antenna may sit
at one end of the train). A train that stops 150 m further on brakes, dwells and
accelerates exactly as it would at the stop point, just 150 m later; from a point where
the reference cruises before the stop to one where it cruises after it, the time is the
same. A fix is a **cruise fix** if the reference runs at constant speed for 300 m either
side of it (`ref_cruise`, `ref_fixes.cruise`).

`ref_pairs` pairs each usable cruise fix with the trip's next one, however far apart.
Usable: exact content time; more than 300 m from both end stops (trains park up to ~250 m
past the stop at terminals); not a repeated coordinate. About 30% of fixes repeat the
previous fix's coordinate: mostly standing trains, but some are stale positions (the same
coordinate reported twice mid-run, then a 2–3 km jump), so a coordinate only counts when
it first appears. About 20% of fixes are cruise fixes.

Result on 2026-11-07/08 (weekend Locals). Pairs are grouped by how many served stops lie
between the two fixes: **within** a segment (0) tests the curve; **1 stop** tests the
curves plus one whole stop (braking, dwell W, accelerating); **2+ stops** covers segments
too short to have cruise fixes of their own.

| pairs | number of pairs | share with excess < 0 | share with excess < −15 s | minimum excess | 5th percentile of excess | median excess |
|---|---|---|---|---|---|---|
| within a segment | 273 | 15% | 2.6% | −23 s | −10 s | +12 s |
| across 1 stop | 416 | 6.0% | 1.0% | −29 s | −2 s | +33 s |
| across 2+ stops | 250 | 0% | 0% | +6 s | +25 s | +82 s |

## Open

- Weekday data: Express and Limited times are still minute-only, and they're the long
  segments that pin down p.
- Where trains really stand at stops (and the GPS antenna offset): handled by the
  evaluation, not modelled.
