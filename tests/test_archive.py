"""The archive's merge-by-union design: log segments, create-only uploads, sync."""
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from google.api_core import exceptions as gexc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fetch  # noqa: E402
import fetch_cloud  # noqa: E402
from train_fusion import sync  # noqa: E402


# ------------------------------------------------------------------ a fake GCS
class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    @property
    def time_created(self):
        return self.bucket.created[self.name]

    def upload_from_filename(self, path, if_generation_match=None, timeout=None):
        assert if_generation_match == 0, "uploads must be create-only"
        if self.name in self.bucket.store:
            raise gexc.PreconditionFailed("exists")
        self.bucket.store[self.name] = Path(path).read_bytes()
        self.bucket.created[self.name] = datetime.now(timezone.utc)

    def exists(self):
        return self.name in self.bucket.store

    def download_to_filename(self, path):
        Path(path).write_bytes(self.bucket.store[self.name])


class FakeListing:
    def __init__(self, blobs, prefixes):
        self._blobs, self.prefixes = blobs, set()
        self._pending = prefixes

    def __iter__(self):
        return iter(self._blobs)

    @property
    def pages(self):
        self.prefixes = self._pending
        return iter([self._blobs])


class FakeBucket:
    def __init__(self):
        self.store, self.created = {}, {}

    def blob(self, name):
        return FakeBlob(self, name)

    def list_blobs(self, prefix="", delimiter=None):
        names = sorted(n for n in self.store if n.startswith(prefix))
        if delimiter is None:
            return FakeListing([FakeBlob(self, n) for n in names], set())
        direct = [FakeBlob(self, n) for n in names if delimiter not in n[len(prefix):]]
        prefixes = {prefix + n[len(prefix):].split(delimiter)[0] + delimiter
                    for n in names if delimiter in n[len(prefix):]}
        return FakeListing(direct, prefixes)


class FakeClient:
    def __init__(self):
        self.b = FakeBucket()

    def bucket(self, name):
        return self.b


def write(p: Path, data: str = "x", age_s: float = 0.0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data)
    if age_s:
        t = time.time() - age_s
        os.utime(p, (t, t))
    return p


def segment(day_dir: Path, minute: datetime, instance: str, lines: list[str]) -> Path:
    return write(day_dir / "fetch_log" / f"{minute:%Y%m%dT%H%MZ}_{instance}.jsonl",
                 "".join(l + "\n" for l in lines))


# ------------------------------------------------------------------ the log
def test_read_log_merges_legacy_and_segments_and_skips_torn_lines(tmp_path):
    d = tmp_path / "rt" / "2026-11-11"
    write(d / "fetch_log.jsonl", '{"a": 1}\n{"a": 2')  # torn last line
    t = datetime(2026, 11, 11, 20, 0, tzinfo=timezone.utc)
    segment(d, t, "vm1-aaaaaa", ['{"b": 1}'])
    segment(d, t, "vm2-bbbbbb", ['{"c": 1}'])
    assert fetch.read_log(d) == [{"a": 1}, {"b": 1}, {"c": 1}]


def test_log_files_since_keeps_legacy_and_recent_segments(tmp_path):
    d = tmp_path / "day"
    write(d / "fetch_log.jsonl")
    t = datetime(2026, 11, 11, 20, 0, tzinfo=timezone.utc)
    old = segment(d, t - timedelta(hours=2), "i", [])
    before = segment(d, t - timedelta(minutes=1), "i", [])  # ends exactly at `since`
    covering = segment(d, t, "i", [])  # its minute contains `since`
    new = segment(d, t + timedelta(minutes=5), "i", [])
    files = fetch.log_files(d, since=t + timedelta(seconds=30))
    assert before not in files
    assert d / "fetch_log.jsonl" in files and covering in files and new in files
    assert old not in files


def test_append_log_writes_this_instances_segment(tmp_path):
    fetch.append_log(tmp_path, {"x": 1})
    (p,) = (tmp_path / "fetch_log").iterdir()
    assert p.name.endswith(f"_{fetch.INSTANCE}.jsonl") and fetch.log_minute(p.name) is not None
    assert fetch.read_log(tmp_path) == [{"x": 1}]


# ------------------------------------------------------------------ upload
def test_upload_skips_open_files_and_deletes_only_old_uploaded_ones(tmp_path):
    client = FakeClient()
    up = fetch_cloud.Uploader(tmp_path, "b", instance="me-111111", client=client)
    day = tmp_path / "rt" / "2026-11-11"
    now = datetime.now(timezone.utc)
    pb_old = write(day / "vehiclepositions" / "a.pb", "old", age_s=3 * 3600)
    pb_new = write(day / "vehiclepositions" / "b.pb", "new")
    tmp = write(day / "vehiclepositions" / "c.pb.tmp")
    mine_open = segment(day, now, "me-111111", ["{}"])
    mine_done = segment(day, now - timedelta(minutes=5), "me-111111", ["{}"])
    other = segment(day, now, "dead-222222", ["{}"])  # an earlier process's: finished
    write(tmp_path / "derived" / "x.parquet")  # not archive data

    up.upload_pass()
    rel = lambda p: p.relative_to(tmp_path).as_posix()  # noqa: E731
    assert set(client.b.store) == {rel(pb_old), rel(pb_new), rel(mine_done), rel(other)}
    assert not pb_old.exists() and pb_new.exists() and tmp.exists() and mine_open.exists()

    up.upload_pass(final=True)  # shutdown: the open segment goes too
    assert rel(mine_open) in client.b.store


