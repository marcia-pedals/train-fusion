"""Stage `tu_checks`: check what we assume about how 511's trip updates (TU) for one trip
evolve, on every date being built. All in terms of departure times (H4 is why that's safe).

H1  Wherever the trip is seen with content time in [dep - 15 min, dep - 10 min] (dep: its
    scheduled first departure), at least once it lists, within the horizon (below), every
    scheduled stop but the terminus, in order.
H2  From dep - 15 min on, every snapshot listing the trip lists, within the horizon, a suffix
    of its scheduled stops but the terminus: from some stop on, every one of them, in order.
    Judged per snapshot (each content once, at its first appearance). A failure is one of
    H2_FAILURES: the first two listed stops swapped, two other adjacent ones swapped, the
    list ending short of the horizon, or anything else.
H3  No departure is in the past (before the snapshot's content time).
H4  Every stop has a departure, and an arrival only at exactly departure - 60 s.

H3 and H4 have held on all data so far: a violation raises TuHypothesisViolation, which stops
the build. H1 and H2 sometimes fail: each trip's H1 outcome and each snapshot's H2 outcome go
to <date>/tu_checks.parquet, for the Pipeline health page. (Exploration that led here: branch
tu-exploration, experiments/tu_hypotheses.py.)

The horizon: 511 lists a trip's stops only up to ~90 min after now, but before departure
sometimes lists all of them (every stop but the terminus) instead, alternating between the
two. So H1 and H2 compare stop lists only up to HORIZON_S after the snapshot. An expected stop a snapshot doesn't list is placed at its
scheduled time plus the delay of the nearest listed stop before it. A difference that goes
away when stops within BOUNDARY_S of the cut are ignored isn't a failure.

Ghosts are discarded before any check: entities whose departures are all more than GHOST_S
before the snapshot (late-night trips 511 brings back around 03:00 with ~95 min old times).

Missing / failed fetches never count against a hypothesis:
- H1 and H2 only judge snapshots that have the trip: a trip never seen when the check applies
  (whether its fetches failed, were empty or just didn't list it, or it's outside the
  archive) isn't judged, and has no row for that check.
- H2: a trip missing from a snapshot whose whole feed is empty isn't a dropout.
- H3: an inexact content time (the raw stamp, up to INEXACT_S late) only puts a departure
  definitely in the past when it's more than INEXACT_S before the stamp.

Snapshots are every successful TU fetch, including those whose content repeats an earlier
fetch (decode drops those from tu.parquet; here they take the content of the first fetch with
the same digest), so an A, B, A flip-flop is seen. H3 and H4 are judged once per content, at
its first appearance. An entity is assigned to the scheduled trip (trip_id, service date)
whose times are nearest its predictions; trip_ids not in the static feed (South County) are
ignored.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import polars as pl

from train_fusion.common import paths
from train_fusion.common.timeutil import local_hms, service_day_origin
from train_fusion.stages.decode.schemas import FETCHES, TU
from train_fusion.stages.static.stage import Static
from train_fusion.stages.tu_checks.schemas import TU_CHECKS

H1_WINDOW_S = (900.0, 600.0)  # [dep - 15 min, dep - 10 min]
H2_FROM_S = 900.0  # H2 applies from dep - 15 min
DWELL_S = 60  # H4
HORIZON_S = 5400.0  # 511 lists stops up to ~90 min ahead
BOUNDARY_S = 120.0
GHOST_S = 3600.0
INEXACT_S = 15.0
MAX_EXAMPLES = 5  # in a TuHypothesisViolation's message

Stops = list[tuple[str, int | None, int | None]]  # [(stop_id, arr_epoch, dep_epoch)] in listed order
Timed = list[tuple[str, float]]  # [(stop_id, time)]


class TuHypothesisViolation(ValueError):
    """511's trip updates broke a hypothesis that has always held (H3 or H4)."""


@dataclass
class Snap:
    """One successful TU fetch."""
    t: float  # content time
    exact: bool
    n_entities: int
    first: bool  # the first fetch with this content
    trips: dict[str, Stops]


@dataclass
class Trip:
    """One scheduled trip on one service date."""
    date: str
    trip_id: str
    stops: list[str]
    sched: dict[str, float]  # stop_id -> scheduled departure, epoch s
    dep0: float
    obs: list[tuple[int, Stops]] = field(default_factory=list)  # (snapshot index, its stops)


