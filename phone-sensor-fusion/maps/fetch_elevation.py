"""Ground elevation along the line, every 50 m, from the USGS 1/3 arc-second DEM (via the
public opentopodata.org API: 100 points per request, one request per second).
Adds a column to nothing: writes maps/data/elevation.csv (s_m, elevation_m), which
maps/track.py serves interpolated.

Bare-earth elevation: embankments are in it, bridges and viaducts are not (the DEM shows
the ground or water under them).

    python -m maps.fetch_elevation
"""

import csv
import json
import subprocess
import time

import numpy as np

from maps import track as trackmod

STEP_M = 50.0
URL = "https://api.opentopodata.org/v1/ned10m"


def main():
    prof = np.loadtxt(trackmod.DATA / "profile.csv", delimiter=",", skiprows=1)
    s = np.arange(0, prof[-1, 0], STEP_M)
    lat, lon = np.interp(s, prof[:, 0], prof[:, 1]), np.interp(s, prof[:, 0], prof[:, 2])
    elev = []
    for i in range(0, len(s), 100):
        locs = "|".join(f"{a:.6f},{b:.6f}" for a, b in zip(lat[i:i + 100], lon[i:i + 100]))
        for attempt in range(5):
            r = subprocess.run(["curl", "-sS", "-m", "60", "-A", "track-elevation-fetch/0.1", f"{URL}?locations={locs}"],
                               capture_output=True, text=True)
            try:
                res = json.loads(r.stdout)["results"]
                break
            except Exception:
                time.sleep(3 * (attempt + 1))
        else:
            raise SystemExit(f"elevation request failed: {r.stdout[:200]}")
        elev += [x["elevation"] for x in res]
        time.sleep(1.1)
    with (trackmod.DATA / "elevation.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["s_m", "elevation_m"])
        for a, b in zip(s, elev):
            w.writerow([f"{a:.0f}", "" if b is None else f"{b:.2f}"])
    e = np.array([np.nan if x is None else x for x in elev])
    print(f"{len(s)} points, {np.isnan(e).sum()} missing, range {np.nanmin(e):.1f}..{np.nanmax(e):.1f} m")


if __name__ == "__main__":
    main()
