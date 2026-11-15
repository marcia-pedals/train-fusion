"""Camera image sources: how to request an image, and how to judge what came back.

A source knows its host's politeness rules (min_gap_s between requests), whether the
server honours conditional requests, and how to read the capture time and recognise an
"offline" placeholder from metadata alone (headers and JPEG headers; no pixel decoding).

judge() returns a dict that goes into the fetch log, with at least `quality`:
    ok           a new camera image
    stale        a new file, but its capture time is long before the request (the
                 server keeps re-serving an old frame)
    placeholder  the source's "camera unavailable" image
    not_image    200 but not a JPEG (an HTML error page, say)
    truncated    a JPEG without its end marker
plus, where known: capture_utc (unix s), capture_src (where it came from), age_s
(request time - capture time), exif_lag_s (server stamp - camera/encoder stamp).
fetch.py adds "unchanged" (304, or the same bytes again) and "error".
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from fetchers.cams.jpeg import JpegInfo

UA = "train-fusion-archiver/0.1 (low-rate webcam archiver; one request per image refresh)"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
TZ = ZoneInfo("America/Los_Angeles")
STALE_AFTER_PERIODS = 3


@dataclass
class Response:
    status: int | None
    headers: dict
    body: bytes


def http_get(url: str, headers: dict, timeout: float = 30.0) -> Response:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Response(r.status, dict(r.headers), r.read())
    except urllib.error.HTTPError as e:
        return Response(e.code, dict(e.headers or {}), e.read() if e.code != 304 else b"")


def header_time(h: dict, name: str) -> float | None:
    v = {k.lower(): x for k, x in h.items()}.get(name)
    try:
        return parsedate_to_datetime(v).timestamp() if v else None
    except (TypeError, ValueError, IndexError):
        return None


def exif_local(s: str | None) -> float | None:
    """An EXIF "YYYY:MM:DD HH:MM:SS" in Pacific local time, as unix seconds."""
    try:
        return datetime.strptime(s, "%Y:%m:%d %H:%M:%S").replace(tzinfo=TZ).timestamp() if s else None
    except ValueError:
        return None


class Source:
    host = ""
    min_gap_s = 1.0           # between requests to this host
    timeout_s = 30.0
    if_modified_since = True  # send If-Modified-Since (some servers only match exactly)
    concurrency = 1           # requests in flight to this host
    margin_s = 4.0            # initial delay after an image's capture time to fetch it
    live = False              # a fresh frame on every request (no refresh cycle to lock onto)

    def request(self, cam) -> tuple[str, dict]:
        return cam.url, {}

    def ext(self, info: JpegInfo | None) -> str:
        return "jpg" if info else "bin"

    def judge(self, cam, resp: Response, info: JpegInfo | None, now: float) -> dict:
        if resp.status != 200:
            return {"quality": "error"}
        if info is None:
            return {"quality": "not_image"}
        if not info.complete:
            return {"quality": "truncated"}
        return {"quality": "ok"}

    def observed(self, cam, resp: Response, info: JpegInfo | None) -> None:
        """Called after every new image (e.g. to keep a session alive)."""

    def failed(self, cam, resp: Response) -> None:
        """Called after an error response (e.g. to drop an expired key)."""


class Caltrans(Source):
    """Caltrans CCTV stills (cwwp2.dot.ca.gov, nginx).

    Measured 2026-11-11, District 4:
    - The image is regenerated every 300 s (±1 s), each camera on its own phase.
      Last-Modified is the regeneration time; the EXIF DateTimeOriginal is the frame's
      grab time (Pacific local), usually a few seconds earlier. When EXIF is minutes older
      than Last-Modified, the server has re-stamped an old frame.
    - But the new file only appears ~25-35 s after its Last-Modified (up to ~60 s seen),
      so the fetch aims ~30 s after the stamp (margin_s; schedule.py adapts it).
    - ETag/If-None-Match gives 304s. If-Modified-Since only matches the exact time
      (nginx default), so we don't send it.
    - A camera that's down is replaced by a 320x260 "Temporarily Unavailable" image with a
      fresh banner and a small error code (1, 2, 4 seen), re-made every 300 s like a real
      one. It's 13.4-13.8 kB with quantization tables 931afd90; real frames from the
      encoder that shares those tables (tv458) were >= 20 kB.
    - `previous/<name>-N.jpg` (N=1..12) are history images 15 min apart, but for some
      cameras they hold years-old frames stamped 1970 / 2041, so we don't use them.
    - The in-image banner clock of some cameras is an hour off (no DST); headers aren't.
    """
    host = "cwwp2.dot.ca.gov"
    min_gap_s = 1.0
    margin_s = 30.0
    if_modified_since = False
    PLACEHOLDER_QT = {"931afd90"}
    PLACEHOLDER_BYTES = (13000, 14200)

    def judge(self, cam, resp, info, now):
        base = super().judge(cam, resp, info, now)
        if base["quality"] != "ok":
            return base
        lm = header_time(resp.headers, "last-modified")
        ex = exif_local(info.exif_time)
        out = {"capture_utc": lm, "capture_src": "last-modified" if lm else None}
        if lm is None and ex is not None:
            out.update(capture_utc=ex, capture_src="exif")
        if lm is not None and ex is not None:
            out["exif_lag_s"] = round(lm - ex, 1)
        cap = out["capture_utc"]
        if cap is not None:
            out["age_s"] = round(now - cap, 1)
        lo, hi = self.PLACEHOLDER_BYTES
        if info.qt in self.PLACEHOLDER_QT and lo <= len(resp.body) <= hi:
            out["quality"] = "placeholder"
        elif (cap is not None and now - cap > STALE_AFTER_PERIODS * cam.period_s) or \
                (out.get("exif_lag_s") or 0) > STALE_AFTER_PERIODS * cam.period_s:
            out["quality"] = "stale"
        else:
            out["quality"] = "ok"
        return out


class TrafficLand(Source):
    """Santa Clara County expressway cameras, as published by TrafficLand
    (ie.trafficland.com), with the per-camera token WeatherBug's camera pages embed.

    Measured 2026-11-11:
    - Each request returns a fresh frame (a new JPEG every request at a 2 s interval,
      the rate WeatherBug's own viewer polls at): there is no refresh cycle to lock onto,
      and no capture time at all (no Last-Modified/ETag/EXIF; Cache-Control max-age=2), so
      the capture time is taken as the request time. Requests take 0.9-2.3 s.
    - The `Image-Status` header mirrors the outcome. A camera that's down: HTTP 404,
      Image-Status 404, and an 18,283-byte "This image is temporarily unavailable" PNG.
      A bad token: HTTP 401, Image-Status 401, a PNG.
    - Tokens: https://www.weatherbug.com/traffic-cam/<anything>/<id> embeds
      `id=<id>&key=<64 hex>` for the camera and ~30 nearby ones. They expire: all of them
      had changed between 12:30 and 14:00 on 2026-11-11 (a stale one gets HTTP 401). We
      re-read the page when a token is refused, at most every 5 min per camera.
    """
    host = "ie.trafficland.com"
    min_gap_s = 1.0
    concurrency = 2  # a down camera's 404 takes ~5.5 s; don't let it hold up the live ones
    live = True
    timeout_s = 20.0
    if_modified_since = False
    SIZE = "huge"  # 640x480 here; "full"/"half" are smaller
    TOKEN_PAGE = "https://www.weatherbug.com/traffic-cam/mountain-view-ca-94043/{id}"
    TOKEN_RE = re.compile(r"id=(\d+)(?:&|&amp;|\\u0026)key=([0-9a-f]{64})")
    TOKEN_MIN_GAP_S = 300.0  # per camera, between token page reads

    def __init__(self):
        self.tokens: dict[str, str] = {}
        self.token_read: dict[str, float] = {}

    def token(self, cam_ref: str) -> str:
        if cam_ref not in self.tokens:
            last = self.token_read.get(cam_ref, 0.0)
            if time.time() - last < self.TOKEN_MIN_GAP_S:
                raise RuntimeError(f"no token for {cam_ref} (page read {time.time() - last:.0f}s ago)")
            self.token_read[cam_ref] = time.time()
            r = http_get(self.TOKEN_PAGE.format(id=cam_ref), {"User-Agent": BROWSER_UA}, timeout=30)
            if r.status != 200:
                raise RuntimeError(f"token page: HTTP {r.status}")
            # the page also carries nearby cameras' tokens: take those too (one read, many
            # cams), replacing older ones, which expire (rotated within ~1.5 h on 2026-11-11)
            for i, k in self.TOKEN_RE.findall(r.body.decode("utf-8", "replace")):
                self.tokens[i] = k
            if cam_ref not in self.tokens:
                raise RuntimeError(f"token page has no key for {cam_ref}")
        return self.tokens[cam_ref]

    def request(self, cam):
        ref = cam.url  # the TrafficLand camera id
        url = (f"https://ie.trafficland.com/v2.0/{ref}/{self.SIZE}?system=weatherbug-web"
               f"&pubtoken={self.token(ref)}&refreshRate=2000")
        # their viewer is a web page; look like one (and say where it's embedded)
        return url, {"User-Agent": BROWSER_UA, "Referer": "https://www.weatherbug.com/"}

    def ext(self, info):
        return "jpg" if info else "png"

    def judge(self, cam, resp, info, now):
        h = {k.lower(): v for k, v in resp.headers.items()}
        img = h.get("image-status")
        out = {"image_status": img}
        if resp.status == 404 and img == "404":
            return {**out, "quality": "placeholder"}
        if resp.status != 200:
            return {**out, "quality": "error"}
        base = super().judge(cam, resp, info, now)
        return {**out, **base, "capture_utc": None, "capture_src": "request"}

    def failed(self, cam, resp):
        if resp.status in (401, 403):
            self.tokens.pop(cam.url, None)  # read it again (rate-limited by TOKEN_MIN_GAP_S)


SOURCES: dict[str, Source] = {
    "caltrans": Caltrans(),
    "trafficland": TrafficLand(),
}
