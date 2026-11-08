"""Stage `reference`: the fastest a train could possibly run each stop pattern, and how
actual runs on the dates being built compare with it. See docs/reference.md.

A reference is
- one *segment curve* per (from_station -> to_station) pair of consecutive served stops:
  knots (p, t) from (0, 0) = "starts moving at from_station" to (L, T) = "stops moving at
  to_station", time linear between knots. The curve is the minimum-time run from rest to
  rest under the track speed limits, accelerating and braking at ACCEL_MPS2
  (physics.py);
- one *dwell* W at every station: stops moving -> starts moving again, fitted to the
  timetable together with a recovery allowance p (timetable_fit.py; p is
  reported but not part of the reference).

A trip's reference trajectory chains its segments with W at each intermediate stop.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass

import numpy as np
import polars as pl

from train_fusion.common import paths
from train_fusion.common.timeutil import service_day_origin
from train_fusion.stages.linearize.schemas import VP_LIN
from train_fusion.stages.reference import timetable_fit
from train_fusion.stages.reference.physics import Run, min_time_run
from train_fusion.stages.reference.schemas import REF_CRUISE, REF_FIT, REF_FIXES, REF_KNOTS, REF_LIMITS, REF_PAIRS, REF_SEGMENTS, REF_TRIPS
from train_fusion.stages.reference.track import ShapeLimits, load_speed_limits, on_shape
from train_fusion.stages.static.stage import Static
from train_fusion.stages.timetable import stage as timetable

ACCEL_MPS2 = 1.0   # = braking; Caltrain KISS gross starting acceleration (Stadler data sheet)
N_KNOTS = 21
FAR_M = 200.0      # fixes further than this from their trip's shape are ignored
TERMINAL_M = 300.0  # fixes this close to the origin / final stop are "at the terminal" (trains park up to ~250 m past)
# A fix is a "cruise fix" if the reference runs at constant speed for this far either side of
# it. Times between cruise fixes don't depend on where exactly trains stop (or where the GPS
# antenna sits on the train), as long as that's off by less than this.
CRUISE_MARGIN_M = 300.0


# ------------------------------------------------------------------ segments
def segments(static: Static) -> tuple[pl.DataFrame, dict[str, ShapeLimits]]:
    """Every (from, to) pair of consecutive served stops in the feed, with its median
    length, and its speed limits (positions from the start stop, from one trip that runs
    it: the stations anchor the limits, so every shape gives the same thing)."""
    ts = (static.trip_stops.sort("trip_id", "stop_idx")
          .join(static.trips.select("trip_id", "shape_id", "direction_id"), on="trip_id")
          .with_columns(pl.col("station_id").shift(-1).over("trip_id").alias("to_station"),
                        (pl.col("s_m").shift(-1).over("trip_id") - pl.col("s_m")).alias("len"))
          .drop_nulls("to_station")
          .with_columns((pl.col("station_id") + ">" + pl.col("to_station")).alias("segment_id")))
    segs = (ts.group_by("segment_id", "direction_id", pl.col("station_id").alias("from_station"), "to_station")
            .agg(pl.col("len").median().alias("length_m"), pl.len().alias("n_trips"))
            .sort("direction_id", "segment_id"))
    limits, by_shape = load_speed_limits(), {}
    lims = {}
    for r in ts.sort("segment_id", "trip_id").unique("segment_id", keep="first").iter_rows(named=True):
        sh = r["shape_id"]
        if sh not in by_shape:
            by_shape[sh] = on_shape(limits, static.stations_by_shape[sh], r["direction_id"],
                                    float(static.geoms[sh].dist_gtfs[-1]))
        e, v = by_shape[sh].edges - r["s_m"], by_shape[sh].mps
        keep = (e[1:] > 0) & (e[:-1] < r["len"])
        lims[r["segment_id"]] = ShapeLimits(np.clip(np.append(e[:-1][keep], e[1:][keep][-1]), 0.0, r["len"]), v[keep])
    return segs, lims


def constant_speed_stretches(x: np.ndarray, v: np.ndarray, tol: float = 1e-6) -> list[tuple[float, float]]:
    """Maximal stretches [x0, x1] of a dense profile over which the speed doesn't change."""
    flat = np.abs(np.diff(v)) < tol
    out, i = [], 0
    while i < len(flat):
        if flat[i] and v[i] > 0:
            j = i
            while j + 1 < len(flat) and flat[j + 1]:
                j += 1
            out.append((float(x[i]), float(x[j + 1])))
            i = j + 1
        else:
            i += 1
    return out


