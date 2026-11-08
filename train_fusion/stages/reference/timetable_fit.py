"""Split the timetable into physics, dwell and padding (docs/reference.md has the LP).

Trips of a pattern (same stations, same static times relative to the first departure;
stages.static.stage.trip_patterns) are expected to share their timetable exactly, also below a minute.
pattern_bounds checks that: relative to each trip's static first departure, every trip of
the pattern that 511's trip updates narrowed must have the same timetable intervals
(stage `timetable`); those are the pattern's intervals. A pattern with no narrowed trips
uses the static ones, which are the same for all its trips by definition. Anything else is
a PatternMismatch.

Then, for every segment i -> i+1 of every pattern g, with R the physical minimum run time
(physics.py) and D the pattern's departures (inside its intervals):

    D_{g,i+1} − D_{g,i} = (1 + p)·R + W + ε_{g,i},    ε_{g,i} ≥ 0

- p: a uniform recovery allowance, proportional to running time;
- W: the dwell (and any fixed per-stop cost the physics misses). A pattern's last segment
  ends at its *arrival* at the terminal, so it has no W term;
- ε: the pattern's padding on that segment.

The LP minimises the total padding Σ ε.

Errors in where trains really stop move a few seconds of R from one segment to the next
and cancel along a trip.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl
from scipy.optimize import linprog
from scipy.sparse import coo_array


class PatternMismatch(ValueError):
    """A pattern's trips don't share their timetable."""


@dataclass(frozen=True)
class Fit:
    p: float
    W: float
    rows: pl.DataFrame  # one per (pattern_id, seg_idx)


def pattern_bounds(timetable: pl.DataFrame, patterns: pl.DataFrame) -> pl.DataFrame:
    """One row per (pattern_id, stop_idx): station_id and the pattern's timetable interval
    [lo_s, hi_s] relative to its first static departure; n_trips; narrowed (by trip
    updates). Raises PatternMismatch if narrowed trips of a pattern disagree."""
    tt = (timetable.join(patterns, on="trip_id").sort("trip_id", "stop_idx")
          .with_columns((pl.col("dep_lo_s") - pl.col("static_s").first().over("trip_id")).alias("lo_s"),
                        (pl.col("dep_hi_s") - pl.col("static_s").first().over("trip_id")).alias("hi_s"),
                        (pl.col("source") == "tu").any().over("trip_id").alias("narrowed")))
    out = []
    for (pid,), g in tt.group_by("pattern_id", maintain_order=True):
        use = g.filter("narrowed") if g["narrowed"].any() else g
        ref_trip = use["trip_id"][0]
        ref = use.filter(pl.col("trip_id") == ref_trip)
        diff = (use.join(ref.select("stop_idx", pl.col("lo_s").alias("lo0"), pl.col("hi_s").alias("hi0")), on="stop_idx")
                .filter((pl.col("lo_s") != pl.col("lo0")) | (pl.col("hi_s") != pl.col("hi0"))))
        if len(diff):
            d = diff.row(0, named=True)
            raise PatternMismatch(
                f"pattern {pid}: at stop {d['stop_idx']} ({d['station_id']}) trip {d['trip_id']} has "
                f"[{d['lo_s']:.0f}, {d['hi_s']:.0f}] s but trip {ref_trip} has [{d['lo0']:.0f}, {d['hi0']:.0f}] s "
                f"after the first departure")
        out.append(ref.select(pl.lit(pid).alias("pattern_id"), "stop_idx", "station_id", "lo_s", "hi_s",
                              pl.lit(g["trip_id"].n_unique()).cast(pl.Int64).alias("n_trips"),
                              pl.lit(bool(g["narrowed"].any())).alias("narrowed")))
    return pl.concat(out).sort("pattern_id", "stop_idx")


def fit(bounds: pl.DataFrame, run_s: dict[str, float]) -> Fit:
    """bounds: pattern_bounds(); run_s: physical minimum run time per segment_id. Raises
    ValueError if infeasible (physics slower than the timetable somewhere)."""
    b = bounds.sort("pattern_id", "stop_idx").with_row_index("d")
    nxt = lambda c: pl.col(c).shift(-1).over("pattern_id")
    segs = (b.with_columns(nxt("station_id").alias("to_station"), nxt("d").alias("d_next"),
                           (pl.col("stop_idx") == pl.col("stop_idx").max().over("pattern_id") - 1).alias("final"))
            .drop_nulls("to_station")
            .with_columns((pl.col("station_id") + ">" + pl.col("to_station")).alias("segment_id")))
    m, n_d = len(segs), len(b)
    R = segs["segment_id"].replace_strict(run_s, return_dtype=pl.Float64).to_numpy()
    hasW = (~segs["final"]).to_numpy().astype(float)
    i, j, k = segs["d"].to_numpy().astype(int), segs["d_next"].to_numpy().astype(int), np.arange(m)
    # variables [p, W, D_0 .. D_{n_d-1}, ε_0 .. ε_{m-1}];  D_j − D_i − R·p − hasW·W − ε = R
    A = coo_array((np.concatenate([np.ones(m), -np.ones(m), -R, -hasW, -np.ones(m)]),
                   (np.concatenate([k] * 5), np.concatenate([2 + j, 2 + i, np.zeros(m, int), np.ones(m, int),
                                                             2 + n_d + k]))),
                  shape=(m, 2 + n_d + m)).tocsr()
    c = np.zeros(2 + n_d + m)
    c[2 + n_d:] = 1.0  # minimise Σ ε
    bounds_ = ([(None, None)] * 2 + list(zip(b["lo_s"].to_list(), b["hi_s"].to_list())) + [(0, None)] * m)
    res = linprog(c, A_eq=A, b_eq=R, bounds=bounds_, method="highs")
    if not res.success:
        raise ValueError(f"timetable fit failed: {res.message}")
    p, W, eps = float(res.x[0]), float(res.x[1]), res.x[2 + n_d:]
    model = (1 + p) * R + W * hasW
    rows = segs.select("pattern_id", pl.col("stop_idx").alias("seg_idx"), "segment_id", "final", "n_trips",
                       "narrowed").with_columns(pl.Series("phys_s", R), pl.Series("model_s", model),
                                                pl.Series("pad_s", eps), pl.Series("sched_s", model + eps))
    return Fit(p, W, rows)
