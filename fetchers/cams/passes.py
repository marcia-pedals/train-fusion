"""When trains will pass a camera, from the newest archived GTFS-RT trip updates.

Live cameras (a fresh frame on every request) are fetched fast only while a train is
expected in view, and slowly otherwise: a pass lasts seconds, the gaps between passes tens
of minutes, so this cuts requests ~5-10x for the same passes caught.

Positions along the line are km from San Francisco (4th & King), measured on the GTFS
shapes of feed 20260922 (SF-Tamien, then San Jose-Gilroy from Diridon). A train's time
at a camera is interpolated linearly between the predicted times at the stations on
either side of it. Trip updates list only the stops still ahead, so for a train whose
next stop is already past the camera, the time is extrapolated back from that stop at
LINE_SPEED_MPS.

This reads the files the GTFS archiver (fetchers/gtfs/fetch.py) writes on the same machine:
data/rt/<service_date>/tripupdates/*.pb. If there are none fresh enough, `windows()`
returns None and the caller falls back to fetching fast all through service hours.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fetchers.gtfs.fetch import service_date

# km from SF along the line, per station (stop_id without its platform digit:
# 7001x = San Francisco ... 7032x = Gilroy). Northbound and southbound platforms agree to
# within ~20 m, except Gilroy's (~180 m apart; the mean is used).
STATION_KM = {
    "7001": 0.00, "7002": 2.52, "7003": 7.94, "7004": 14.61, "7005": 17.65, "7006": 21.73,
    "7007": 24.29, "7008": 26.05, "7009": 28.37, "7010": 30.52, "7011": 31.87, "7012": 35.21,
    "7013": 37.20, "7014": 40.69, "7016": 46.29, "7017": 48.30, "7019": 50.84, "7020": 54.79,
    "7021": 57.89, "7022": 62.20, "7023": 65.39, "7024": 71.28, "7025": 73.53, "7026": 75.44,
    "7027": 78.28, "7028": 83.27, "7029": 88.57, "7030": 107.62, "7031": 113.62, "7032": 123.59,
}
LINE_SPEED_MPS = 25.0
FRESH_S = 300.0        # trip updates older than this are ignored
REREAD_S = 30.0        # re-read the newest trip-updates file at most this often

try:
    from google.transit import gtfs_realtime_pb2
except ImportError:
    gtfs_realtime_pb2 = None


def station_km(stop_id: str) -> float | None:
    return STATION_KM.get(stop_id[:4]) if stop_id.startswith("70") and len(stop_id) == 5 else None


def pass_times(feed, km: float, now: float) -> list[tuple[str, float]]:
    """(trip_id, predicted time at `km`) for each trip in a parsed FeedMessage that
    will still pass it. Times in the past are kept (a train may be in view now)."""
    out = []
    for e in feed.entity:
        if not e.HasField("trip_update"):
            continue
        pts = []
        for u in e.trip_update.stop_time_update:
            k = station_km(u.stop_id)
            t = u.departure.time or u.arrival.time
            if k is not None and t:
                pts.append((k, float(u.arrival.time or t), float(t)))
        if not pts:
            continue
        # order along the trip's own direction of travel (by time)
        pts.sort(key=lambda p: p[1])
        t_at = None
        for (k1, _, d1), (k2, a2, _) in zip(pts, pts[1:]):
            if min(k1, k2) <= km <= max(k1, k2):
                f = 0.0 if k2 == k1 else (km - k1) / (k2 - k1)
                t_at = d1 + f * (a2 - d1)
                break
        if t_at is None:
            k0, a0, d0 = pts[0]
            if abs(k0 - km) < 0.05:
                t_at = a0
            elif len(pts) == 1 or (pts[1][0] - k0) * (k0 - km) > 0:
                # the camera lies before the first remaining stop, in the direction of
                # travel: the train is either still short of it or already past it. The
                # extrapolated time tells which (past = gone, give or take a minute).
                t = a0 - abs(k0 - km) * 1000 / LINE_SPEED_MPS
                if t >= now - 60:
                    t_at = t
        if t_at is not None:
            out.append((e.trip_update.trip.trip_id, t_at))
    return out


def file_time(p: Path) -> float:
    """When the archiver fetched this file: its name is the request time (fetch.stamp).
    Not the mtime, which is the download time on a synced copy."""
    try:
        return datetime.strptime(p.name.split(".pb")[0], "%Y%m%dT%H%M%S.%fZ").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return p.stat().st_mtime


class PassPredictor:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.feed = None
        self.feed_file = None
        self.read_at = 0.0

    def _newest(self) -> Path | None:
        now = datetime.now(timezone.utc)
        for h in (0, 3):  # around the service-day change, the previous day's dir
            d = self.data_dir / "rt" / service_date(now - timedelta(hours=h)) / "tripupdates"
            if d.is_dir():
                files = sorted(p for p in d.iterdir() if p.suffix == ".pb")
                if files:
                    return files[-1]
        return None

    def _load(self) -> None:
        if time.time() - self.read_at < REREAD_S:
            return
        self.read_at = time.time()
        if gtfs_realtime_pb2 is None:
            self.feed = None
            return
        p = self._newest()
        if p is None or time.time() - file_time(p) > FRESH_S:
            self.feed = None
            return
        if p != self.feed_file:
            m = gtfs_realtime_pb2.FeedMessage()
            try:
                m.ParseFromString(p.read_bytes())
            except Exception:
                return  # keep the previous one; it ages out via FRESH_S
            self.feed, self.feed_file, self.feed_mtime = m, p, file_time(p)
        elif time.time() - self.feed_mtime > FRESH_S:
            self.feed = None

    def windows(self, km: float, before_s: float, after_s: float) -> list[tuple[float, float, str]] | None:
        """[(start, end, trip_id)] around each predicted pass of `km`, or None if there's
        no fresh trip-update data to predict from."""
        self._load()
        if self.feed is None:
            return None
        now = time.time()
        return sorted((t - before_s, t + after_s, trip) for trip, t in pass_times(self.feed, km, now)
                      if t + after_s > now)