def build(static: Static) -> dict:
    segs, lims = segments(static)
    runs: dict[str, Run] = {}
    for sid, length in segs.select("segment_id", "length_m").iter_rows():
        scale = length / lims[sid].edges[-1]  # this segment's limits, stretched to its median length
        runs[sid] = min_time_run(length, lambda x, lim=lims[sid], scale=scale: lim.at(x / scale), ACCEL_MPS2)
    bounds = timetable_fit.pattern_bounds(timetable.load(static), static.trips.select("trip_id", "pattern_id"))
    fit = timetable_fit.fit(bounds, {k: r.run_s for k, r in runs.items()})

    seg_rows, knot_rows, lim_rows, cruise_rows = [], [], [], []
    for r in segs.iter_rows(named=True):
        sid, run, lim = r["segment_id"], runs[r["segment_id"]], lims[r["segment_id"]]
        scale = r["length_m"] / lim.edges[-1]
        p, t, v = run.knots(N_KNOTS)
        seg_rows.append({**r, "run_s": run.run_s, "v_max_mps": float(run.v.max())})
        knot_rows += [{"segment_id": sid, "knot": k, "p_m": p[k], "t_s": t[k], "v_mps": v[k]} for k in range(N_KNOTS)]
        lim_rows += [{"segment_id": sid, "p0_m": a * scale, "p1_m": b * scale, "limit_mps": m}
                     for a, b, m in zip(lim.edges, lim.edges[1:], lim.mps)]
        cruise_rows += [{"segment_id": sid, "p0_m": a, "p1_m": b} for a, b in constant_speed_stretches(run.x, run.v)]
    info = {"accel_mps2": ACCEL_MPS2, "W_s": fit.W, "p": fit.p, "static_version": static.version,
            "trips": len(static.trips), "patterns": fit.rows["pattern_id"].n_unique(), "paddings": len(fit.rows),
            "patterns_narrowed": bounds.filter("narrowed")["pattern_id"].n_unique()}
    return {"ref_segments": pl.DataFrame(seg_rows), "ref_knots": pl.DataFrame(knot_rows),
            "ref_limits": pl.DataFrame(lim_rows), "ref_cruise": pl.DataFrame(cruise_rows), "ref_fit": fit.rows,
            "info": info}


# ------------------------------------------------------------------ chaining for a trip
@dataclass
class TripRef:
    """A trip's chained reference: knots (s, tau) non-decreasing in both."""
    trip_id: str
    stop_s: np.ndarray        # served stops' positions on the trip's shape
    sched_epoch: np.ndarray   # their static scheduled times
    seg_ids: list[str]        # segment_id of run k (stop k -> k + 1)
    s: np.ndarray
    tau: np.ndarray
    phase: list[str]
    run_idx: np.ndarray

    @property
    def dep_epoch(self) -> float:
        return float(self.sched_epoch[0])

    def tau_at(self, s: np.ndarray) -> np.ndarray:
        """Reference time to reach s (the earliest, so a stop maps to the arrival there)."""
        i = np.clip(np.searchsorted(self.s, s, side="left"), 1, len(self.s) - 1)
        s0, s1, t0, t1 = self.s[i - 1], self.s[i], self.tau[i - 1], self.tau[i]
        f = np.clip((s - s0) / np.where(s1 > s0, s1 - s0, 1.0), 0.0, 1.0)
        return t0 + f * (t1 - t0)

    def run_of(self, s: np.ndarray) -> np.ndarray:
        return np.clip(np.searchsorted(self.stop_s, s, side="right") - 1, 0, len(self.stop_s) - 2)


