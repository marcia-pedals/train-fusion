"""One-off: turn OpenStreetMap `maxspeed` tags on the Caltrain main line (SF <-> Tamien)
into track/speed_limits.toml. The pipeline reads only the TOML; this is kept to show where
the numbers came from and to redo it if needed.

Get the OSM data (Overpass API), then run:

    curl -s https://overpass-api.de/api/interpreter --data-urlencode \
      'data=[out:json][timeout:80];way["railway"="rail"]["usage"="main"](37.0,-122.6,37.8,-121.5);out tags geom;' \
      -o /some/dir/rail.json
    python -m experiments.osm_speed_limits <date> /some/dir/rail.json > track/speed_limits.toml

<date>: a built service date; its static feed provides the shape and stations.

Method: project every way named "...Peninsula Subdivision..." that lies along a northbound
Tamien -> SF shape onto it; per 10 m along the shape take the *highest* limit of any
track there (parallel tracks differ: sidings and yard leads are slower than the main
line); collapse into runs; anchor each run end to its nearest station.
"""
from __future__ import annotations

import json
import sys
from datetime import date

import numpy as np
import polars as pl

from train_fusion.stages.static.stage import Static

CELL_M = 10.0
MAX_OFFSET_M = 40.0   # ways further than this from the shape are other lines / yards
MPH = 0.44704


def corridor_shape(st: Static) -> str:
    """A northbound shape that runs Tamien -> San Francisco."""
    nb = set(st.trips.filter(pl.col("direction_id") == 0)["shape_id"])
    ss = st.shape_stations
    has = lambda sid: set(ss.filter(pl.col("shape_id") == sid)["station_id"])
    cands = sorted(s for s in nb if {"tamien", "san_francisco"} <= has(s))
    if not cands:
        sys.exit("no northbound Tamien -> SF shape")
    return cands[0]


def main(day: str, osm_json: str) -> None:
    st = Static(day)
    shape = corridor_shape(st)
    g = st.geoms[shape]
    stn = st.shape_stations.filter(pl.col("shape_id") == shape).sort("s_m")
    st_ids, st_s = stn["station_id"].to_list(), stn["s_m"].to_numpy()
    s_tam, s_sf = st_s[st_ids.index("tamien")], st_s[st_ids.index("san_francisco")]

    cells = np.arange(s_tam - 300, s_sf + 300, CELL_M)
    best = np.zeros(len(cells))
    for w in json.load(open(osm_json))["elements"]:
        t = w["tags"]
        if "Peninsula" not in t.get("name", "") or "maxspeed" not in t:
            continue
        lat = np.array([p["lat"] for p in w["geometry"]])
        lon = np.array([p["lon"] for p in w["geometry"]])
        s, off = g.project(lat, lon)
        if np.median(off) > MAX_OFFSET_M:
            continue
        mph = float(t["maxspeed"].split()[0])
        inside = (cells >= s.min()) & (cells < s.max())
        best[inside] = np.maximum(best[inside], mph)

    covered = best > 0
    cells, best = cells[covered], best[covered]
    default = 79.0
    runs, i0 = [], 0
    for i in range(1, len(cells) + 1):
        if i == len(cells) or best[i] != best[i0]:
            runs.append((cells[i0], cells[i - 1] + CELL_M, best[i0]))
            i0 = i

    def anchor(s: float) -> tuple[str, int]:
        j = int(np.argmin(np.abs(st_s - s)))
        return st_ids[j], int(round((s - st_s[j]) / 10) * 10)

    out = [
        "# Track speed limits, San Francisco <-> Tamien (Caltrain Peninsula Subdivision).",
        f"# Taken from OpenStreetMap `maxspeed` tags on {date.today().isoformat()} by",
        "# experiments/osm_speed_limits.py; crowd-edited, not an official Caltrain source. Where",
        "# parallel tracks differ, the main line's (highest) limit is used. Hand edits are fine.",
        "#",
        "# Everything not listed below is `default_mph`. Each restriction runs between two",
        "# points, each given as a station plus an offset in metres along the line, positive",
        "# towards San Francisco (north). Restrictions are listed south to north and apply in",
        "# both directions.",
        "",
        f"default_mph = {default:g}",
        "",
    ]
    for a, b, mph in runs:
        if mph >= default:
            continue
        fa, fb = anchor(a), anchor(b)
        out += ["[[restriction]]", f"mph = {mph:g}",
                f'from = {{ station = "{fa[0]}", offset_m = {fa[1]} }}',
                f'to = {{ station = "{fb[0]}", offset_m = {fb[1]} }}',
                f"# {(b - a):.0f} m", ""]
    if any(m > default for *_, m in runs):
        print("warning: limits above default present", file=sys.stderr)
    print("\n".join(out).rstrip() + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
