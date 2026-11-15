"""ALERTCalifornia (UC San Diego) wildfire cameras: pan-tilt-zoom cameras on hilltops and
towers whose frames are archived on their side, so we read their frame lists rather than
polling an image.

Measured 2026-11-11 (sanbrunomtn-cam01, redwoodcity-cam02):
- Catalog: https://cameras.alertcalifornia.org/public-camera-data/all_cameras-v3.json
  (1310 cameras): location, current azimuth/tilt/zoom/fov and view footprint.
- Frame lists: public-camera-data/<cam>/10sec/{5,15,30}-min.json (a frame every ~11 s) and
  1min/{1,3,6,12}-hour.json (~1 per min): {"frames": [{"ts": unix s, "path": ...}]},
  Cache-Control max-age=10. Frames are 1920x1080 JPEGs, ~200-350 kB, kept 2 days
  (x-amz-expiration), immutable (ETag = content hash, in the name).
- Each frame's headers (so a HEAD is enough) carry x-amz-meta-context, whose
  X-Data-Context is base64 JSON: pan/tilt/zoom, is_pano, and the capture `timestamp`
  (sub-second, UTC). EXIF DateTime is Pacific local time, to the second.
- The cameras sit in a "home" pose most of the time and turn elsewhere for ~10 min at a
  time (by schedule or an operator). The per-frame pan/tilt/zoom is *not always the
  pose of that frame*: around moves it can belong to a neighbouring frame (the burned-in
  overlay disagreed by up to 35 deg): it seems to be the commanded pose, and a glance's
  frame can be grabbed mid-move or carry the previous glance's tag. So we keep a frame
  when it *or a neighbour* is tagged with a pose of interest, and log `pose_stable`
  (neighbours agree) for the later stage, which can read the burned-in overlay.
- The pattern on 2026-11-11: each camera holds its home pose and glances elsewhere for
  1-2 frames every ~2 min (redwoodcity-cam02 cycles through ~6 directions; sanbrunomtn
  glances wide at downtown SF, then its zoom-back frames are wide views over Bayshore).

Policy per camera (cams.Cam.url holds the ALERTCalifornia id; cams.ALERTCA):
    "all"       download every frame (the default: pose tags are too unreliable to filter
                on until a later stage reads the burned-in overlay; ~2.2 GB/day/camera)
    "track"     download frames tagged with one of the camera's track_poses, and neighbours
    "nonhome"   download every frame not tagged with the home pose, and its neighbours
Every frame is HEADed and logged (its pose), downloaded or not.

License: CC BY-NC-ND 4.0 (alertcalifornia.org/terms): non-commercial use, credit
"ALERTCalifornia | UC San Diego", no redistributed derivatives.
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fetchers.archive import append_log
from fetchers.cams import jpeg
from fetchers.cams.schedule import jitter
from fetchers.cams.sources import UA, Response, http_get
from fetchers.gtfs.fetch import TZ, service_date, stamp

BASE = "https://cameras.alertcalifornia.org"
LIST_EVERY_S = 300.0      # the 15-min list, every 5 min: each frame is seen in 3 lists
SHORT_LIST = "10sec/15-min.json"
GAP_LIST = "10sec/30-min.json"    # after a start or gap: every frame of the last 30 min...
LONG_LIST = "1min/12-hour.json"   # ...and ~1/min for the 12 h before (all that's listed)
MIN_GAP_S = 0.5           # between requests to the host (CloudFront)
BACKOFF_S = (30.0, 1800.0)
TOP = "cams"

# cam id -> {"home": pose, "track_poses": [pose, ...], "mode": "track" | "nonhome"}
# pose = (pan, tilt, zoom) as the frame metadata reports them; matched within POSE_TOL.
POSE_TOL = (1.0, 1.0, 0.2)  # degrees, degrees, relative zoom


def pose_of(headers: dict) -> dict:
    """pan/tilt/zoom/is_pano/capture time from a frame's x-amz-meta-context header."""
    h = {k.lower(): v for k, v in headers.items()}
    try:
        ctx = json.loads(h["x-amz-meta-context"])
        d = json.loads(base64.b64decode(ctx["headers"]["X-Data-Context"][0]))
        cap = datetime.fromisoformat(d["timestamp"][:26] + "+00:00").timestamp()
        return {"pan": float(d["pan"]), "tilt": float(d["tilt"]), "zoom": float(d["zoom"]),
                "is_pano": d.get("is_pano") == "yes", "capture_utc": round(cap, 3)}
    except (KeyError, ValueError, TypeError, IndexError):
        return {}


