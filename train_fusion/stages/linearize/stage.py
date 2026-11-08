"""Stage `linearize`: each VP fix -> distance along its trip's shape (UTM 10N projection,
interpolating the shape's own shape_dist_traveled, so positions match the stops') and its
distance from the shape."""
from __future__ import annotations

import polars as pl

from train_fusion.common.paths import day_dir
from train_fusion.stages.decode.schemas import VP
from train_fusion.stages.linearize.schemas import VP_LIN
from train_fusion.stages.static.stage import Static


def vp_lin(vp: pl.DataFrame, static: Static) -> tuple[pl.DataFrame, list[str]]:
    parts, unknown = [], []
    for (trip,), g in vp.group_by("trip_id", maintain_order=True):
        shape = static.shape_of_trip.get(trip)
        if shape is None:
            unknown.append(trip)
            continue
        s, off = static.geoms[shape].project(g["lat"].to_numpy(), g["lon"].to_numpy())
        parts.append(g.select("trip_id", "t_epoch", "content_exact", "lat", "lon")
                     .with_columns(pl.Series("s_m", s), pl.Series("offset_m", off)))
    df = pl.concat(parts) if parts else VP_LIN.empty()
    return df.sort("trip_id", "t_epoch"), sorted(unknown)


def run(day: str) -> dict:
    vl, unknown = vp_lin(VP.read(day_dir(day) / "vp.parquet"), Static(day))
    VP_LIN.write(vl, day_dir(day) / "vp_lin.parquet")
    return {"vp_lin": len(vl), "unknown_trips": unknown}
