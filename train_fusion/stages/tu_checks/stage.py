"""Stage `tu_checks`: check what we assume about how 511's trip updates (TU) for one trip
evolve, on every date being built. All in terms of departure times (H3 is why that's safe).

H1  From dep - 15 min on (dep: its scheduled first departure), every snapshot listing the
    trip lists, within the horizon (below), a suffix of its scheduled stops but the terminus:
    from some stop on, every one of them, in order; and up to dep - 10 min that suffix starts
    at the origin (all of them). Judged per snapshot (each content once, at its first
    appearance). A failure is one of TU_H1's outcomes: the first two listed stops swapped, two other
    adjacent ones swapped, the list ending short of the horizon, not starting at the origin
    by dep - 10 min, or anything else.
H2  No departure is in the past (before the snapshot's content time).
H3  Every stop has a departure, and an arrival only at exactly departure - 60 s.

Since H1 only judges trip updates that have the trip, another check ("listed") says when
a trip is missing from them: each trip is expected in every trip update (each content once, at
its first appearance) that isn't completely empty (that's the Pipeline health page's Data
presence), from dep - 15 min until LISTED_END_S before the scheduled departure of its last
listed stop (the one before the terminus; the trip leaves the list when it departs that, which
can be a little early). Its outcome: listed in all of them, or missing from 1 to
LISTED_FEW of them, from more but not all, or from all.

And one more ("kept") says which trip-update entities the checks discard: each entity (each
content once, at its first appearance) is kept, or discarded as a ghost (below) or as a trip
that isn't in the static schedule of the dates built. (South County trips never get here:
decode drops them.)

H2 and H3 have held on all data so far: a violation raises TuHypothesisViolation, which stops
the build. H1 sometimes fails: each snapshot's H1 outcome goes to <date>/tu_h1.parquet (and
"listed" to tu_listed, "kept" to tu_kept), for the Pipeline health page. (Exploration that led here: branch
tu-exploration, experiments/tu_hypotheses.py.)

The horizon: 511 lists a trip's stops only up to ~90 min after now, but before departure
sometimes lists all of them (every stop but the terminus) instead, alternating between the
two. So H1 compares stop lists only up to HORIZON_S after the snapshot. An expected stop a
snapshot doesn't list is placed at its scheduled time plus the delay of the nearest listed stop
before it. A difference that goes
away when stops within BOUNDARY_S of the cut are ignored isn't a failure.

Ghosts are discarded before any check: entities whose departures are all more than GHOST_S
before the snapshot (late-night trips 511 brings back around 03:00 with ~95 min old times).

Missing / failed fetches never count against a hypothesis:
- H1 only judges snapshots that have the trip: a trip never seen when the check applies
  (whether its fetches failed, were empty or just didn't list it, or it's outside the
  archive) isn't judged, and has no row for that check ("listed" covers it).
- H2: an inexact content time (the raw stamp, up to INEXACT_S late) only puts a departure
  definitely in the past when it's more than INEXACT_S before the stamp.

Snapshots are every successful TU fetch, including those whose content repeats an earlier
fetch (decode drops those from tu.parquet; here they take the content of the first fetch with
the same digest), so an A, B, A flip-flop is seen. H2 and H3 are judged once per content, at
its first appearance. An entity is assigned to the scheduled trip (trip_id, service date)
whose times are nearest its predictions.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import polars as pl

from train_fusion.common import paths
from train_fusion.common.schema_dsl import Table
from train_fusion.common.timeutil import local_hms, service_date, service_day_origin
from train_fusion.stages.decode.schemas import FETCHES, TU
from train_fusion.stages.static.stage import Static
from train_fusion.stages.tu_checks.schemas import TU_H1, TU_KEPT, TU_LISTED

H1_FROM_S = 900.0  # H1 applies from dep - 15 min
H1_ORIGIN_UNTIL_S = 600.0  # and up to dep - 10 min the list starts at the origin
DWELL_S = 60  # H3
HORIZON_S = 5400.0  # 511 lists stops up to ~90 min ahead
BOUNDARY_S = 120.0
GHOST_S = 3600.0
INEXACT_S = 15.0
LISTED_END_S = 120.0  # "listed": expected until this long before the last listed stop's departure
LISTED_FEW = 10  # "listed": up to this many misses are "missing 1-10"
MAX_EXAMPLES = 5  # in a TuHypothesisViolation's message

Stops = list[tuple[str, int | None, int | None]]  # [(stop_id, arr_epoch, dep_epoch)] in listed order
Timed = list[tuple[str, float]]  # [(stop_id, time)]


class TuHypothesisViolation(ValueError):
    """511's trip updates broke a hypothesis that has always held (H2 or H3)."""


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


