"""Download the OpenStreetMap data the track model is built from (Overpass API):
main-line rail ways with geometry, and the switch / level-crossing / station nodes.
Writes data/phone-sensor-fusion/maps/osm_*.json (© OpenStreetMap contributors, ODbL).

    python -m maps.fetch_osm
"""

import subprocess
import time

from common import paths

BBOX = "37.25,-122.50,37.80,-121.85"  # San Francisco .. south of Tamien
QUERIES = {
    "rail": f'way["railway"="rail"]({BBOX});out tags geom;',
    "nodes": f'(node["railway"~"^(switch|level_crossing|railway_crossing)$"]({BBOX});'
             f'node["railway"~"^(station|halt|stop)$"]({BBOX}););out;',
    "platforms": f'(way["railway"="platform"]({BBOX});way["public_transport"="platform"]["train"="yes"]({BBOX}););out tags center;',
}


SERVERS = ["https://overpass-api.de/api/interpreter",
           "https://overpass.private.coffee/api/interpreter",
           "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]


def fetch(q: str, out) -> None:
    """The public servers are often at capacity: go round them with growing waits."""
    for attempt in range(8):
        for server in SERVERS:
            r = subprocess.run(
                ["curl", "-sS", "--fail", "-m", "180", "-A", "osm-rail-fetch/0.1", server,
                 "--data-urlencode", f"data=[out:json][timeout:120];{q}", "-o", str(out)])
            if r.returncode == 0 and out.read_bytes().lstrip()[:1] == b"{":
                return
        time.sleep(10 * 2**attempt)
    raise SystemExit(f"could not fetch {out.name}")


def main():
    for name, q in QUERIES.items():
        out = paths.derived("maps", f"osm_{name}.json")
        fetch(q, out)
        print("wrote", out, out.stat().st_size)


if __name__ == "__main__":
    main()