def load_snaps(dates: list[str]) -> list[Snap]:
    """Every successful TU fetch of these archive dates, in request order (a fetch of a valid
    but empty feed counts, with no trips, timed by its request)."""
    out = []
    for d in dates:
        f = FETCHES.read(paths.day_dir(d) / "fetches.parquet").filter(pl.col("feed") == "tripupdates")
        tu = TU.read(paths.day_dir(d) / "tu.parquet").sort("file", "trip_id", "stu_idx")
        by_file: dict[str, dict[str, Stops]] = defaultdict(lambda: defaultdict(list))
        for file, trip, stop, arr, dep in tu.select("file", "trip_id", "stop_id", "arr_epoch", "dep_epoch").iter_rows():
            by_file[file][trip].append((stop, arr, dep))
        first = {r["content_sha256"]: r for r in f.filter(pl.col("ok") & ~pl.col("duplicate")).iter_rows(named=True)}
        for r in f.sort("request_epoch").iter_rows(named=True):
            if r["ok"]:
                o = first[r["content_sha256"]]
                out.append((r["request_epoch"], Snap(o["content_epoch"], o["content_exact"], r["n_entities"],
                                                     not r["duplicate"], dict(by_file.get(o["file"], {})))))
            elif r["status"] == 200 and r["content_sha256"] is not None and r["n_entities"] == 0:
                out.append((r["request_epoch"], Snap(r["request_epoch"], False, 0, True, {})))
    return [s for _, s in sorted(out, key=lambda x: x[0])]


def load_trips(dates: list[str]) -> tuple[dict[str, list[Trip]], dict[str, str]]:
    """The scheduled trips of these dates by trip_id, and stop_id -> short station name."""
    trips, names = defaultdict(list), {}
    for d in dates:
        st, origin = Static(d), service_day_origin(d)
        station = dict(st.stations.select("station_id", "name").iter_rows())
        for sid, stn in st.platforms.select("stop_id", "station_id").iter_rows():
            names[sid] = station[stn].removesuffix(" Caltrain Station").removesuffix(" Station")
        for trip_id in sorted(st.active_trip_ids):
            s = st.stops_by_trip.get(trip_id)
            if s is not None:
                stops = s["stop_id"].to_list()
                sched = {sid: origin + x for sid, x in zip(stops, s["sched_s"].to_list())}
                trips[trip_id].append(Trip(d, trip_id, stops, sched, sched[stops[0]]))
    return trips, names


def _time(arr, dep) -> float:
    return dep if dep is not None else arr


def assign(trips: list[Trip], stops: Stops) -> Trip:
    """The scheduled trip an entity's predictions belong to: nearest scheduled times."""
    def miss(x: Trip) -> float:
        diffs = [abs(_time(a, d) - x.sched[s]) for s, a, d in stops if s in x.sched and _time(a, d) is not None]
        return float(np.median(diffs)) if diffs else abs((_time(*stops[0][1:]) or x.dep0) - x.dep0)
    return min(trips, key=miss)


def timed(stops: Stops) -> Timed:
    return [(s, _time(a, d)) for s, a, d in stops]


def expected(x: Trip, stops: Stops) -> Timed:
    """Every stop of x but the terminus with a time: listed, else scheduled plus the delay of
    the nearest listed stop before it."""
    got, delay, out = dict(timed(stops)), 0.0, []
    for s in x.stops[:-1]:
        if s in got:
            delay = got[s] - x.sched[s]
            out.append((s, got[s]))
        else:
            out.append((s, x.sched[s] + delay))
    return out


def within(lst: Timed, cut: float, margin: float = 0.0) -> list[str]:
    """The stops at most `cut`, leaving out those within `margin` of it."""
    return [s for s, t in lst if t <= cut and not abs(t - cut) <= margin]


def check_h3_h4(snaps: list[Snap], obs: list[tuple[int, Stops, Trip]], names: dict[str, str]) -> dict:
    """Raises TuHypothesisViolation on any H3 or H4 failure; counts what was checked."""
    bad: list[str] = []
    n = 0
    for i, stops, x in obs:
        s = snaps[i]
        if not s.first:
            continue
        slack = 0.0 if s.exact else INEXACT_S
        for pos, (stop, arr, dep) in enumerate(stops):
            n += 1
            where = (f"{x.date} trip {x.trip_id} snapshot {local_hms(s.t)}{'' if s.exact else ' (inexact)'} "
                     f"stop #{pos} {names.get(stop, stop)}")
            if dep is None:
                bad.append(f"H4: {where} has no departure")
            elif arr is not None and dep - arr != DWELL_S:
                bad.append(f"H4: {where}: arrival {local_hms(arr)}, departure {local_hms(dep)}")
            if dep is not None and s.t - dep > slack:
                bad.append(f"H3: {where}: departure {local_hms(dep)}, {s.t - dep:.0f} s in the past")
    if bad:
        raise TuHypothesisViolation(f"{len(bad)} trip-update stops break H3 / H4, e.g.\n  "
                                    + "\n  ".join(bad[:MAX_EXAMPLES]))
    return {"stops": n}


