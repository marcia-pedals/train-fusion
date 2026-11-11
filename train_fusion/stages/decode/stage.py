"""Stage `decode`: one service date's raw snapshots -> vp, tu.

Observations are timed by *content time* (when their content first appeared), from, in
order of preference: parity_check events, the fetcher's logged content_ets, else the raw
stamp (flagged content_exact = false). Snapshots whose content repeats an earlier one of
the same feed are dropped. See docs/pipeline.md 3.2.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
from google.transit import gtfs_realtime_pb2 as g

from train_fusion.common.paths import ROOT, RT, day_dir
from train_fusion.stages.decode.schemas import TU, VP

sys.path.insert(0, str(ROOT))
from fetchers.archive import read_log  # noqa: E402
from fetchers.gtfs.fetch import STAMP_S, parse_feed  # noqa: E402  (same digest as the fetcher logs)

FEEDS = ("tripupdates", "vehiclepositions")


def _resolve(file: str | None) -> Path | None:
    if not file:
        return None
    p = Path(file)
    for cand in (p, ROOT / p, RT.parent.parent / p):
        if cand.exists():
            return cand
    return None


def _read_log(day: str) -> tuple[list[dict], list[dict]]:
    reqs, events = [], []
    for r in read_log(RT / day):  # every fetcher's log segments (and the legacy file)
        if r.get("event") == "parity_check":
            events.append(r)
        elif r.get("feed") in FEEDS and "request_utc" in r:
            reqs.append(r)
    return reqs, events


def _content_times(fetches: list[dict], events: list[dict]) -> None:
    """Fill content_epoch / content_exact on each fetch dict (in place)."""
    by_stamp = {}
    for f in fetches:
        if f["stamp_epoch"] is not None:
            by_stamp.setdefault((f["feed"], f["stamp_epoch"]), []).append(f)

    # 2. the fetcher's own content_ets, and 3. otherwise the stamp (inexact)
    for f in fetches:
        ce = f.pop("_logged_content")
        if f["stamp_epoch"] is None:
            f["content_epoch"], f["content_exact"] = None, False
        elif ce is not None:
            f["content_epoch"], f["content_exact"] = float(ce), True
        else:
            f["content_epoch"], f["content_exact"] = float(f["stamp_epoch"]), False

    # 1. parity checks are authoritative; a check that contradicts the parity the
    #    fetcher had assumed makes its fetches since the previous check ambiguous.
    last_check = {feed: 0.0 for feed in FEEDS}
    for e in sorted(events, key=lambda e: e["utc"]):
        feed, a, b, res = e["feed"], e.get("ets_a"), e.get("ets_b"), e.get("result", "")
        t_ev = datetime.fromisoformat(e["utc"]).timestamp()
        if "content stamp" not in res or a is None or b is None:
            continue
        a_is_content = res.startswith("first")
        truth = {a: float(a) if a_is_content else float(a) - STAMP_S,
                 b: float(a) if a_is_content else float(b)}
        for f in by_stamp.get((feed, a), []):
            assumed_content = f["content_exact"] and f["content_epoch"] == float(a)
            if f["content_exact"] and assumed_content != a_is_content:
                for g_ in fetches:
                    if (g_["feed"] == feed and g_["content_exact"] and g_["role"] == "normal"
                            and last_check[feed] <= g_["request_epoch"] <= t_ev):
                        g_["content_epoch"], g_["content_exact"] = float(g_["stamp_epoch"]), False
        for stamp, ct in truth.items():
            for f in by_stamp.get((feed, stamp), []):
                f["content_epoch"], f["content_exact"] = ct, True
        last_check[feed] = t_ev


def run(day: str) -> dict[str, pl.DataFrame]:
    reqs, events = _read_log(day)
    logged_files = set()
    fetches, bodies = [], {}
    for r in reqs:
        path = _resolve(r.get("file"))
        if path:
            logged_files.add(path.resolve())
        fetches.append({"feed": r["feed"], "request_epoch": datetime.fromisoformat(r["request_utc"]).timestamp(),
                        "status": r.get("status"), "role": r.get("role", "unknown"),
                        "_logged_content": r.get("content_ets"), "_path": path})
    # .pb files with no log line (should be rare)
    for feed in FEEDS:
        for p in sorted((RT / day / feed).glob("*.pb")):
            if p.resolve() not in logged_files:
                t = datetime.strptime(p.stem, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc).timestamp()
                fetches.append({"feed": feed, "request_epoch": t, "status": 200, "role": "unknown",
                                "_logged_content": None, "_path": p})

    for f in fetches:
        p = f.pop("_path")
        f["file"] = str(p.relative_to(ROOT)) if p and p.is_relative_to(ROOT) else (str(p) if p else None)
        msg, stamp, digest = None, None, None
        if p and f["status"] == 200 and p.suffix == ".pb":
            body = p.read_bytes()
            stamp, digest = parse_feed(body)
            msg = g.FeedMessage()
            try:
                msg.ParseFromString(body)
            except Exception:
                msg = None
        f["ok"] = msg is not None and stamp is not None
        f["stamp_epoch"], f["content_sha256"] = stamp, digest
        f["n_entities"] = len(msg.entity) if msg is not None else 0
        bodies[id(f)] = msg

    _content_times(fetches, events)

    seen, vp_rows, tu_rows = set(), [], []
    for f in sorted(fetches, key=lambda f: (f["content_epoch"] or 0, f["request_epoch"])):
        f["duplicate"] = False
        if not f["ok"]:
            continue
        key = (f["feed"], f["content_sha256"])
        if key in seen:
            f["duplicate"] = True
            continue
        seen.add(key)
        msg, t, stamp, exact = bodies[id(f)], f["content_epoch"], f["stamp_epoch"], f["content_exact"]
        for e in msg.entity:
            if e.HasField("vehicle"):
                v = e.vehicle
                vp_rows.append({"trip_id": v.trip.trip_id, "route_id": v.trip.route_id,
                                "direction_id": v.trip.direction_id, "t_epoch": t, "stamp_epoch": stamp,
                                "content_exact": exact, "lat": v.position.latitude, "lon": v.position.longitude,
                                "file": f["file"]})
            if e.HasField("trip_update"):
                u = e.trip_update
                for i, s in enumerate(u.stop_time_update):
                    tu_rows.append({"trip_id": u.trip.trip_id, "t_epoch": t, "stamp_epoch": stamp,
                                    "content_exact": exact, "stu_idx": i, "stop_id": s.stop_id,
                                    "arr_epoch": s.arrival.time if s.HasField("arrival") else None,
                                    "dep_epoch": s.departure.time if s.HasField("departure") else None,
                                    "file": f["file"]})
    out = {
        "fetches": pl.DataFrame(fetches, schema_overrides={"status": pl.Int64, "stamp_epoch": pl.Int64,
                                                           "content_epoch": pl.Float64})
                   .sort("feed", "request_epoch"),
        "vp": pl.DataFrame(vp_rows) if vp_rows else VP.empty(),
        "tu": pl.DataFrame(tu_rows, schema_overrides={"arr_epoch": pl.Int64, "dep_epoch": pl.Int64})
              if tu_rows else TU.empty(),
    }
    d = day_dir(day)
    VP.write(out["vp"].sort("trip_id", "t_epoch"), d / "vp.parquet")
    TU.write(out["tu"].sort("trip_id", "t_epoch", "stu_idx"), d / "tu.parquet")
    return out