def check_h2_h3(snaps: list[Snap], obs: list[tuple[int, Stops, Trip]], names: dict[str, str]) -> dict:
    """Raises TuHypothesisViolation on any H2 or H3 failure; counts what was checked."""
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
                bad.append(f"H3: {where} has no departure")
            elif arr is not None and dep - arr != DWELL_S:
                bad.append(f"H3: {where}: arrival {local_hms(arr)}, departure {local_hms(dep)}")
            if dep is not None and s.t - dep > slack:
                bad.append(f"H2: {where}: departure {local_hms(dep)}, {s.t - dep:.0f} s in the past")
    if bad:
        raise TuHypothesisViolation(f"{len(bad)} trip-update stops break H2 / H3, e.g.\n  "
                                    + "\n  ".join(bad[:MAX_EXAMPLES]))
    return {"stops": n}


def _not_a_suffix(got: list[str], want: list[str], names: dict[str, str]) -> tuple[str, str]:
    """How a stop list that isn't a suffix of `want` differs from the nearest one: its kind (an
    H1 failure kind) and in words."""
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


def check_h1(x: Trip, snaps: list[Snap], names: dict[str, str]) -> list[dict]:
    """One row per snapshot of the trip from dep - 15 min on (each content once)."""
    want = x.stops[:-1]
    rows = []
    for i, st in x.obs:
        s = snaps[i]
        if s.t < x.dep0 - H1_FROM_S or not s.first or not st:
            continue
        row = {"date": x.date, "trip_id": x.trip_id, "dep0_epoch": x.dep0, "t_epoch": s.t, "detail": ""}
        got, cut = timed(st), s.t + HORIZON_S
        first = got[0][0]
        if first in want:  # the suffix from the first listed stop (from the origin, up to dep - 10 min)
            suffix = expected(x, st)[want.index(first):]
            if any(within(got, cut, m) == within(suffix, cut, m) for m in (0.0, BOUNDARY_S)):
                if first == want[0] or s.t > x.dep0 - H1_ORIGIN_UNTIL_S:
                    rows.append(row | {"outcome": "ok"})
                    continue
                k = want.index(first)
                skipped = ", ".join(names.get(n, n) for n in want[:k]) if k <= 3 else f"the first {k} stops"
                kind, what = "not from origin", f"starts at {names.get(first, first)}, leaving out {skipped}"
            else:
                kind, what = _not_a_suffix(within(got, cut) or [n for n, _ in got], want, names)
        else:
            kind, what = _not_a_suffix(within(got, cut) or [n for n, _ in got], want, names)
        listed = ", ".join(names.get(n, n) for n, _ in got[:8]) + (", …" if len(got) > 8 else "")
        rows.append(row | {"outcome": kind,
                           "detail": f"{(s.t - x.dep0) / 60:+.0f} min from departure: {what} ({len(got)} listed: {listed})"})
    return rows