def chain(trip_id: str, stops: pl.DataFrame, origin_epoch: float, knots: dict[str, tuple[np.ndarray, np.ndarray]],
          dwell_s: float) -> TripRef:
    st_ids, stop_s = stops["station_id"].to_list(), stops["s_m"].to_numpy()
    s_out, tau_out, phase, run_idx, seg_ids = [], [], [], [], []
    tau0 = 0.0
    for k in range(len(st_ids) - 1):
        sid = f"{st_ids[k]}>{st_ids[k + 1]}"
        seg_ids.append(sid)
        p, t = knots[sid]
        scale = (stop_s[k + 1] - stop_s[k]) / p[-1]  # this trip's own stop spacing (within a few m)
        s_out.append(stop_s[k] + p * scale)
        tau_out.append(tau0 + t)
        last = k == len(st_ids) - 2
        phase += ["run"] * (len(p) - 1) + ["end" if last else "dwell"]
        run_idx += [k] * len(p)
        tau0 += t[-1] + (0.0 if last else dwell_s)
    return TripRef(trip_id, stop_s, stops["sched_s"].to_numpy() + origin_epoch, seg_ids,
                   np.concatenate(s_out), np.concatenate(tau_out), phase, np.array(run_idx))


# ------------------------------------------------------------------ measuring against actual runs
def measure_day(day: str, tabs: dict) -> dict[str, pl.DataFrame]:
    static = Static(day)
    origin = service_day_origin(day)
    knots = {k[0]: (g["p_m"].to_numpy(), g["t_s"].to_numpy())
             for k, g in tabs["ref_knots"].sort("knot").group_by("segment_id")}
    W = tabs["info"]["W_s"]
    cruise: dict[str, list[tuple[float, float]]] = {}
    for sid, a, b in tabs["ref_cruise"].select("segment_id", "p0_m", "p1_m").iter_rows():
        if b - a > 2 * CRUISE_MARGIN_M:
            cruise.setdefault(sid, []).append((a + CRUISE_MARGIN_M, b - CRUISE_MARGIN_M))
    vl = VP_LIN.read(paths.day_dir(day) / "vp_lin.parquet").filter(pl.col("offset_m") <= FAR_M)
    fixes_by_trip = {k[0]: g.sort("t_epoch") for k, g in vl.group_by("trip_id")}

    trip_rows, fix_parts, pair_parts = [], [], []
    for trip in sorted(static.active_trip_ids):
        st_ids = static.stops_by_trip[trip]["station_id"].to_list()
        if any(f"{a}>{b}" not in knots for a, b in zip(st_ids, st_ids[1:])):
            print(f"  {day} trip {trip}: stop pattern not in the reference, skipped")
            continue
        ref = chain(trip, static.stops_by_trip[trip], origin, knots, W)
        trip_rows.append(pl.DataFrame({"i": np.arange(len(ref.s)), "s_m": ref.s, "tau_s": ref.tau,
                                       "t_epoch": ref.dep_epoch + ref.tau, "phase": ref.phase,
                                       "run_idx": ref.run_idx}).with_columns(pl.lit(trip).alias("trip_id")))
        g = fixes_by_trip.get(trip)
        if g is None or g.is_empty():
            continue
        t, s = g["t_epoch"].to_numpy(), g["s_m"].to_numpy()
        tau, run = ref.tau_at(s), ref.run_of(s)
        lat, lon = g["lat"].to_numpy(), g["lon"].to_numpy()
        repeat = np.concatenate([[False], (lat[1:] == lat[:-1]) & (lon[1:] == lon[:-1])])
        zone = np.where(s < ref.stop_s[0] + TERMINAL_M, "origin",
                        np.where(s > ref.stop_s[-1] - TERMINAL_M, "final", "line"))
        p_m = s - ref.stop_s[run]
        in_cruise = np.array([any(a <= p <= b for a, b in cruise.get(ref.seg_ids[k], [])) for p, k in zip(p_m, run)],
                             dtype=bool)
        fix_parts.append(pl.DataFrame({
            "trip_id": trip, "t_epoch": t, "content_exact": g["content_exact"], "repeat": repeat,
            "cruise": in_cruise, "s_m": s, "zone": zone, "run_idx": run,
            "segment_id": [ref.seg_ids[k] for k in run], "p_m": p_m, "tau_s": tau,
            "seg_tau_s": tau - ref.tau[np.searchsorted(ref.run_idx, run, side="left")],
            "delay_s": t - (ref.dep_epoch + tau), "sched_delay_s": t - np.interp(s, ref.stop_s, ref.sched_epoch)}))
        # pairs: each usable cruise fix with the trip's next one, however far apart. Usable =
        # exact time, between the terminals, and not a repeated coordinate (a coordinate is
        # only known to be current when it first appears: repeats are standing trains *or*
        # stale positions).
        use = np.flatnonzero(g["content_exact"].to_numpy() & (zone == "line") & ~repeat & in_cruise)
        a, b = use[:-1], use[1:]
        if len(a):
            stops = run[b] - run[a]
            pair_parts.append(pl.DataFrame({
                "trip_id": trip, "t0_epoch": t[a], "t1_epoch": t[b], "s0_m": s[a], "s1_m": s[b],
                "run0": run[a], "run1": run[b], "stops": stops,
                "segment_id": pl.Series([ref.seg_ids[r] if n == 0 else None for r, n in zip(run[a], stops)],
                                        dtype=pl.String),
                "dt_s": t[b] - t[a], "dtau_s": tau[b] - tau[a], "excess_s": (t[b] - t[a]) - (tau[b] - tau[a])}))

    cat = lambda parts, table: pl.concat(parts).with_columns(pl.lit(day).alias("date")) if parts else table.empty()
    return {"ref_trips": cat(trip_rows, REF_TRIPS), "ref_fixes": cat(fix_parts, REF_FIXES),
            "ref_pairs": cat(pair_parts, REF_PAIRS)}