def same_pose(a, b) -> bool:
    if not a or not b or a.get("pan") is None or b.get("pan") is None:
        return False
    return (abs(a["pan"] - b["pan"]) <= POSE_TOL[0] and abs(a["tilt"] - b["tilt"]) <= POSE_TOL[1]
            and abs(a["zoom"] - b["zoom"]) <= POSE_TOL[2] * max(a["zoom"], b["zoom"]))


def matches(p, poses) -> bool:
    return any(same_pose(p, {"pan": q[0], "tilt": q[1], "zoom": q[2]}) for q in poses)


class Poller:
    """One camera: list, HEAD each new frame, decide, GET, store, log."""

    def __init__(self, cam, conf: dict, data_dir: Path, verbose=False, backfill=True):
        self.cam, self.conf, self.data_dir, self.verbose = cam, conf, data_dir, verbose
        self.backfill = backfill  # start from the 12-hour list (~1 frame/min) instead of 15 min
        self.ref = cam.url
        self.seen: dict[int, dict] = {}   # ts -> {"path", "pose", "done"}
        self.last_ts = None
        self.failures = 0
        self.last_req = 0.0

    def _get(self, url, method="GET"):
        wait = self.last_req + MIN_GAP_S - time.time()
        if wait > 0:
            time.sleep(wait)
        self.last_req = time.time()
        if method == "HEAD":
            import urllib.request
            import urllib.error
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return Response(r.status, dict(r.headers), b"")
            except urllib.error.HTTPError as e:
                return Response(e.code, dict(e.headers or {}), b"")
        return http_get(url, {}, timeout=60)

    def log(self, rec):
        t = datetime.now(timezone.utc)
        append_log(self.data_dir / TOP / service_date(t), {"cam": self.cam.id, "source": "alertcalifornia", **rec})

    def list_frames(self) -> list[dict]:
        """The frames listed now: the 15-min list normally; after a start (with backfill)
        or a gap, also the 30-min and 12-hour lists. Frames are kept 2 days, but only
        these lists name them (paths end in a content hash), so this is all we can get."""
        gap = time.time() - self.last_ts if self.last_ts else None
        if (gap is None and self.backfill) or (gap is not None and gap >= 14 * 60):
            specs = [LONG_LIST, GAP_LIST]
        elif gap is None:
            specs = [GAP_LIST]
        else:
            specs = [SHORT_LIST]
        frames = {}
        for spec in specs:
            t0 = time.time()
            r = self._get(f"{BASE}/public-camera-data/{self.ref}/{spec}?rqts={int(t0)}")
            got = []
            if r.status == 200:
                try:
                    got = json.loads(r.body)["frames"]
                except (ValueError, KeyError, TypeError):
                    r = Response(-1, r.headers, r.body)
            self.log({"event": "list", "spec": spec, "request_utc": datetime.fromtimestamp(t0, timezone.utc).isoformat(),
                      "elapsed_s": round(time.time() - t0, 3), "status": r.status, "frames": len(got),
                      "newest_ts": got[-1]["ts"] if got else None})
            if r.status != 200:
                raise IOError(f"list {spec}: HTTP {r.status}")
            frames.update({f["ts"]: f for f in got})
        return [frames[t] for t in sorted(frames)]

    def decide(self, ts: int) -> tuple[bool, bool]:
        """(download?, pose_stable) for a frame whose neighbours' poses are known."""
        keys = sorted(self.seen)
        i = keys.index(ts)
        p = self.seen[ts]["pose"]
        # neighbours: the adjacent ~11 s frames (not the 1-min frames of a backfill)
        nb = [self.seen[keys[j]]["pose"] for j in (i - 1, i + 1)
              if 0 <= j < len(keys) and abs(keys[j] - ts) <= 20]
        stable = bool(nb) and all(same_pose(p, q) for q in nb)
        mode = self.conf.get("mode", "all")
        if mode == "all":
            return True, stable
        home = dict(zip(("pan", "tilt", "zoom"), self.conf["home"]))
        if mode == "nonhome":
            # the camera glances away for a frame or two every ~2 min; a frame's pose tag
            # can belong to its neighbour, so keep the frames on either side of a glance too
            near = [p] + nb
            want = any(not same_pose(q, home) for q in near)
        else:
            want = matches(p, self.conf.get("track_poses", [])) or any(
                matches(q, self.conf.get("track_poses", [])) for q in nb)
        return want, stable

    def cycle(self) -> None:
        frames = self.list_frames()
        new = [f for f in frames if f["ts"] not in self.seen and f["ts"] > time.time() - 13 * 3600]
        for f in new:
            t0 = time.time()
            r = self._get(f"{BASE}/{f['path']}", "HEAD")
            pose = pose_of(r.headers) if r.status == 200 else {}
            self.seen[f["ts"]] = {"path": f["path"], "pose": pose, "done": False,
                                  "head": {"status": r.status, "elapsed_s": round(time.time() - t0, 3),
                                           "bytes": int(r.headers.get("Content-Length", r.headers.get("content-length", 0)) or 0),
                                           "etag": r.headers.get("ETag") or r.headers.get("etag")}}
        if frames:
            self.last_ts = max(self.last_ts or 0, frames[-1]["ts"])
        # decide every frame whose next neighbour is known (the newest waits for the next list)
        keys = sorted(self.seen)
        for ts in keys[:-1]:
            s = self.seen[ts]
            if s["done"]:
                continue
            s["done"] = True
            want, stable = self.decide(ts)
            rec = {"frame_ts": ts, "path": s["path"], **s["pose"], "pose_stable": stable, "head": s["head"],
                   "capture_lag_s": round(ts - s["pose"]["capture_utc"], 3) if s["pose"].get("capture_utc") else None}
            cap = s["pose"].get("capture_utc") or ts
            cap_dt = datetime.fromtimestamp(cap, timezone.utc)
            path = self.data_dir / TOP / service_date(cap_dt) / self.cam.id / f"{stamp(cap_dt)}.jpg"
            if want and path.exists():  # archived before (a restart's backfill)
                rec.update(event="frame", quality="have", file=str(path))
            elif want and s["head"]["status"] == 200:
                t0 = time.time()
                r = self._get(f"{BASE}/{s['path']}")
                info = jpeg.parse(r.body) if r.status == 200 else None
                if r.status == 200 and r.body:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_name(path.name + ".tmp")
                    tmp.write_bytes(r.body)
                    tmp.rename(path)
                else:
                    path = None
                rec.update(event="frame", status=r.status, elapsed_s=round(time.time() - t0, 3), bytes=len(r.body),
                           sha256=hashlib.sha256(r.body).hexdigest() if r.body else None,
                           file=str(path) if path else None, jpeg=info.as_dict() if info else None,
                           quality="ok" if info and info.complete else "error")
            else:
                rec.update(event="frame", quality="skipped" if s["head"]["status"] == 200 else "error",
                           why="pose" if s["head"]["status"] == 200 else "head failed")
            self.log(rec)
            if self.verbose and rec["quality"] not in ("skipped", "have"):
                print(f"{datetime.fromtimestamp(ts, TZ):%H:%M:%S} {self.cam.id:28s} {rec['quality']:8s} "
                      f"pan={s['pose'].get('pan')} tilt={s['pose'].get('tilt')} zoom={s['pose'].get('zoom')} "
                      f"stable={stable}", flush=True)
        # forget frames older than the long list
        for ts in [t for t in self.seen if t < time.time() - 13 * 3600]:
            del self.seen[ts]

    def run(self, stop: threading.Event, deadline: float | None = None) -> None:
        while not stop.is_set() and (deadline is None or time.time() < deadline):
            try:
                self.cycle()
                self.failures = 0
                delay = jitter(LIST_EVERY_S)
            except Exception as e:  # network, HTTP errors: back off
                self.failures += 1
                delay = jitter(min(BACKOFF_S[0] * 2 ** (self.failures - 1), BACKOFF_S[1]), 0.2)
                print(f"  [{self.cam.id}] {type(e).__name__}: {e}; retry in {delay:.0f}s", flush=True)
            stop.wait(delay)
