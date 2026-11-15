"""The webcam fetcher: JPEG header parsing, scheduling, judging, storing."""
import os
import struct
import time
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetchers.cams import fetch, jpeg, schedule, sources  # noqa: E402
from fetchers.cams.cams import Cam  # noqa: E402


# ------------------------------------------------------------------ a tiny JPEG writer
def make_jpeg(w=320, h=260, qt=b"\x01" * 64, exif_time=None, comment=None, scan=b"\x00" * 100,
              eoi=True) -> bytes:
    """Headers in the order cameras write them, a fake entropy-coded scan, and EOI."""
    def seg(marker, payload):
        return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload

    out = b"\xff\xd8" + seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    if exif_time:
        # little-endian TIFF: IFD0 with DateTime + ExifIFD pointer; sub-IFD with DateTimeOriginal
        dt = exif_time.encode() + b"\x00"  # 20 bytes
        ifd0 = 8
        sub = ifd0 + 2 + 2 * 12 + 4
        data0 = sub + 2 + 12 + 4
        data1 = data0 + len(dt)
        t = b"II*\x00" + struct.pack("<I", ifd0)
        t += struct.pack("<H", 2)
        t += struct.pack("<HHII", 0x0132, 2, len(dt), data0)
        t += struct.pack("<HHII", 0x8769, 4, 1, sub)
        t += struct.pack("<I", 0)
        t += struct.pack("<H", 1) + struct.pack("<HHII", 0x9003, 2, len(dt), data1) + struct.pack("<I", 0)
        t += dt + dt
        out += seg(0xE1, b"Exif\x00\x00" + t)
    if comment:
        out += seg(0xFE, comment.encode())
    out += seg(0xDB, b"\x00" + qt)
    out += seg(0xC0, b"\x08" + struct.pack(">HH", h, w) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    out += seg(0xDA, b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00") + scan
    return out + (b"\xff\xd9" if eoi else b"")


def test_jpeg_headers():
    b = make_jpeg(exif_time="2026:11:11 12:08:02", comment="County: SCL")
    i = jpeg.parse(b)
    assert (i.width, i.height, i.complete) == (320, 260, True)
    assert i.exif_time == "2026:11:11 12:08:02" and i.comment == "County: SCL"
    assert i.qt == jpeg.parse(make_jpeg()).qt != jpeg.parse(make_jpeg(qt=b"\x02" * 64)).qt


def test_jpeg_truncated_and_not_jpeg():
    assert jpeg.parse(make_jpeg(eoi=False)).complete is False
    assert jpeg.parse(make_jpeg()[:30]).complete is False
    assert jpeg.parse(b"<html>error</html>") is None


# ------------------------------------------------------------------ the schedule
def test_locks_onto_the_refresh_cycle():
    s = schedule.CamSchedule(300.0, margin_s=4.0)
    assert s.new_image(now=1000.0, capture=900.0) == pytest.approx(900 + 300 + 3.75)
    # a late fetch: the next one waits for the next refresh, from the capture time
    assert s.new_image(now=1210.0, capture=1200.0) == pytest.approx(1200 + 300 + 3.5)


def test_unchanged_ladder_then_widens_margin():
    s = schedule.CamSchedule(300.0, margin_s=4.0)
    s.new_image(1000.0, 1000.0)
    assert s.same_image(1304.0) == 1314.0  # 10 s, then 15 s, ...
    assert s.same_image(1314.0) == 1329.0
    s.new_image(1329.0, 1300.0)  # it came: we'd aimed too early
    # at least past the last miss (1314 - 1300 + 2), not just +MARGIN_UP_S
    assert s.margin == pytest.approx(16.0)
    assert s.unchanged == 0


def test_ladder_ends_in_slow_polling():
    s = schedule.CamSchedule(300.0)
    for _ in schedule.RETRY_LADDER_S:
        s.same_image(0.0)
    assert 120 <= s.same_image(0.0) <= 180  # period / 2, jittered


def test_period_learned_from_capture_gaps_and_clamped():
    s = schedule.CamSchedule(300.0)
    t = 0.0
    for _ in range(5):
        t += 60.0
        s.new_image(t + 1, t)
    assert s.period == 150.0  # learned 60 s, but at most 2x faster than nominal
    s = schedule.CamSchedule(300.0)
    for k in range(5):
        s.new_image(k * 600.0 + 1, k * 600.0)  # every other refresh: counts as 300 s
    assert s.period == 300.0


def test_failures_back_off_exponentially_and_honour_retry_after():
    s = schedule.CamSchedule(300.0)
    d = [s.failed(0.0) for _ in range(4)]
    assert d[0] < d[1] < d[2] < d[3] and 24 <= d[0] <= 36
    for _ in range(20):
        s.failed(0.0)
    assert s.failed(0.0) <= schedule.BACKOFF_MAX_S * 1.2
    assert s.failed(0.0, retry_after=5000) >= 5000 * 0.8
    s.new_image(0.0, None)
    assert s.failures == 0


def test_placeholder_polls_slowly():
    s = schedule.CamSchedule(300.0)
    assert s.placeholder(0.0) >= schedule.DOWN_POLL_S * 0.9


# ------------------------------------------------------------------ judging Caltrans
CAM = Cam("caltrans-x", "caltrans", "X", 37.0, -122.0, "http://cwwp2/x.jpg", 300.0, "track", True)


def resp(body, lm="Wed, 11 Nov 2026 20:08:09 GMT"):
    return sources.Response(200, {"Last-Modified": lm, "ETag": '"e1"'}, body)


def test_caltrans_capture_time_and_lag():
    b = make_jpeg(exif_time="2026:11:11 12:08:02", scan=b"\x00" * 30000)
    now = 1794427689.0 + 20  # 20 s after Last-Modified (20:08:09Z)
    j = sources.Caltrans().judge(CAM, resp(b), jpeg.parse(b), now)
    assert j["quality"] == "ok" and j["capture_utc"] == 1794427689.0
    assert j["age_s"] == 20.0 and j["exif_lag_s"] == 7.0  # EXIF is Pacific local time


def test_caltrans_placeholder_by_tables_and_size():
    qt = b"\x07" * 64
    src = sources.Caltrans()
    src.PLACEHOLDER_QT = {jpeg.parse(make_jpeg(qt=qt)).qt}
    small = make_jpeg(qt=qt, scan=b"\x00" * 13400)
    big = make_jpeg(qt=qt, scan=b"\x00" * 25000)  # a real frame from the same encoder
    assert src.judge(CAM, resp(small), jpeg.parse(small), 1794427700.0)["quality"] == "placeholder"
    assert src.judge(CAM, resp(big), jpeg.parse(big), 1794427700.0)["quality"] == "ok"


def test_caltrans_stale_when_exif_far_behind():
    b = make_jpeg(exif_time="2026:11:11 11:00:00", scan=b"\x00" * 30000)
    j = sources.Caltrans().judge(CAM, resp(b), jpeg.parse(b), 1794427700.0)
    assert j["quality"] == "stale"


# ------------------------------------------------------------------ fetch_cam end to end
def test_fetch_cam_stores_new_images_once(tmp_path, monkeypatch):
    from email.utils import formatdate
    b = make_jpeg(scan=b"\x00" * 30000)
    lm = formatdate(usegmt=True)
    replies = [resp(b, lm), resp(b, lm), sources.Response(304, {}, b""), sources.Response(503, {}, b"busy")]
    sent = []

    def fake_get(url, headers, timeout=30):
        sent.append(dict(headers))
        return replies.pop(0)

    monkeypatch.setattr(fetch, "http_get", fake_get)
    monkeypatch.setitem(fetch.SOURCES, "caltrans", sources.Caltrans())
    st = fetch.CamState(CAM)
    for _ in range(4):
        fetch.fetch_cam(st, tmp_path, 0.0, verbose=False)
    files = sorted(p.name for p in (tmp_path / "cams").rglob("*") if p.is_file() and "fetch_log" not in p.parts)
    assert sorted(f.rsplit(".", 1)[1] for f in files) == ["http503", "jpg"]
    assert sent[1].get("If-None-Match") == '"e1"' and "If-Modified-Since" not in sent[1]
    from fetchers.archive import read_log
    (day,) = [d for d in (tmp_path / "cams").iterdir()]
    q = [r["quality"] for r in read_log(day)]
    assert q == ["ok", "unchanged", "unchanged", "error"]
    assert st.sched.failures == 1


# ------------------------------------------------------------------ pass prediction
from fetchers.cams import passes  # noqa: E402
from google.transit import gtfs_realtime_pb2  # noqa: E402


def tu_feed(trips):
    """trips: {trip_id: [(stop_id, arrival, departure)]}"""
    m = gtfs_realtime_pb2.FeedMessage()
    m.header.gtfs_realtime_version = "2.0"
    for tid, stops in trips.items():
        e = m.entity.add(id=tid)
        e.trip_update.trip.trip_id = tid
        for sid, a, d in stops:
            u = e.trip_update.stop_time_update.add(stop_id=sid)
            u.arrival.time, u.departure.time = a, d
    return m


def test_pass_time_interpolates_between_stations_either_direction():
    sa, mv = passes.STATION_KM["7020"], passes.STATION_KM["7021"]  # San Antonio, Mountain View
    km = sa + 0.75 * (mv - sa)
    feed = tu_feed({
        "sb": [("70202", 1000, 1060), ("70212", 1260, 1320)],   # SB: dep SA 1060, arr MV 1260
        "nb": [("70211", 2000, 2060), ("70201", 2260, 2320)],   # NB: dep MV 2060, arr SA 2260
    })
    got = dict(passes.pass_times(feed, km, 0))
    assert got["sb"] == pytest.approx(1060 + 0.75 * 200)
    assert got["nb"] == pytest.approx(2060 + 0.25 * 200)


def test_pass_time_extrapolates_before_first_remaining_stop_and_skips_passed():
    mv = passes.STATION_KM["7021"]
    feed = tu_feed({
        # SB, next stop Sunnyvale at 5900: at 5.3 km back, the camera was passed at ~5690
        "gone": [("70222", 5900, 5960), ("70232", 6300, 6360)],
        # SB, next stop MV, camera 1 km before it -> 1000 m / 25 m/s earlier
        "coming": [("70212", 6000, 6060), ("70222", 6300, 6360)],
    })
    got = dict(passes.pass_times(feed, mv - 1.0, now=5800))
    assert "gone" not in got
    assert got["coming"] == pytest.approx(6000 - 1000 / passes.LINE_SPEED_MPS)


def test_predictor_reads_newest_fresh_file_and_ignores_stale(tmp_path, monkeypatch):
    from fetchers.gtfs.fetch import service_date
    from datetime import datetime, timezone
    d = tmp_path / "rt" / service_date(datetime.now(timezone.utc)) / "tripupdates"
    d.mkdir(parents=True)
    now = time.time()
    feed = tu_feed({"t1": [("70212", int(now) + 600, int(now) + 660), ("70222", int(now) + 900, int(now) + 960)]})
    name = datetime.fromtimestamp(now - 30, timezone.utc).strftime("%Y%m%dT%H%M%S.000Z.pb")
    (d / name).write_bytes(feed.SerializeToString())
    p = passes.PassPredictor(tmp_path)
    w = p.windows(passes.STATION_KM["7021"], 120, 240)
    assert [x[2] for x in w] == ["t1"] and w[0][0] == pytest.approx(now + 660 - 120, abs=2)  # a station: its departure
    (d / name).rename(d / "20260101T000000.000Z.pb")  # an old fetch, freshly synced
    p.read_at = 0
    p.feed_file = None
    assert p.windows(57.0, 120, 240) is None


def test_live_cadence():
    w = [(100.0, 460.0, "t1"), (2000.0, 2360.0, "t2")]
    assert schedule.live_next(150.0, w, 12) == (153.0, "pass window t1")
    assert schedule.live_next(500.0, w, 12)[0] == 560.0          # slow between passes
    assert schedule.live_next(1990.0, w, 12)[0] == 2000.0        # but on time for the next
    assert schedule.live_next(3000.0, w, 12)[0] == 3060.0        # none ahead
    assert schedule.live_next(0.0, None, 12)[0] == 3.0           # no predictions: fast by day
    assert schedule.live_next(0.0, None, 3)[0] == schedule.LIVE_NIGHT_S  # ... and slow at night


# ------------------------------------------------------------------ TrafficLand
SCC = Cam("scc-1", "trafficland", "X", 37.0, -122.0, "401837", 3.0, "track", True, km=57.7)


def test_trafficland_judges_placeholder_and_refreshes_token(monkeypatch):
    src = sources.TrafficLand()
    page = b'..."id=401837\\u0026key=' + b"a" * 64 + b'" ... id=401836&amp;key=' + b"b" * 64
    calls = []

    def fake_get(url, headers, timeout=30):
        calls.append(url)
        return sources.Response(200, {}, page)

    monkeypatch.setattr(sources, "http_get", fake_get)
    url, h = src.request(SCC)
    assert "pubtoken=" + "a" * 64 in url and h["Referer"].startswith("https://www.weatherbug.com")
    assert src.tokens["401836"] == "b" * 64  # neighbours' tokens come along
    down = sources.Response(404, {"Image-Status": "404"}, b"\x89PNG...")
    assert src.judge(SCC, down, None, 0)["quality"] == "placeholder"
    src.failed(SCC, sources.Response(401, {"Image-Status": "401"}, b"png"))
    assert "401837" not in src.tokens
    with pytest.raises(RuntimeError):  # page re-read is rate-limited
        src.request(SCC)
    assert len(calls) == 1
    # a later page read replaces the neighbours' (expired) tokens too
    page = b"id=401837&key=" + b"c" * 64 + b" id=401836&key=" + b"d" * 64
    src.token_read["401837"] = 0
    src.request(SCC)
    assert src.tokens["401836"] == "d" * 64


def test_registry_is_well_formed():
    from fetchers.cams.cams import CAMS
    ids = [c.id for c in CAMS]
    assert len(ids) == len(set(ids))
    for c in CAMS:
        assert c.source in sources.SOURCES or c.source == "alertcalifornia"
        assert isinstance(c.note, str) and isinstance(c.period_s, float)
        assert c.recheck_s is None or isinstance(c.recheck_s, float)
        assert c.km is None or isinstance(c.km, float)
        assert c.sees_track in ("yes", "track", "maybe", "no", "down")
        if SOURCES_LIVE.get(c.source):
            assert c.km is not None, f"{c.id}: a live camera needs km for pass windows"


SOURCES_LIVE = {k: v.live for k, v in sources.SOURCES.items()}