def check_listed(x: Trip, snaps: list[Snap], expected_t: np.ndarray, expected_i: np.ndarray) -> dict | None:
    """Whether x is in every trip update it's expected in (None: it's expected in none, e.g. its
    time is outside the archive or every fetch then failed or was empty). expected_i: the
    indices of the non-empty first-appearance snapshots, expected_t their content times."""
    lo, hi = x.dep0 - H1_FROM_S, x.sched[x.stops[-2]] - LISTED_END_S
    expected = expected_i[(expected_t >= lo) & (expected_t <= hi)]
    if not len(expected):
        return None
    seen = {i for i, _ in x.obs}
    missing = [int(i) for i in expected if i not in seen]
    row = {"date": x.date, "trip_id": x.trip_id, "dep0_epoch": x.dep0, "n_expected": len(expected),
           "n_missing": len(missing), "first_missing_epoch": None, "detail": ""}
    if not missing:
        return row | {"outcome": "in all"}
    runs: list[list[int]] = []  # consecutive expected trip updates it's missing from
    pos = {int(i): k for k, i in enumerate(expected)}
    for i in missing:
        if runs and pos[i] == pos[runs[-1][-1]] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    kind = ("never listed" if len(missing) == len(expected)
            else "missing 1-10" if len(missing) <= LISTED_FEW else "missing more")
    gaps = ", ".join(f"{local_hms(snaps[r[0]].t)}" + (f"–{local_hms(snaps[r[-1]].t)} ({len(r)})" if len(r) > 1 else "")
                     for r in runs[:6]) + (", …" if len(runs) > 6 else "")
    return row | {"outcome": kind, "first_missing_epoch": snaps[missing[0]].t,
                  "detail": f"missing from {len(missing)} of {len(expected)}: {gaps}"}


def kept_row(s: Snap, trip_id: str, stops: Stops, x: Trip | None, kind: str | None, names: dict[str, str]) -> dict:
    """Whether a trip-update entity is kept or discarded (kind: "ghost", "not in schedule")."""
    row = {"date": service_date(s.t), "trip_id": trip_id, "dep0_epoch": x.dep0 if x else None, "t_epoch": s.t,
           "outcome": kind or "kept", "detail": ""}
    if kind is None:
        return row
    deps = [t for t in (_time(a, d) for _, a, d in stops) if t is not None]
    first = f"{len(stops)} stop{'s' * (len(stops) > 1)} from {names.get(stops[0][0], stops[0][0])}"
    if kind == "ghost":
        return row | {"detail": f"{first}, latest departure {local_hms(max(deps))} ({(s.t - max(deps)) / 60:.0f} min before)"}
    return row | {"detail": f"{first}" + (f" at {local_hms(_time(*stops[0][1:]))}" if deps else "")}


def check(dates: list[str]) -> tuple[list[tuple[Table, pl.DataFrame]], dict]:
    """The tables with their rows (TU_H1: the snapshots of every scheduled trip of these dates;
    TU_LISTED: the trips; TU_KEPT: every entity); raises on H2 / H3."""
    snaps = load_snaps(dates)
    trips, names = load_trips(dates)
    obs = []  # (snapshot index, stops, trip) of every non-ghost entity of a scheduled trip
    kept = []  # "kept" rows
    for i, s in enumerate(snaps):
        for trip_id, stops in s.trips.items():
            if trip_id not in trips:
                kind = "not in schedule"
            elif all(_time(a, d) < s.t - GHOST_S for _, a, d in stops):
                kind = "ghost"
            else:
                kind = None
            x = assign(trips[trip_id], stops) if trip_id in trips else None
            if s.first:
                kept.append(kept_row(s, trip_id, stops, x, kind, names))
            if kind is None:
                x.obs.append((i, stops))
                obs.append((i, stops, x))
    info = check_h2_h3(snaps, obs, names)
    expected_i = np.array([i for i, s in enumerate(snaps) if s.first and s.n_entities > 0], dtype=int)
    expected_t = np.array([snaps[i].t for i in expected_i])
    h1, listed = [], []
    for x in sorted((x for v in trips.values() for x in v), key=lambda x: x.dep0):
        h1 += check_h1(x, snaps, names)
        if r := check_listed(x, snaps, expected_t, expected_i):
            listed.append(r)
    tables = [(t, t.conform(pl.DataFrame(rows, schema=t.polars_schema())) if rows else t.empty())
              for t, rows in ((TU_H1, h1), (TU_LISTED, listed), (TU_KEPT, [r for r in kept if r["date"] in dates]))]
    for t, df in tables:
        info[t.name] = dict(df["outcome"].value_counts().iter_rows())
    return tables, info


def run(dates: list[str]) -> dict:
    """Check every date; write each date's rows of each table to <date>/<table>.parquet."""
    tables, info = check(dates)
    for t, df in tables:
        for d in dates:
            t.write(df.filter(pl.col("date") == d), paths.day_dir(d) / f"{t.name}.parquet")
    return info
