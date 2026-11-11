#!/usr/bin/env python3
"""Archive raw Caltrain GTFS-RT feeds (and daily static GTFS) from 511.org.

Layout (all under DATA_DIR, default ./data, which is gitignored):

    data/rt/<service_date>/<feed>/<utc_stamp>.pb   raw response body, byte-for-byte
    data/rt/<service_date>/fetch_log/<utc_minute>_<instance>.jsonl
                                                   one line per request (timing, status,
                                                   headers, scheduler state) + parity checks
    data/gtfs/<local_date>/GTFSTransitData_CT_<utc_stamp>.zip   raw static GTFS zip
    data/gtfs/fetch_log/<utc_minute>_<instance>.jsonl

Every file is written once and never changed: the log goes to a new file each minute, named
by this process's instance id. So archives from several fetchers (restarts, a replaced VM,
two running at once) merge by plain union, which is what `--bucket` relies on: it uploads
each finished file create-only to GCS (see fetch_cloud.py). Older archives have a single
fetch_log.jsonl per directory instead; read_log() reads both.

service_date is the Caltrain operating day: local (America/Los_Angeles) time minus
SERVICE_DAY_CUTOFF_HOURS, so trains running after midnight file under the previous day.

Scheduling (see Scheduler): 511 allows 60 req/hour per API key, so each RT feed gets
its own key and is fetched at most once per 60 s on average. 511 re-stamps the feeds
every ~15.1 s, but the content only changes on every other stamp (~30.3 s); the stamps
in between repeat the previous content. So each feed locks onto the *content* stamps:
it fetches `margin` after the content update 60 s past the last one it saw, with the
parity found by an occasional pair of fetches one stamp apart, and the margin adapted
AIMD-style to the serve lag. If the feed has no timestamps (e.g. overnight, no trains),
it falls back to a plain 60 s cadence.

The daily static GTFS fetch replaces one vehicle-position fetch (first slot after 03:00
local, when no trains run) so the key's budget is never exceeded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import secrets
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from google.transit import gtfs_realtime_pb2
except ImportError:  # archiving still works; we just fall back to a plain 60 s cadence
    gtfs_realtime_pb2 = None

TZ = ZoneInfo("America/Los_Angeles")
SERVICE_DAY_CUTOFF_HOURS = 3
BASE = "https://api.511.org/transit"
AGENCY = "CT"

MIN_INTERVAL_S = 60.0     # per key: 60 requests/hour
STAMP_S = 15.0            # 511 re-stamps the feed every ~15.1 s...
CONTENT_S = 30.0          # ...but content only changes every other stamp (~30.3 s)
# Seconds after the predicted content update to fetch; adapts (serve lag seen up to ~1.5 s).
MARGIN_S = 3.0            # starting value
MARGIN_MIN_S, MARGIN_MAX_S = 1.0, 12.0
MARGIN_UP_S = 1.0         # when a fetch arrives before the update it aimed at
MARGIN_DOWN_S = 0.05      # otherwise (~3 s/hour); at the edge ~1 in 20 fetches is early
STALE_S = 5.0             # if data at the rate limit would be this far past margin, wait a cycle (with
                          # no drift, a late phase never self-corrects, so re-phase eagerly)
PARITY_CHECK_EVERY_S = 900.0  # TU parity flipped within an hour on 2026-11-07
PARITY_RETRY_S = 300.0    # after an inconclusive check

# feed name -> (url path, env var holding the API key)
RT_FEEDS = {
    "tripupdates": ("tripupdates", "API_KEY_511_1"),
    "vehiclepositions": ("vehiclepositions", "API_KEY_511_2"),
}
# Static GTFS borrows the vehicle-position key's slot once a day at this local hour.
STATIC_KEY_ENV = "API_KEY_511_2"
STATIC_SLOT_FEED = "vehiclepositions"
STATIC_LOCAL_HOUR = 3

KEEP_HEADERS = ("date", "ratelimit-limit", "ratelimit-remaining", "content-type",
                "content-length", "last-modified", "etag", "retry-after")

# Names this process's log files, so concurrent or successive fetchers never share one.
INSTANCE = f"{socket.gethostname().split('.')[0]}-{secrets.token_hex(3)}"
LOG_DIR = "fetch_log"

log_lock = threading.Lock()
fetch_counts = Counter()  # feed -> successful (HTTP 200) fetches, for --metrics


def service_date(t_utc: datetime) -> str:
    local = t_utc.astimezone(TZ) - timedelta(hours=SERVICE_DAY_CUTOFF_HOURS)
    return local.strftime("%Y-%m-%d")


def stamp(t_utc: datetime) -> str:
    return t_utc.strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z"  # ms precision


def load_dotenv(path: Path) -> None:
    """Minimal KEY="value" parser so the script also works outside direnv."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def http_get(url: str, timeout: float = 30.0):
    req = urllib.request.Request(url, headers={"User-Agent": "train-fusion-archiver/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


def log_minute(name: str) -> datetime | None:
    """The UTC minute a log segment covers, from its name (None for other files)."""
    try:
        return datetime.strptime(name.split("_", 1)[0], "%Y%m%dT%H%MZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def append_log(log_dir: Path, rec: dict) -> None:
    """Append to this process's segment for the current minute, under log_dir/fetch_log/."""
    now = datetime.now(timezone.utc)
    path = log_dir / LOG_DIR / f"{now:%Y%m%dT%H%MZ}_{INSTANCE}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with log_lock, path.open("a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def log_files(log_dir: Path, since: datetime | None = None) -> list[Path]:
    """The fetch-log files of a directory: the legacy single file, then the segments
    (those covering `since` or later, if given)."""
    out = [log_dir / "fetch_log.jsonl"] if (log_dir / "fetch_log.jsonl").exists() else []
    seg = log_dir / LOG_DIR
    if seg.is_dir():
        for p in sorted(seg.glob("*.jsonl")):
            m = log_minute(p.name)
            if since is None or (m is not None and m + timedelta(minutes=1) > since):
                out.append(p)
    return out


def read_log(log_dir: Path, since: datetime | None = None) -> list[dict]:
    """Every record in a directory's fetch log, skipping unparseable (e.g. torn) lines."""
    out = []
    for p in log_files(log_dir, since):
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def parse_feed(body: bytes):
    """(latest entity timestamp, digest of content with all timestamps stripped), or Nones."""
    if gtfs_realtime_pb2 is None or not body:
        return None, None
    try:
        m = gtfs_realtime_pb2.FeedMessage()
        m.ParseFromString(body)
    except Exception:
        return None, None
    ts = [t for t in (e.trip_update.timestamp or e.vehicle.timestamp for e in m.entity) if t]
    m.header.Clear()
    m.header.gtfs_realtime_version = "-"
    for e in m.entity:
        e.trip_update.ClearField("timestamp") if e.HasField("trip_update") else None
        e.vehicle.ClearField("timestamp") if e.HasField("vehicle") else None
    digest = hashlib.sha256(m.SerializeToString(deterministic=True)).hexdigest()
    return (max(ts) if ts else None), digest


def fetch_and_store(kind: str, url: str, out_dir: Path, filename: str, log_dir: Path,
                    scheduled: float, parse: bool, extra=None):
    """Fetch, save the raw body, log. Returns (request_time, entity_ts, content_digest)."""
    t0 = datetime.now(timezone.utc)
    m0 = time.monotonic()
    err = None
    try:
        status, headers, body = http_get(url)
    except Exception as e:  # network errors, timeouts
        status, headers, body, err = None, {}, b"", f"{type(e).__name__}: {e}"
    elapsed = time.monotonic() - m0
    path = None
    if body:
        # Non-200 bodies are kept too, with a suffix, so quirks can be studied later.
        if status != 200:
            filename = f"{filename}.http{status}"
        path = out_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(body)
        tmp.rename(path)
    ets, digest = parse_feed(body) if parse and status == 200 else (None, None)
    extra = dict(extra or {})
    content_of = extra.pop("content_of", None)  # callable: entity ts -> content stamp
    if content_of is not None:
        extra["content_ets"] = content_of(ets)
    rec = {
        "feed": kind,
        "scheduled_utc": datetime.fromtimestamp(scheduled, timezone.utc).isoformat(),
        "request_utc": t0.isoformat(),
        "elapsed_s": round(elapsed, 3),
        "status": status,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest() if body else None,
        "entity_ts": ets,
        "age_at_request_s": round(t0.timestamp() - ets, 3) if ets else None,
        "content_sha256": digest,
        "file": str(path) if path else None,
        "headers": {k.lower(): v for k, v in headers.items() if k.lower() in KEEP_HEADERS},
        "error": err,
        **(extra or {}),
    }
    append_log(log_dir, rec)
    if status == 200:
        with log_lock:
            fetch_counts[kind] += 1
    tag = f" [{extra['role']}]" if extra and extra.get("role", "normal") != "normal" else ""
    print(f"{t0.astimezone(TZ):%H:%M:%S.%f}"[:-3], f"{kind:16s}", status, f"{len(body):6d}B",
          f"{elapsed:.2f}s", f"age={rec['age_at_request_s']}",
          f"margin={extra.get('margin_s') if extra else None}",
          "rem=" + str(rec["headers"].get("ratelimit-remaining")), err or "", tag, flush=True)
    return t0.timestamp(), ets, digest


def rt_log_dir(data_dir: Path) -> Path:
    return data_dir / "rt" / service_date(datetime.now(timezone.utc))


def fetch_rt(feed: str, data_dir: Path, scheduled: float, extra=None):
    path, key_env = RT_FEEDS[feed]
    url = f"{BASE}/{path}?api_key={os.environ[key_env]}&agency={AGENCY}"
    now = datetime.now(timezone.utc)
    sd = service_date(now)
    return fetch_and_store(feed, url, data_dir / "rt" / sd / feed, stamp(now) + ".pb",
                           data_dir / "rt" / sd, scheduled, True, extra)


def fetch_static(data_dir: Path, scheduled: float):
    url = f"{BASE}/datafeeds?api_key={os.environ[STATIC_KEY_ENV]}&operator_id={AGENCY}"
    now = datetime.now(timezone.utc)
    day = now.astimezone(TZ).strftime("%Y-%m-%d")
    return fetch_and_store("gtfs_static", url, data_dir / "gtfs" / day,
                           f"GTFSTransitData_{AGENCY}_{stamp(now)}.zip",
                           data_dir / "gtfs", scheduled, False)


def have_static_today(data_dir: Path, remote=None) -> bool:
    """Whether today's static GTFS zip was fetched: locally, or else per `remote(day)`
    (with --bucket, since uploaded files are deleted and a fresh VM starts empty)."""
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    d = data_dir / "gtfs" / day
    if d.exists() and any(p.suffix == ".zip" for p in d.iterdir()):
        return True
    return remote is not None and remote(day)


class Scheduler:
    """Per-feed fetch timing. State: margin, the last known content-update stamp, the
    update the last fetch was aimed at, and when the next parity check is due.

    Each fetch aims `margin` after a content update: c_ets + k * 30 s, the first one that
    is still fresh when the 60 s rate limit allows the next request. Phase drift is
    corrected every fetch since the aim is relative to the data's own timestamp.

    Whatever stamp a fetch returns is snapped onto the content lattice by whole 15 s
    stamps relative to the aim, so arriving a stamp early or late doesn't flip parity.
    Arriving early (a stamp short of the aim) bumps the margin by MARGIN_UP_S; otherwise
    it shrinks by MARGIN_DOWN_S, probing back toward lower latency.

    The parity (which stamps carry new content) can't be seen at 1 fetch/min, so at
    startup and every 15 min we take a second fetch one stamp after a normal one: if content
    changed, the second is a content stamp, else the first was. (feed_loop skips a slot
    beforehand to make quota room for it; RateLimiter is the hard guarantee.)
    """

    def __init__(self, margin: float):
        self.margin = margin
        self.c_ets = None    # last known content-update stamp
        self.aimed = None    # content stamp the last fetch was aimed at
        self.next_check = 0.0

    def content_stamp(self, ets):
        """The content stamp at which this fetch's content first appeared, or None if the
        parity isn't known yet. Snaps onto the content lattice: an in-between stamp (early
        or late by one) repeats the content of the stamp before it."""
        if ets is None or self.aimed is None:
            return None
        return ets - STAMP_S if round((ets - self.aimed) / STAMP_S) % 2 else ets

    def observe(self, ets) -> None:
        """Account for a normal fetch's entity timestamp."""
        if ets is None:
            return
        if self.aimed is None:
            self.c_ets = ets  # parity unknown; the parity check sorts it out
            return
        stamps_off = round((ets - self.aimed) / STAMP_S)
        if stamps_off < 0:
            self.margin = min(self.margin + MARGIN_UP_S, MARGIN_MAX_S)
        else:
            self.margin = max(self.margin - MARGIN_DOWN_S, MARGIN_MIN_S)
        # ets itself, not aimed, sets the new phase, so drift is absorbed.
        self.c_ets = self.content_stamp(ets)

    def parity_result(self, ets_a, dig_a, ets_b, dig_b, now: float) -> str:
        if None in (ets_a, dig_a, ets_b, dig_b) or round((ets_b - ets_a) / STAMP_S) != 1:
            if ets_b is not None and ets_a is not None and ets_b <= ets_a:
                self.margin = min(self.margin + MARGIN_UP_S, MARGIN_MAX_S)  # B was early
            self.next_check = now + PARITY_RETRY_S
            return "inconclusive"
        self.next_check = now + PARITY_CHECK_EVERY_S
        if dig_b != dig_a:
            self.c_ets = ets_b
            return "second is content stamp"
        self.c_ets = ets_a
        return "first is content stamp"

    def check_due(self, now: float) -> bool:
        return self.c_ets is not None and now >= self.next_check

    def next_time(self, earliest: float) -> float:
        if self.c_ets is None or earliest - self.c_ets > 600:
            self.aimed = None
            return earliest
        # First content update that will still be fresh (within STALE_S past margin) at
        # `earliest`; fetch margin after it lands, but never before `earliest`.
        k = max(1, math.ceil((earliest - STALE_S - self.margin - self.c_ets) / CONTENT_S))
        self.aimed = self.c_ets + k * CONTENT_S
        return max(self.aimed + self.margin, earliest)


class RateLimiter:
    """Hard per-key guarantee: at most QUOTA requests in any rolling hour."""

    QUOTA, WINDOW_S = 60, 3600.0

    def __init__(self, history=()):
        self.times = sorted(history)[-self.QUOTA:]

    def allowed_at(self) -> float:
        """Earliest time the next request may be made."""
        return self.times[-self.QUOTA] + self.WINDOW_S if len(self.times) >= self.QUOTA else 0.0

    def record(self, t: float) -> None:
        self.times = (self.times + [t])[-self.QUOTA:]


def recent_requests(data_dir: Path, feed: str) -> list:
    """Request times for this feed's key in the last hour, from the fetch logs, so a
    restart doesn't overspend the quota."""
    cutoff = time.time() - RateLimiter.WINDOW_S
    since = datetime.fromtimestamp(cutoff, timezone.utc)
    kinds = {feed} | ({"gtfs_static"} if feed == STATIC_SLOT_FEED else set())
    now = datetime.now(timezone.utc)
    dirs = {data_dir / "rt" / service_date(now - timedelta(hours=h)) for h in (0, 1)}
    dirs.add(data_dir / "gtfs")
    out = []
    for d in dirs:
        for r in read_log(d, since):
            try:
                if r.get("feed") in kinds and "request_utc" in r:
                    t = datetime.fromisoformat(r["request_utc"]).timestamp()
                    if t > cutoff:
                        out.append(t)
            except (ValueError, TypeError):
                continue
    return out


def feed_loop(feed: str, args, stop: threading.Event) -> None:
    limiter = RateLimiter(recent_requests(args.data_dir, feed))
    sched = max(time.time() + (args.stagger if feed == STATIC_SLOT_FEED else 0.0), limiter.allowed_at())
    s = Scheduler(args.margin)
    cycles = 0
    while not stop.is_set():
        stop.wait(max(0.0, sched - time.time()))
        if stop.is_set():
            break
        use_static = feed == STATIC_SLOT_FEED and (
            not have_static_today(args.data_dir, args.static_in_bucket)
            and datetime.now(TZ).hour >= STATIC_LOCAL_HOUR)
        if use_static:
            limiter.record(fetch_static(args.data_dir, sched)[0])
        else:
            # content_ets (logged) = content stamp of what this fetch got; None until the
            # parity is known. Probes don't log it: their parity_check event settles both.
            state = {"role": "normal", "margin_s": round(s.margin, 3), "aimed_ets": s.aimed,
                     "content_of": s.content_stamp}
            t_a, ets_a, dig_a = fetch_rt(feed, args.data_dir, sched, state)
            limiter.record(t_a)
            s.observe(ets_a)
            # Parity check: one more fetch a stamp later, with extra margin since only
            # landing after the stamp matters. Skipped (and retried next cycle, after
            # skipping a slot to make room) if the quota doesn't allow it right now.
            t_b = (ets_a or 0) + STAMP_S + s.margin + 2.0
            if ets_a is not None and s.check_due(time.time()) and limiter.allowed_at() <= t_b:
                stop.wait(max(0.0, t_b - time.time()))
                if stop.is_set():
                    break
                state = {"role": "parity_probe", "margin_s": round(s.margin + 2.0, 3)}
                t_b, ets_b, dig_b = fetch_rt(feed, args.data_dir, t_b, state)
                limiter.record(t_b)
                result = s.parity_result(ets_a, dig_a, ets_b, dig_b, time.time())
                append_log(rt_log_dir(args.data_dir), {
                    "event": "parity_check", "feed": feed, "utc": datetime.now(timezone.utc).isoformat(),
                    "ets_a": ets_a, "ets_b": ets_b, "changed": dig_a != dig_b, "result": result})
                print(f"  [{feed}] parity check: {ets_a} -> {ets_b}, "
                      f"content {'changed' if dig_a != dig_b else 'same'}: {result}", flush=True)
        # Normally 60 s between fetches; if a parity check will be due at the next fetch,
        # skip a slot first so the quota has room for its probe.
        gap = MIN_INTERVAL_S * (2 if s.check_due(sched + MIN_INTERVAL_S) else 1)
        sched = s.next_time(max(sched + gap, limiter.allowed_at()))
        if args.verbose:
            print(f"  [{feed}] c_ets={s.c_ets} aimed={s.aimed} margin={s.margin:.2f}s "
                  f"next in {sched - time.time():.1f}s", flush=True)
        cycles += 1
        if args.max_minutes and cycles >= args.max_minutes:
            break


crashed = threading.Event()


def run_thread(target, *a) -> threading.Thread:
    """Start a thread. If it raises, the traceback is printed and `crashed` set, so main()
    exits nonzero and systemd restarts us, rather than one feed silently stopping while the
    other keeps the process alive."""
    def wrapped():
        try:
            target(*a)
        except BaseException:
            import traceback
            traceback.print_exc()
            sys.stdout.flush()
            crashed.set()
    t = threading.Thread(target=wrapped, name=getattr(target, "__name__", "thread"), daemon=True)
    t.start()
    return t


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "data")))
    ap.add_argument("--margin", type=float, default=MARGIN_S,
                    help="initial seconds after the predicted content update to fetch")
    ap.add_argument("--stagger", type=float, default=0.0,
                    help="delay the first vehiclepositions fetch by this many seconds")
    ap.add_argument("--max-minutes", type=int, default=0, help="stop after N fetch cycles per feed (0 = forever)")
    ap.add_argument("--bucket", help="upload finished files create-only to this GCS bucket, then delete "
                                     "them locally once older than the rate-limit window")
    ap.add_argument("--metrics", action="store_true",
                    help="report successful fetches per feed to Cloud Monitoring (for alerting)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print scheduler state each cycle")
    args = ap.parse_args()

    load_dotenv(Path(__file__).with_name(".env.local"))
    for _, key_env in RT_FEEDS.values():
        if not os.environ.get(key_env):
            sys.exit(f"missing {key_env} (put it in .env.local)")
    if gtfs_realtime_pb2 is None:
        print("warning: gtfs-realtime-bindings not importable; using plain 60 s cadence", flush=True)
    print(f"instance {INSTANCE}, data in {args.data_dir}", flush=True)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())  # systemd stop: flush, then exit
    uploader = None
    args.static_in_bucket = None
    if args.bucket or args.metrics:
        import fetch_cloud  # only needed (with its google-cloud deps) in cloud mode
    if args.bucket:
        uploader = fetch_cloud.Uploader(args.data_dir, args.bucket, INSTANCE)
        args.static_in_bucket = uploader.has_static
        run_thread(uploader.run, stop)
    if args.metrics:
        run_thread(fetch_cloud.Metrics(INSTANCE, fetch_counts, log_lock, list(RT_FEEDS)).run, stop)

    feeds = [run_thread(feed_loop, f, args, stop) for f in RT_FEEDS]
    try:
        while not stop.is_set() and not crashed.is_set() and any(t.is_alive() for t in feeds):
            stop.wait(0.5)
    except KeyboardInterrupt:
        pass
    stop.set()
    for t in feeds:
        t.join(timeout=40)  # an in-flight request times out after 30 s
    if uploader is not None:
        print("final upload...", flush=True)
        uploader.upload_pass(final=True)
    sys.exit(1 if crashed.is_set() else 0)


if __name__ == "__main__":
    main()
