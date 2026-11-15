#!/usr/bin/env python3
"""Archive webcam images that can see the Caltrain tracks.

    python -m fetchers.cams.fetch                # every enabled camera, forever
    python -m fetchers.cams.fetch --cams caltrans-tv922 --max-minutes 30 -v

Layout (under DATA_DIR, default ./data, gitignored), mirroring the GTFS archiver:

    data/cams/<service_date>/<cam_id>/<utc_stamp>.<ext>   raw response body, byte-for-byte
    data/cams/<service_date>/fetch_log/<utc_minute>_<instance>.jsonl
                                    one line per request: timing, status, headers, the
                                    capture time and quality judgment, scheduler state
    data/cams/catalog/<local_date>/<source>_<utc_stamp>.json   camera catalogs, daily

<utc_stamp> is the request time. Only images that are new are written: a 304, or a 200 with
the same bytes as the camera's previous image, is logged (`quality: "unchanged"`) but not
stored again. Non-200 bodies are kept with a `.http<status>` suffix, as in the GTFS archive.

Each camera is fetched about once per refresh of its image, just after the refresh (see
schedule.py), with conditional requests (ETag / Last-Modified) where the server supports
them. Requests to one host go one at a time, at least `min_gap_s` apart, and the whole host
pauses on 403/429. The camera list and what each source needs are in cams.py / sources.py.

The quality of each capture is judged from metadata only (no pixels are decoded): see
`sources.Source.judge` and the `quality` field of the log.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import os
import signal
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from fetchers.archive import INSTANCE, append_log
from fetchers.cams import jpeg
from fetchers.cams import alertca
from fetchers.cams.cams import ALERTCA, CAMS, CATALOGS, Cam
from fetchers.cams.passes import PassPredictor
from fetchers.cams.schedule import WINDOW_AFTER_S, WINDOW_BEFORE_S, CamSchedule, jitter, live_next
from fetchers.cams.sources import SOURCES, Response, http_get
from fetchers.gtfs.fetch import TZ, service_date, stamp

TOP = "cams"
KEEP_HEADERS = ("date", "content-type", "content-length", "last-modified", "etag",
                "cache-control", "expires", "age", "retry-after", "x-cache", "server")
HOST_PAUSE_S = (300.0, 6 * 3600.0)  # a host's pause on 403/429 doubles from the first to the second

fetch_counts = Counter()  # cam id -> new images archived (for --metrics)
counts_lock = threading.Lock()


def parse_http_date(s: str | None) -> float | None:
    try:
        return parsedate_to_datetime(s).timestamp() if s else None
    except (TypeError, ValueError, IndexError):
        return None


def retry_after(headers: dict) -> float | None:
    v = headers.get("retry-after")
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        t = parse_http_date(v)
        return max(0.0, t - time.time()) if t else None


def write_once(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(body)
    tmp.rename(path)


class CamState:
    """What we remember about one camera between fetches."""

    def __init__(self, cam: Cam):
        self.cam = cam
        self.sched = CamSchedule(cam.period_s, SOURCES[cam.source].margin_s)
        self.etag = None
        self.last_modified = None    # the header, verbatim, for If-Modified-Since
        self.sha = None              # of the last body stored
        self.capture = None          # server capture time of the last image


def fetch_cam(st: CamState, data_dir: Path, scheduled: float, verbose: bool,
              predictor: PassPredictor | None = None) -> tuple[float, int | None]:
    """Fetch one camera once: store, log, and return (next fetch time, HTTP status)."""
    cam = st.cam
    src = SOURCES[cam.source]
    t0 = datetime.now(timezone.utc)
    m0 = time.monotonic()
    err = None
    try:
        url, headers = src.request(cam)
        if st.etag:
            headers["If-None-Match"] = st.etag
        if st.last_modified and src.if_modified_since:
            headers["If-Modified-Since"] = st.last_modified
        resp = http_get(url, headers, timeout=src.timeout_s)
    except Exception as e:  # network errors, timeouts, a source's key refresh failing
        resp, err = Response(None, {}, b""), f"{type(e).__name__}: {e}"
    elapsed = time.monotonic() - m0
    now = time.time()
    h = {k.lower(): v for k, v in resp.headers.items()}
    body = resp.body
    sha = hashlib.sha256(body).hexdigest() if body else None
    info = jpeg.parse(body) if resp.status == 200 and body else None
    j = src.judge(cam, resp, info, now) if body else {}
    if resp.status != 200 and j.get("quality") != "placeholder":
        j["quality"] = "error"
    quality = j.get("quality", "error")
    capture = j.get("capture_utc")
    if j.get("capture_src") == "request":  # no capture time: mid-request is our best guess
        capture = j["capture_utc"] = round(t0.timestamp() + elapsed / 2, 3)
    day_dir = data_dir / TOP / service_date(t0)

    path = None
    if resp.status == 304 or (body and sha == st.sha):
        # 304, or the same bytes as the last body we stored: not stored again
        quality = "unchanged" if quality != "placeholder" else quality
        nxt = st.sched.same_image(now) if quality == "unchanged" else st.sched.placeholder(now, cam.recheck_s)
    elif quality in ("ok", "stale", "placeholder"):
        suffix = src.ext(info) if resp.status == 200 else f"{src.ext(info)}.http{resp.status}"
        path = day_dir / cam.id / f"{stamp(t0)}.{suffix}"
        write_once(path, body)
        st.sha = sha
        st.etag = h.get("etag")
        st.last_modified = h.get("last-modified")
        if quality == "placeholder":
            nxt = st.sched.placeholder(now, cam.recheck_s)
        else:
            nxt = st.sched.new_image(now, capture if not src.live else None)
            if quality == "ok":
                with counts_lock:
                    fetch_counts[cam.id] += 1
        if capture is not None:
            st.capture = capture
        src.observed(cam, resp, info)
    else:  # an error, or a 200 that isn't a (whole) image
        if body:  # keep error bodies, as the GTFS archive does
            suffix = f"http{resp.status}" if resp.status != 200 else f"{quality}.bin"
            path = day_dir / cam.id / f"{stamp(t0)}.{suffix}"
            write_once(path, body)
        src.failed(cam, resp)
        nxt = st.sched.failed(now, retry_after(h))

    why = None
    if src.live and quality in ("ok", "stale", "unchanged"):
        windows = predictor.windows(cam.km, WINDOW_BEFORE_S, WINDOW_AFTER_S) \
            if predictor is not None and cam.km is not None else None
        # the interval runs from the start of this request (they take 1-2 s)
        nxt, why = live_next(t0.timestamp(), windows, datetime.now(TZ).hour, cam.period_s)
        nxt = max(nxt, now + 0.5)

    rec = {
        "cam": cam.id,
        "source": cam.source,
        "scheduled_utc": datetime.fromtimestamp(scheduled, timezone.utc).isoformat(),
        "request_utc": t0.isoformat(),
        "elapsed_s": round(elapsed, 3),
        "status": resp.status,
        "bytes": len(body),
        "sha256": sha,
        "file": str(path) if path else None,
        "quality": quality,
        **{k: v for k, v in j.items() if k != "quality"},
        "jpeg": info.as_dict() if info else None,
        "headers": {k: v for k, v in h.items() if k in KEEP_HEADERS},
        "error": err,
        "sched": st.sched.state,
        "cadence": why,
        "next_in_s": round(nxt - now, 1),
    }
    append_log(day_dir, rec)
    if verbose or quality not in ("ok", "unchanged"):
        age = j.get("age_s")
        print(f"{t0.astimezone(TZ):%H:%M:%S}", f"{cam.id:28s}", resp.status, f"{len(body):7d}B",
              f"{elapsed:5.2f}s", f"{quality:11s}", f"age={age}" if age is not None else "",
              f"next {nxt - now:5.0f}s", why or "", err or "", flush=True)
    return nxt, resp.status


class Host:
    """One host's cameras: a queue of (due time, camera), served by `concurrency`
    workers, each request at least min_gap_s after the previous one started, and the
    whole host paused after a 403/429."""

    def __init__(self, host: str, cams: list[Cam], args, predictor):
        self.host, self.args, self.predictor = host, args, predictor
        srcs = {SOURCES[c.source] for c in cams}
        self.min_gap = max(s.min_gap_s for s in srcs)
        self.concurrency = min(s.concurrency for s in srcs)
        self.states = {c.id: CamState(c) for c in cams}
        # Spread the first round out rather than starting with a burst.
        spread = min(60.0, self.min_gap * len(cams) * 2)
        self.queue = [(time.time() + spread * k / len(cams), c.id) for k, c in enumerate(cams)]
        heapq.heapify(self.queue)
        self.cv = threading.Condition()
        self.last_req = 0.0
        self.pause_until, self.pauses = 0.0, 0
        self.deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None

    def worker(self, stop: threading.Event) -> None:
        while not stop.is_set():
            with self.cv:
                while True:
                    if stop.is_set():
                        return
                    if not self.queue:
                        if not self.cv.wait(1.0) and self.deadline and time.time() > self.deadline:
                            return
                        continue
                    due, cid = self.queue[0]
                    start = max(due, self.last_req + self.min_gap, self.pause_until)
                    if self.deadline and start > self.deadline:
                        return
                    if start <= time.time():
                        heapq.heappop(self.queue)
                        self.last_req = time.time()
                        break
                    self.cv.wait(min(start - time.time(), 1.0))
            nxt, status = fetch_cam(self.states[cid], self.args.data_dir, due, self.args.verbose,
                                    self.predictor)
            with self.cv:
                if status in (403, 429):
                    self.pauses += 1
                    pause = min(HOST_PAUSE_S[0] * 2 ** (self.pauses - 1), HOST_PAUSE_S[1])
                    self.pause_until = time.time() + jitter(pause, 0.2)
                    print(f"  [{self.host}] HTTP {status}: pausing the host for {pause:.0f}s", flush=True)
                elif status in (200, 304):
                    self.pauses = 0
                heapq.heappush(self.queue, (nxt, cid))
                self.cv.notify_all()


def catalog_loop(args, stop: threading.Event) -> None:
    """Archive each source's camera catalog once a (local) day: it records which cameras
    the operator says are in service, and any URL changes."""
    done: dict[str, str] = {}
    while not stop.is_set():
        day = datetime.now(TZ).strftime("%Y-%m-%d")
        for name, url in CATALOGS.items():
            if done.get(name) == day:
                continue
            t0 = datetime.now(timezone.utc)
            try:
                resp = http_get(url, {}, timeout=60)
                status, body, err = resp.status, resp.body, None
            except Exception as e:
                status, body, err = None, b"", f"{type(e).__name__}: {e}"
            path = None
            if status == 200 and body:
                path = args.data_dir / TOP / "catalog" / day / f"{name}_{stamp(t0)}.json"
                write_once(path, body)
                done[name] = day
            append_log(args.data_dir / TOP / "catalog", {
                "catalog": name, "request_utc": t0.isoformat(), "status": status,
                "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest() if body else None,
                "file": str(path) if path else None, "error": err})
        stop.wait(jitter(3600.0))


crashed = threading.Event()


def run_thread(target, *a) -> threading.Thread:
    """As in the GTFS fetcher: a crashed thread makes the process exit nonzero (systemd
    restarts it) rather than leaving some cameras silently unfetched."""
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
    ap.add_argument("--rt-dir", type=Path, help="data dir whose rt/ holds the GTFS archiver's trip "
                                              "updates, for pass prediction (default: --data-dir)")
    ap.add_argument("--cams", help="comma-separated camera ids (default: every enabled camera)")
    ap.add_argument("--all", action="store_true", help="include cameras that aren't enabled")
    ap.add_argument("--source", help="only cameras from this source (e.g. caltrans, trafficland)")
    ap.add_argument("--max-minutes", type=float, default=0, help="stop after this long (0 = forever)")
    ap.add_argument("--no-catalog", action="store_true", help="don't archive the camera catalogs")
    ap.add_argument("--bucket", help="upload finished files create-only to this GCS bucket (see fetchers/cloud.py)")
    ap.add_argument("--list", action="store_true", help="list the cameras and exit")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every fetch")
    args = ap.parse_args()

    if args.list:
        for c in CAMS:
            print(f"{'*' if c.enabled else ' '} {c.id:28s} {c.sees_track:5s} {c.lat:.5f},{c.lon:.5f}  {c.name}")
        return
    if args.cams:
        want = set(args.cams.split(","))
        unknown = want - {c.id for c in CAMS}
        if unknown:
            sys.exit(f"unknown cameras: {', '.join(sorted(unknown))} (see --list)")
        cams = [c for c in CAMS if c.id in want]
    else:
        cams = [c for c in CAMS if c.enabled or args.all]
    if args.source:
        cams = [c for c in cams if c.source == args.source]
    by_host = defaultdict(list)
    for c in cams:
        if c.source in SOURCES:
            by_host[SOURCES[c.source].host].append(c)
    ac_cams = [c for c in cams if c.source == "alertcalifornia"]
    print(f"instance {INSTANCE}, {len(cams)} cameras on {len(by_host)} hosts, data in {args.data_dir}", flush=True)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    uploader = None
    if args.bucket:
        from fetchers import cloud
        uploader = cloud.Uploader(args.data_dir, args.bucket, INSTANCE, tops=(TOP,))
        run_thread(uploader.run, stop)
    if not args.no_catalog and not args.max_minutes:
        run_thread(catalog_loop, args, stop)
    predictor = PassPredictor(args.rt_dir or args.data_dir)
    loops = []
    deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None
    for c in ac_cams:
        loops.append(run_thread(alertca.Poller(c, ALERTCA[c.id], args.data_dir, args.verbose).run, stop, deadline))
    for host, hc in by_host.items():
        h = Host(host, hc, args, predictor)
        loops += [run_thread(h.worker, stop) for _ in range(h.concurrency)]
    try:
        while not stop.is_set() and not crashed.is_set() and any(t.is_alive() for t in loops):
            stop.wait(0.5)
    except KeyboardInterrupt:
        pass
    stop.set()
    for t in loops:
        t.join(timeout=60)
    if uploader is not None:
        print("final upload...", flush=True)
        uploader.upload_pass(final=True)
    sys.exit(1 if crashed.is_set() else 0)


if __name__ == "__main__":
    main()