def check_h1(x: Trip, snaps: list[Snap], names: dict[str, str]) -> dict | None:
    """None if the trip isn't seen in the window."""
    lo, hi = x.dep0 - H1_WINDOW_S[0], x.dep0 - H1_WINDOW_S[1]
    row = {"check": "H1", "at_epoch": lo, "failure": None, "detail": ""}
    seen = [(i, st) for i, st in x.obs if lo <= snaps[i].t <= hi]
    if not seen:
        return None
    for i, st in seen:
        cut = snaps[i].t + HORIZON_S
        if any(within(timed(st), cut, m) == within(expected(x, st), cut, m) for m in (0.0, BOUNDARY_S)):
            return row | {"outcome": "ok"}
    i, st = seen[-1]
    cut = snaps[i].t + HORIZON_S
    got, want = within(timed(st), cut), within(expected(x, st), cut)
    missing = ", ".join(names.get(s, s) for s in want if s not in got)
    return row | {"outcome": "failed",
                  "detail": f"seen {len(seen)}× in its window, never with every stop; at {local_hms(snaps[i].t)} "
                            f"{len(got)} of {len(want)} stops, missing {missing or 'none (out of order)'}"}


def _not_a_suffix(got: list[str], want: list[str], names: dict[str, str]) -> tuple[str, str]:
    """How a stop list that isn't a suffix of `want` differs from the nearest one: its kind (an
    H2 failure kind) and in words."""
    name = lambda n: names.get(n, n)
    if bad := [n for n in got if n not in want]:
        return "other", f"lists {', '.join(map(name, bad))}, not a scheduled stop before the terminus"
    k = min(want.index(n) for n in got)  # the suffix from the earliest scheduled stop listed
    expect = [n for n in want[k:] if want.index(n) <= max(want.index(n) for n in got)]
    if got == expect:  # a suffix, but cut short of the horizon
        return "ends early", f"ends at {name(got[-1])}, short of the 90 min horizon"
    for j in range(len(got) - 1):  # two adjacent stops swapped, nothing else
        if got[:j] + [got[j + 1], got[j]] + got[j + 2:] == expect:
            return ("first two swapped" if j == 0 else "other two swapped",
                    f"{name(got[j])} listed before {name(got[j + 1])} (stops {j + 1} and {j + 2})")
    missing = [n for n in expect if n not in got]
    inv = next(((a, b) for a, b in zip(got, got[1:]) if want.index(a) > want.index(b)), None)
    return "other", ", and ".join(([f"missing {', '.join(map(name, missing))}"] if missing else [])
                                  + ([f"out of order: {name(inv[0])} before {name(inv[1])}"] if inv else []))


def check_h2(x: Trip, snaps: list[Snap], names: dict[str, str]) -> list[dict]:
    """One row per snapshot of the trip from dep - 15 min on (each content once)."""
    want = x.stops[:-1]
    rows = []
    for i, st in x.obs:
        s = snaps[i]
        if s.t < x.dep0 - H2_FROM_S or not s.first or not st:
            continue
        row = {"check": "H2", "at_epoch": s.t, "failure": None, "detail": ""}
        got, cut = timed(st), s.t + HORIZON_S
        first = got[0][0]
        if first in want:  # the suffix from the first listed stop
            suffix = expected(x, st)[want.index(first):]
            if any(within(got, cut, m) == within(suffix, cut, m) for m in (0.0, BOUNDARY_S)):
                rows.append(row | {"outcome": "ok"})
                continue
        kind, what = _not_a_suffix(within(got, cut) or [n for n, _ in got], want, names)
        listed = ", ".join(names.get(n, n) for n, _ in got[:8]) + (", …" if len(got) > 8 else "")
        rows.append(row | {"outcome": "failed", "failure": kind,
                           "detail": f"{(s.t - x.dep0) / 60:+.0f} min from departure: {what} ({len(got)} listed: {listed})"})
    return rows


def check(dates: list[str]) -> tuple[pl.DataFrame, dict]:
    """H1 outcomes of every scheduled trip of these dates and H2 outcomes of their snapshots; raises
    on H3 / H4."""
    snaps = load_snaps(dates)
    trips, names = load_trips(dates)
    obs = []  # (snapshot index, stops, trip) of every non-ghost entity of a scheduled trip
    for i, s in enumerate(snaps):
        for trip_id, stops in s.trips.items():
            if trip_id not in trips:
                continue
            if all(_time(a, d) < s.t - GHOST_S for _, a, d in stops):
                continue
            x = assign(trips[trip_id], stops)
            x.obs.append((i, stops))
            obs.append((i, stops, x))
    info = check_h3_h4(snaps, obs, names)
    rows = []
    for x in sorted((x for v in trips.values() for x in v), key=lambda x: x.dep0):
        h1 = check_h1(x, snaps, names)
        for r in ([h1] if h1 else []) + check_h2(x, snaps, names):
            rows.append({"date": x.date, "trip_id": x.trip_id, "dep0_epoch": x.dep0} | r)
    df = TU_CHECKS.conform(pl.DataFrame(rows))
    for h in ("H1", "H2"):
        c = df.filter(pl.col("check") == h)["outcome"].value_counts()
        info[h] = dict(c.iter_rows())
    return df, info


def run(dates: list[str]) -> dict:
    """Check every date; write each date's H1 / H2 outcomes to <date>/tu_checks.parquet."""
    df, info = check(dates)
    for d in dates:
        TU_CHECKS.write(df.filter(pl.col("date") == d), paths.day_dir(d) / "tu_checks.parquet")
    return info
