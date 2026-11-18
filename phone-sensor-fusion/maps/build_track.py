"""Build the 1-D track model of the Caltrain main line from the OSM download
(maps/fetch_osm.py) and write it, checked in, to maps/data/:

  profile.csv   every 10 m along the line: s_m, lat, lon, heading_deg, curvature_per_m
  stations.csv  station, s_m (middle of its platforms), lat, lon (OSM station node),
                milepost_mi (OSM railway:position, for cross-checking only)
  points.csv    kind (switch | level_crossing), s_m, lat, lon

`s_m` is distance along the line from San Francisco, increasing southbound. The line is
the shortest path through the "Caltrain Peninsula Subdivision" main-track ways from the
San Francisco station to Tamien, i.e. one of the two-to-four parallel tracks; the others
are within a few metres of it, which is well below anything we resolve.

Curvature is signed: positive = turning left when travelling southbound (increasing s).

    python -m maps.build_track
"""

import csv
import heapq
import json
from collections import defaultdict

import numpy as np
from pyproj import Transformer
from scipy.ndimage import gaussian_filter1d
from scipy.spatial import cKDTree

from common import paths

OUT = paths.HERE / "maps" / "data"
STEP_M = 10.0
CURV_SMOOTH_M = 50.0  # OSM vertices are hand-traced: smooth heading before differentiating
PLATFORM_MAX_OFFSET_M = 30.0  # platform centres this close to the line are this line's
PLATFORM_SEARCH_M = 250.0  # ... and belong to the station whose node is within this
POINT_MAX_OFFSET_M = 12.0  # switches / crossings on any of the parallel main tracks
to_xy = Transformer.from_crs("EPSG:4326", "EPSG:32610", always_xy=True)
to_ll = Transformer.from_crs("EPSG:32610", "EPSG:4326", always_xy=True)


def load(name):
    return json.loads(paths.derived("maps", f"osm_{name}.json").read_text())["elements"]


def centerline(ways, start_ll, end_ll):
    """Shortest path through the ways' vertices (joined where coordinates coincide)."""
    idx, pts, adj = {}, [], defaultdict(list)
    for w in ways:
        prev = None
        for p in w["geometry"]:
            key = (round(p["lat"], 7), round(p["lon"], 7))
            if key not in idx:
                idx[key] = len(pts)
                pts.append(to_xy.transform(p["lon"], p["lat"]))
            i = idx[key]
            if prev is not None and prev != i:
                d = float(np.hypot(*np.subtract(pts[i], pts[prev])))
                adj[i].append((prev, d)); adj[prev].append((i, d))
            prev = i
    pts = np.array(pts)
    tree = cKDTree(pts)
    a = tree.query(to_xy.transform(start_ll[1], start_ll[0]))[1]
    b = tree.query(to_xy.transform(end_ll[1], end_ll[0]))[1]
    dist, prev, heap = {a: 0.0}, {}, [(0.0, a)]
    while heap:
        d, i = heapq.heappop(heap)
        if i == b:
            break
        if d > dist[i]:
            continue
        for j, w in adj[i]:
            if d + w < dist.get(j, np.inf):
                dist[j], prev[j] = d + w, i
                heapq.heappush(heap, (d + w, j))
    path = [b]
    while path[-1] != a:
        path.append(prev[path[-1]])
    return pts[path[::-1]]


def main():
    ways = [w for w in load("rail") if "Peninsula Subdivision" in w["tags"].get("name", "")]
    nodes = load("nodes")
    stations = {n["tags"]["name"]: n for n in nodes
                if n["tags"].get("railway") in ("station", "halt")
                and "Caltrain" in n["tags"].get("network", "")}
    sf = stations["San Francisco 4th & King Street"]
    tam = stations["Tamien"]
    line = centerline(ways, (sf["lat"], sf["lon"]), (tam["lat"], tam["lon"]))

    seg = np.hypot(*np.diff(line, axis=0).T)
    keep = np.r_[True, seg > 0]
    line = line[keep]
    s_v = np.r_[0, np.cumsum(np.hypot(*np.diff(line, axis=0).T))]
    s = np.arange(0, s_v[-1], STEP_M)
    x, y = np.interp(s, s_v, line[:, 0]), np.interp(s, s_v, line[:, 1])
    # heading (compass: 0 = north, 90 = east) and curvature from the smoothed line
    k = CURV_SMOOTH_M / STEP_M
    xs, ys = gaussian_filter1d(x, k, mode="nearest"), gaussian_filter1d(y, k, mode="nearest")
    dx, dy = np.gradient(xs, s), np.gradient(ys, s)
    theta = np.unwrap(np.arctan2(dy, dx))  # math angle, counter-clockwise positive
    curvature = np.gradient(theta, s)  # + = turning left
    heading = (90 - np.degrees(theta)) % 360
    lon, lat = to_ll.transform(x, y)

    OUT.mkdir(exist_ok=True)
    with (OUT / "profile.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["s_m", "lat", "lon", "heading_deg", "curvature_per_m"])
        for r in zip(s, lat, lon, heading, curvature):
            w.writerow([f"{r[0]:.0f}", f"{r[1]:.6f}", f"{r[2]:.6f}", f"{r[3]:.2f}", f"{r[4]:.3e}"])

    tree = cKDTree(np.c_[x, y])

    def project(n):
        d, i = tree.query(to_xy.transform(n["lon"], n["lat"]))
        return s[i], d

    # A station's position is the middle of its platforms (where a train stops), not the
    # OSM station node, which can sit anywhere along a 200 m platform.
    plat = np.array([project({"lat": w["center"]["lat"], "lon": w["center"]["lon"]})
                     for w in load("platforms")])
    plat_s = plat[plat[:, 1] < PLATFORM_MAX_OFFSET_M, 0]
    rows = []
    for name, n in stations.items():
        s_n, d = project(n)
        near = plat_s[np.abs(plat_s - s_n) < PLATFORM_SEARCH_M]
        if len(near):
            s_n = float(near.mean())
        if d < 100:
            mp = n["tags"].get("railway:position", "").replace("mi:", "")
            rows.append((s_n, name, n["lat"], n["lon"], mp))
    rows.sort()
    with (OUT / "stations.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["station", "s_m", "lat", "lon", "milepost_mi"])
        for s_n, name, la, lo, mp in rows:
            w.writerow([name, f"{s_n:.0f}", la, lo, mp])

    pts = []
    for n in nodes:
        kind = n["tags"].get("railway")
        if kind in ("switch", "level_crossing"):
            s_n, d = project(n)
            if d <= POINT_MAX_OFFSET_M:
                pts.append((s_n, kind, n["lat"], n["lon"]))
    pts.sort()
    with (OUT / "points.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "s_m", "lat", "lon"])
        for s_n, kind, la, lo in pts:
            w.writerow([kind, f"{s_n:.0f}", la, lo])

    print(f"line {s[-1]/1000:.1f} km, {len(rows)} stations, {len(pts)} points")
    # check against the mileposts OSM records for the stations
    prev = None
    for s_n, name, _, _, mp in rows:
        line_ = f"{name:34s} s={s_n/1000:7.3f} km"
        if mp:
            line_ += f"  milepost {float(mp)*1.609344:7.3f} km"
            if prev:
                line_ += f"  leg {(s_n-prev[0])/1000:6.3f} vs mp {(float(mp)-prev[1])*1.609344:6.3f}"
            prev = (s_n, float(mp))
        print(line_)


if __name__ == "__main__":
    main()
