#!/usr/bin/env python3
"""Does feed *content* change on every ~15 s upstream refresh, or only the timestamps?

Fetches each RT feed once per upstream cycle (just after each update) for --minutes,
saves every body under data/experiments/cadence-<stamp>/, then diffs consecutive
snapshots per trip: did the vehicle position / the predicted stop times change?

Uses ~4 requests/minute/key, i.e. 4x the normal quota rate, so keep runs short.
Stops on the first non-200 response.
"""
import argparse
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fetchers.gtfs.fetch import AGENCY, BASE, RT_FEEDS, http_get, load_dotenv, stamp  # noqa: E402
from google.transit import gtfs_realtime_pb2 as g  # noqa: E402


def collect(feed, out, until, stop, margin):
    path, key = RT_FEEDS[feed]
    url = f"{BASE}/{path}?api_key={os.environ[key]}&agency={AGENCY}"
    (out / feed).mkdir(parents=True, exist_ok=True)
    nxt = time.time()
    while not stop.is_set() and nxt < until:
        time.sleep(max(0, nxt - time.time()))
        t = datetime.now(timezone.utc)
        status, headers, body = http_get(url)
        if status != 200:
            print(f"{feed}: HTTP {status} {headers.get('Retry-After', '')} {body[:200]!r}; stopping", flush=True)
            stop.set()
            return
        (out / feed / f"{stamp(t)}.pb").write_bytes(body)
        m = g.FeedMessage()
        m.ParseFromString(body)
        ets = max((e.trip_update.timestamp or e.vehicle.timestamp) for e in m.entity)
        print(f"{t:%H:%M:%S.%f}"[:-3], f"{feed:16s} ets={ets} age={t.timestamp() - ets:5.2f}"
              f" rem={headers.get('RateLimit-Remaining')}", flush=True)
        nxt = max(ets + 15 + margin, time.time() + 5)  # just after the next update


def snapshot(path):
    m = g.FeedMessage()
    m.ParseFromString(path.read_bytes())
    out = {}
    for e in m.entity:
        if e.HasField("vehicle"):
            v = e.vehicle
            out[v.trip.trip_id] = (v.timestamp, (round(v.position.latitude, 6), round(v.position.longitude, 6)))
        else:
            tu = e.trip_update
            out[tu.trip.trip_id] = (tu.timestamp, tuple(
                (s.stop_id, s.arrival.time, s.departure.time) for s in tu.stop_time_update))
    return out


def analyze(out):
    for feed in RT_FEEDS:
        files = sorted((out / feed).glob("*.pb"))
        snaps = [snapshot(f) for f in files]
        print(f"\n=== {feed}: {len(files)} snapshots")
        print("ets step | trips | content changed | unchanged (only ts)")
        tot_changed = tot = 0
        for a, b in zip(snaps, snaps[1:]):
            common = set(a) & set(b)
            if not common:
                continue
            step = max(v[0] for v in b.values()) - max(v[0] for v in a.values())
            changed = sum(a[t][1] != b[t][1] for t in common)
            tot_changed += changed
            tot += len(common)
            print(f"{step:+8d} | {len(common):5d} | {changed:15d} | {len(common) - changed}")
        if tot:
            print(f"overall: {tot_changed}/{tot} = {tot_changed / tot:.0%} of trip-steps changed content")
        # Per trip: how many distinct content values vs snapshots seen
        per_trip = {}
        for s in snaps:
            for t, (_, c) in s.items():
                per_trip.setdefault(t, []).append(c)
        runs = []
        for cs in per_trip.values():
            n = 1
            for x, y in zip(cs, cs[1:]):
                if x == y:
                    n += 1
                else:
                    runs.append(n)
                    n = 1
        if runs:
            print("lengths of unchanged-content runs (in snapshots, completed runs only):",
                  sorted(runs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=5)
    ap.add_argument("--margin", type=float, default=2.5)
    ap.add_argument("--analyze", type=Path, help="only analyze an existing run directory")
    a = ap.parse_args()
    if a.analyze:
        return analyze(a.analyze)
    load_dotenv(ROOT / ".env.local")
    out = ROOT / "data" / "experiments" / f"cadence-{stamp(datetime.now(timezone.utc))}"
    until = time.time() + a.minutes * 60
    stop = threading.Event()
    ths = [threading.Thread(target=collect, args=(f, out, until, stop, a.margin)) for f in RT_FEEDS]
    [t.start() for t in ths]
    [t.join() for t in ths]
    print(f"\nsaved to {out}")
    analyze(out)


if __name__ == "__main__":
    main()
