#!/usr/bin/env python3
"""Measure how soon after an entity timestamp a new snapshot is actually served.

Polls both RT feeds every --interval seconds in a window around the predicted next
upstream update, and prints when each feed's entity timestamp changes. Uses ~2*N requests
(N = window / interval), so keep it small: the quota is 60/hour/key.
"""
import argparse
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fetchers.gtfs.fetch import BASE, AGENCY, RT_FEEDS, http_get, load_dotenv  # noqa: E402
from google.transit import gtfs_realtime_pb2 as g  # noqa: E402


def get(feed):
    path, key = RT_FEEDS[feed]
    t0 = time.time()
    status, headers, body = http_get(f"{BASE}/{path}?api_key={os.environ[key]}&agency={AGENCY}")
    t1 = time.time()
    m = g.FeedMessage()
    m.ParseFromString(body)
    ts = {(e.trip_update.timestamp or e.vehicle.timestamp) for e in m.entity}
    return t0, t1, status, max(ts) if ts else None, headers.get("RateLimit-Remaining")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", type=float, default=60.6)
    ap.add_argument("--before", type=float, default=3)
    ap.add_argument("--after", type=float, default=18)
    ap.add_argument("--interval", type=float, default=1.5)
    a = ap.parse_args()
    load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")

    _, _, _, ts, _ = get("vehiclepositions")
    nxt = ts + a.period
    while nxt - a.before < time.time():
        nxt += a.period
    print(f"last ts {ts}; predicted next ~{nxt:.1f} (in {nxt - time.time():.1f}s)", flush=True)
    time.sleep(max(0, nxt - a.before - time.time()))
    end = nxt + a.after
    seen = {}
    lock = threading.Lock()

    def poll(feed):
        while time.time() < end:
            t0, t1, st, ts, rem = get(feed)
            with lock:
                new = seen.get(feed) not in (None, ts)
                seen[feed] = ts
                print(f"{feed:17s} req={t0:.2f} resp={t1:.2f} status={st} ent_ts={ts}"
                      f" req-ent={t0 - ts:+6.2f}{'  <-- NEW' if new else ''} rem={rem}", flush=True)
            time.sleep(max(0, a.interval - (time.time() - t0)))

    ths = [threading.Thread(target=poll, args=(f,)) for f in RT_FEEDS]
    [t.start() for t in ths]
    [t.join() for t in ths]


if __name__ == "__main__":
    main()
