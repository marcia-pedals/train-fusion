#!/usr/bin/env python3
"""Summarize archived GTFS-RT snapshots: freshness, update cadence, content changes.

usage: python -m fetchers.gtfs.inspect_rt data/rt/<service_date> [--verbose]
"""

import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from google.transit import gtfs_realtime_pb2 as g

from fetchers.archive import read_log


def load(path: Path):
    m = g.FeedMessage()
    m.ParseFromString(path.read_bytes())
    return m


def entity_ts(ent):
    if ent.HasField("trip_update"):
        return ent.trip_update.timestamp or None
    if ent.HasField("vehicle"):
        return ent.vehicle.timestamp or None
    return None


def content_key(m):
    """Feed content with the header stripped, to detect real changes."""
    c = g.FeedMessage()
    c.CopyFrom(m)
    c.header.Clear()
    c.header.gtfs_realtime_version = "x"
    return c.SerializeToString(deterministic=True)


def main():
    day = Path(sys.argv[1])
    verbose = "--verbose" in sys.argv
    log = read_log(day)
    by_file = {r["file"]: r for r in log if r["file"]}
    for feed in ("tripupdates", "vehiclepositions"):
        files = sorted((day / feed).glob("*.pb"))
        print(f"\n=== {feed}: {len(files)} snapshots")
        prev = None
        mods = Counter()
        for f in files:
            r = by_file.get(str(f), {})
            req = datetime.fromisoformat(r["request_utc"]).timestamp() if r else None
            m = load(f)
            hts = m.header.timestamp
            ets = [t for t in (entity_ts(e) for e in m.entity) if t]
            emax = max(ets) if ets else None
            emin = min(ets) if ets else None
            key = content_key(m)
            changed = prev is None or key != prev
            prev = key
            if emax:
                mods[emax % 60] += 1
            print(f"{f.stem}  n={len(m.entity):2d}  req={req and datetime.fromtimestamp(req, timezone.utc):%H:%M:%S}"
                  f"  hdr-req={hts - req if req else 0:+6.1f}"
                  f"  ent(min,max)-req=({(emin - req) if emin else float('nan'):+6.1f},{(emax - req) if emax else float('nan'):+6.1f})"
                  f"  distinct_ent_ts={len(set(ets))}  changed={changed}  rem={r.get('headers', {}).get('ratelimit-remaining')}")
            if verbose:
                print("   ", sorted(Counter(ets).items()))
        print("entity max-ts second-of-minute histogram:", sorted(mods.items()))


if __name__ == "__main__":
    main()
