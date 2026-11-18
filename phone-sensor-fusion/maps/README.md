# Maps

The Caltrain main line (San Francisco – Tamien, 78 km) as a one-dimensional track model,
from OpenStreetMap (© OpenStreetMap contributors, ODbL).

```sh
python -m maps.fetch_osm     # Overpass API -> data/phone-sensor-fusion/maps/osm_*.json
python -m maps.build_track   # -> maps/data/*.csv (checked in), and a check against mileposts
```

| file | |
|---|---|
| `data/profile.csv` | every 10 m: `s_m` (distance along the line from San Francisco), lat, lon, heading, curvature |
| `data/stations.csv` | 26 stations: `s_m` of the middle of the platforms, the OSM station node, OSM's milepost |
| `data/elevation.csv` | ground elevation every 50 m (USGS 1/3″ DEM via opentopodata.org; `python -m maps.fetch_elevation`). Bare earth: it dips into road underpasses the track bridges, so median-filter it over ~300 m before use |
| `data/points.csv` | 189 switches and 157 level crossings on the main tracks, with `s_m` |

`maps/track.py` loads them.

How it is built:

- **The line** is the shortest path through the ways named "Caltrain Peninsula Subdivision"
  from the San Francisco station to Tamien: one of the two to four parallel tracks. The
  others are a few metres away.
- **Curvature** is the derivative of heading along the line after smoothing the geometry
  over 50 m (OSM vertices are hand-traced). Positive = turning left when travelling south.
  It is good enough that the gyro of a resting phone follows speed × curvature through
  every curve (exploration finding 6).
- **A station's position** is the mean of its platform centres, not the station node, which
  can sit anywhere along a 200 m platform. Station spacing then agrees with OSM's mileposts
  to 10–60 m per leg (Hillsdale excepted: it moved in 2021 and the milepost tag is the old
  one). Trains don't stop at the same spot every time either, so "map distance between
  stations" is good to a few tens of metres, i.e. 1–2 % of a typical leg. That is the floor
  under every distance error quoted in this project.

Not here yet: speed limits (the main pipeline has
them in `track/speed_limits.toml`).