def test_upload_never_overwrites(tmp_path):
    client = FakeClient()
    p = write(tmp_path / "gtfs" / "2026-11-11" / "z.zip", "first")
    fetch_cloud.Uploader(tmp_path, "b", client=client).upload_pass()
    p.write_text("second")
    fetch_cloud.Uploader(tmp_path, "b", client=client).upload_pass()  # e.g. after a restart
    assert client.b.store["gtfs/2026-11-11/z.zip"] == b"first"


def test_two_fetchers_merge_by_union_and_sync_back(tmp_path, monkeypatch):
    """Two fetchers (or a VM and its replacement) archiving the same day: the bucket holds
    both, and a synced copy reads every log line of both."""
    client = FakeClient()
    day = "2026-11-11"
    t = datetime(2026, 11, 11, 20, 0, tzinfo=timezone.utc)
    for inst, n in (("vm1-aaaaaa", 1), ("vm2-bbbbbb", 2)):
        root = tmp_path / inst
        write(root / "rt" / day / "vehiclepositions" / f"2026111{n}T200000.000Z.pb", inst)
        segment(root / "rt" / day, t, inst, [f'{{"from": "{inst}"}}'])
        fetch_cloud.Uploader(root, "b", instance=inst, client=client).upload_pass(final=True)

    local = tmp_path / "laptop"
    monkeypatch.setattr(sync, "service_date", lambda _: day)
    got = sync.sync("b", local, client=client)
    assert got == {day: 4}
    assert {r["from"] for r in fetch.read_log(local / "rt" / day)} == {"vm1-aaaaaa", "vm2-bbbbbb"}
    assert len(list((local / "rt" / day / "vehiclepositions").glob("*.pb"))) == 2


# ------------------------------------------------------------------ sync
def test_sync_is_incremental_and_stops_listing_final_days(tmp_path, monkeypatch):
    client = FakeClient()
    b = client.b
    for name in ("rt/2026-11-01/vehiclepositions/a.pb", "rt/2026-11-11/vehiclepositions/a.pb",
                 "gtfs/2026-11-01/z.zip"):
        b.store[name], b.created[name] = b"x", datetime.now(timezone.utc)
    monkeypatch.setattr(sync, "service_date", lambda _: "2026-11-11")

    assert sync.sync("b", tmp_path, client=client) == {"2026-11-01": 1, "2026-11-11": 1, "gtfs": 1}
    assert (tmp_path / "rt" / "2026-11-01" / ".final").exists()
    assert not (tmp_path / "rt" / "2026-11-11" / ".final").exists()  # still filling

    b.store["rt/2026-11-11/vehiclepositions/b.pb"] = b"y"
    b.created["rt/2026-11-11/vehiclepositions/b.pb"] = datetime.now(timezone.utc)
    b.store["rt/2026-11-01/vehiclepositions/late.pb"] = b"z"  # not looked for: day is final
    b.created["rt/2026-11-01/vehiclepositions/late.pb"] = datetime.now(timezone.utc)
    assert sync.sync("b", tmp_path, client=client) == {"2026-11-11": 1}


def test_sync_warns_when_archive_is_stale(tmp_path, monkeypatch, capsys):
    client = FakeClient()
    name = "rt/2026-11-11/vehiclepositions/a.pb"
    client.b.store[name] = b"x"
    client.b.created[name] = datetime.now(timezone.utc) - timedelta(hours=1)
    monkeypatch.setattr(sync, "service_date", lambda _: "2026-11-11")
    sync.sync("b", tmp_path, client=client)
    assert "is the fetcher running?" in capsys.readouterr().out


# ------------------------------------------------------------------ supervision
def test_crashing_thread_sets_crashed(monkeypatch):
    monkeypatch.setattr(fetch, "crashed", threading.Event())

    def boom():
        raise RuntimeError("disk full")

    fetch.run_thread(boom).join()
    assert fetch.crashed.is_set()


@pytest.mark.parametrize("final", [False, True])
def test_legacy_log_uploaded_only_once_quiet(tmp_path, final):
    up = fetch_cloud.Uploader(tmp_path, "b", client=FakeClient())
    busy = write(tmp_path / "rt" / "d" / "fetch_log.jsonl")
    quiet = write(tmp_path / "rt" / "e" / "fetch_log.jsonl", age_s=3600)
    assert not up.finished(busy, final) and up.finished(quiet, final)