def summary(pairs: pl.DataFrame) -> dict:
    """How often, and by how much, trains beat the reference between consecutive cruise
    fixes. By stops in between: 'within' a segment tests the curve; '1 stop' tests the
    curves plus one stop (braking, dwell, accelerating); '2+ stops' covers segments too
    short to have cruise fixes of their own."""
    out = {}
    for label, p in (("within", pairs.filter(pl.col("stops") == 0)),
                     ("1 stop", pairs.filter(pl.col("stops") == 1)),
                     ("2+ stops", pairs.filter(pl.col("stops") >= 2))):
        if p.is_empty():
            continue
        e = p["excess_s"]
        out[label] = {"n": len(p), "below_0s": round((e < 0).mean(), 3), "below_-15s": round((e < -15).mean(), 3),
                      "min_s": round(e.min(), 1), "q05_s": round(e.quantile(0.05), 1),
                      "q50_s": round(e.quantile(0.5), 1)}
    return out


# ------------------------------------------------------------------ stage entry point
def run(dates: list[str]) -> dict:
    out = paths.REFERENCE
    shutil.rmtree(out, ignore_errors=True)
    # the reference comes from the newest static feed; measuring uses each date's own feed
    tabs = build(Static(dates[-1]))
    for name, table in (("ref_segments", REF_SEGMENTS), ("ref_knots", REF_KNOTS), ("ref_limits", REF_LIMITS),
                        ("ref_cruise", REF_CRUISE), ("ref_fit", REF_FIT)):
        table.write(tabs[name], out / f"{name}.parquet")
    pairs = []
    for day in dates:
        m = measure_day(day, tabs)
        for name, table in (("ref_trips", REF_TRIPS), ("ref_fixes", REF_FIXES), ("ref_pairs", REF_PAIRS)):
            table.write(m[name], out / day / f"{name}.parquet")
        pairs.append(m["ref_pairs"])
    info = {**tabs["info"], "summary": summary(pl.concat(pairs))}
    (out / "reference.json").write_text(json.dumps(info, indent=1))
    return info
