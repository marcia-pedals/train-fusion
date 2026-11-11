#!/usr/bin/env python3
"""Can we tell, for every archived fetch, whether its stamp was a content stamp?

511 stamps each feed every ~15.1 s but content changes only every other stamp. Anchors
(stamps known to be content / repeat) come from the cadence experiments (every stamp
captured) and the fetcher's parity_check events. Stamps are joined into contiguous
stretches wherever the number of stamps between consecutive observed stamps is
unambiguous (residual <= 3 s); within a stretch every stamp gets an index, and we check
whether one parity (even index = content, or odd) explains all its anchors.

usage: python experiments/content_parity.py [SERVICE_DATE]   (default: latest)
"""
import datetime as dt
import glob
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch import read_log  # noqa: E402
from google.transit import gtfs_realtime_pb2 as g

def info(path):
    m = g.FeedMessage(); m.ParseFromString(open(path, 'rb').read())
    ets = max((e.trip_update.timestamp or e.vehicle.timestamp) for e in m.entity) if m.entity else None
    m.header.timestamp = 0
    for e in m.entity:
        if e.HasField('trip_update'): e.trip_update.timestamp = 0
        if e.HasField('vehicle'): e.vehicle.timestamp = 0
    return ets, hashlib.sha256(m.SerializeToString(deterministic=True)).hexdigest()


day = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob("data/rt/*"))[-1].split("/")[-1]
L = read_log(Path(f"data/rt/{day}"))
hm = lambda s: dt.datetime.fromtimestamp(s).strftime('%H:%M')
for feed in ('vehiclepositions', 'tripupdates'):
    anchors = {}
    for d in sorted(glob.glob(f'data/experiments/cadence-*/{feed}')):
        seq = {}
        for p in sorted(glob.glob(d + '/*.pb')):
            e, h = info(p)
            if e: seq[e] = h
        s_ = sorted(seq)
        for a, b in zip(s_, s_[1:]):
            if round((b - a) / 15.13) == 1: anchors[b] = seq[b] != seq[a]
    for r in L:
        if r.get('event') == 'parity_check' and r['feed'] == feed and 'content' in r['result']:
            first = r['result'].startswith('first'); anchors[r['ets_a']] = first; anchors[r['ets_b']] = not first
    stamps = sorted({r['entity_ts'] for r in L if r.get('feed') == feed and r.get('entity_ts')} | set(anchors))
    segs = [[stamps[0]]]; idx = {stamps[0]: 0}
    for a, b in zip(stamps, stamps[1:]):
        n = round((b - a) / 15.13)
        if abs((b - a) - n * 15.13) > 3: segs.append([b]); idx[b] = 0
        else: segs[-1].append(b); idx[b] = idx[a] + n
    print('==', feed)
    for sg in segs:
        an = [(idx[s] % 2 == 0) == anchors[s] for s in sg if s in anchors]
        print(f'  {hm(sg[0])}-{hm(sg[-1])}  {len(sg):3d} stamps  anchors {len(an):2d}  '
              f'even=content agrees {sum(an)}/{len(an)}')

