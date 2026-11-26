"""common/sync.py against an in-memory bucket: raw/ one way, labels/ both ways."""
import base64
import hashlib
import sys
from pathlib import Path

import pytest
from google.api_core.exceptions import Forbidden, PreconditionFailed

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import sync  # noqa: E402


class Blob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    @property
    def md5_hash(self):
        return base64.b64encode(hashlib.md5(self.bucket.store[self.name]).digest()).decode()

    @property
    def generation(self):
        return self.bucket.gen[self.name]

    def download_to_filename(self, path):
        Path(path).write_bytes(self.bucket.store[self.name])

    def upload_from_filename(self, path, if_generation_match=None):
        assert if_generation_match is not None, "label uploads must be conditional"
        if self.bucket.read_only:
            raise Forbidden("no storage.objects.create permission")
        if self.bucket.gen.get(self.name, 0) != if_generation_match:
            raise PreconditionFailed("generation mismatch")
        self.bucket.put(self.name, Path(path).read_bytes())


class Listing:
    def __init__(self, blobs, prefixes):
        self.blobs, self.prefixes = blobs, prefixes

    def __iter__(self):
        return iter(self.blobs)


class Bucket:
    def __init__(self):
        self.store, self.gen, self.n = {}, {}, 0
        self.read_only = False

    def put(self, name, data: bytes):
        self.n += 1
        self.store[name], self.gen[name] = data, self.n

    def blob(self, name):
        return Blob(self, name)

    def list_blobs(self, prefix="", delimiter=None):
        names = sorted(n for n in self.store if n.startswith(prefix))
        if delimiter is None:
            return Listing([Blob(self, n) for n in names], set())
        files = [Blob(self, n) for n in names if delimiter not in n[len(prefix):]]
        dirs = {prefix + n[len(prefix):].split(delimiter)[0] + delimiter
                for n in names if delimiter in n[len(prefix):]}
        return Listing(files, dirs)


@pytest.fixture
def env(tmp_path):
    return Bucket(), tmp_path / "labels", tmp_path / ".labels-sync.json"


def run(env, **kw):
    bucket, local, state = env
    return sync.sync_labels(bucket, local, state, **kw)


def write(local, rel, text):
    p = local / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_raw_downloads_complete_recordings_once(tmp_path):
    b = Bucket()
    b.put("raw/r1/accel.csv.gz", b"a")
    b.put("raw/r1/meta.json", b"{}")
    b.put("raw/r2/accel.csv.gz", b"b")  # no meta.json yet: still uploading
    dest = tmp_path / "raw"
    assert sync.sync_raw(b, dest) == {"r1": 2}
    assert (dest / "r1" / "meta.json").read_bytes() == b"{}"
    assert not (dest / "r2").exists()
    assert sync.sync_raw(b, dest) == {}
    b.put("raw/r2/meta.json", b"{}")
    assert sync.sync_raw(b, dest) == {"r2": 2}


def test_labels_new_files_go_both_ways(env):
    bucket, local, _ = env
    write(local, "r1/stops.csv", "here")
    bucket.put("labels/r2/stops.csv", b"there")
    done = run(env)
    assert done == {"uploaded": ["r1/stops.csv"], "downloaded": ["r2/stops.csv"], "conflict": [],
                    "forbidden": []}
    assert bucket.store["labels/r1/stops.csv"] == b"here"
    assert (local / "r2/stops.csv").read_text() == "there"
    assert run(env) == {"uploaded": [], "downloaded": [], "conflict": [], "forbidden": []}


def test_labels_edits_go_whichever_way_they_were_made(env):
    bucket, local, _ = env
    write(local, "r1/stops.csv", "v1")
    write(local, "r1/phone.csv", "v1")
    run(env)
    write(local, "r1/stops.csv", "v2 here")
    bucket.put("labels/r1/phone.csv", b"v2 there")
    done = run(env)
    assert done["uploaded"] == ["r1/stops.csv"] and done["downloaded"] == ["r1/phone.csv"]
    assert bucket.store["labels/r1/stops.csv"] == b"v2 here"
    assert (local / "r1/phone.csv").read_text() == "v2 there"


def test_labels_deletions_are_not_propagated(env):
    bucket, local, state = env
    write(local, "r1/stops.csv", "v1")
    write(local, "RIDES.md", "key")
    run(env)
    for p in local.rglob("*"):  # cleared the laptop's labels (state file kept)
        if p.is_file():
            p.unlink()
    assert sorted(run(env)["downloaded"]) == ["RIDES.md", "r1/stops.csv"]
    del bucket.store["labels/RIDES.md"], bucket.gen["labels/RIDES.md"]
    assert run(env)["uploaded"] == ["RIDES.md"]


def test_labels_conflicts_are_left_alone_until_resolved(env):
    bucket, local, _ = env
    write(local, "r1/stops.csv", "v1")
    run(env)
    write(local, "r1/stops.csv", "mine")
    bucket.put("labels/r1/stops.csv", b"theirs")
    assert run(env)["conflict"] == ["r1/stops.csv"]
    assert (local / "r1/stops.csv").read_text() == "mine"
    assert bucket.store["labels/r1/stops.csv"] == b"theirs"
    assert run(env, conflicts="local")["uploaded"] == ["r1/stops.csv"]
    assert bucket.store["labels/r1/stops.csv"] == b"mine"


def test_fresh_checkout_with_different_files_is_a_conflict(env):
    bucket, local, _ = env
    bucket.put("labels/r1/stops.csv", b"theirs")
    write(local, "r1/stops.csv", "mine")  # no state: can't tell which side changed
    assert run(env)["conflict"] == ["r1/stops.csv"]
    assert run(env, conflicts="remote")["downloaded"] == ["r1/stops.csv"]
    assert (local / "r1/stops.csv").read_text() == "theirs"


def test_labels_upload_never_overwrites_a_concurrent_change(env, monkeypatch):
    bucket, local, _ = env
    write(local, "r1/stops.csv", "v1")
    run(env)
    write(local, "r1/stops.csv", "mine")
    real = Blob.upload_from_filename

    def racing(self, path, if_generation_match=None):  # another checkout uploads first
        bucket.put(self.name, b"theirs")
        return real(self, path, if_generation_match)

    monkeypatch.setattr(Blob, "upload_from_filename", racing)
    assert run(env)["conflict"] == ["r1/stops.csv"]
    assert bucket.store["labels/r1/stops.csv"] == b"theirs"


def test_read_only_access_downloads_and_keeps_local_changes(env):
    bucket, local, _ = env
    bucket.put("labels/r1/stops.csv", b"v1")
    bucket.read_only = True
    assert run(env)["downloaded"] == ["r1/stops.csv"]
    write(local, "r1/stops.csv", "mine")
    write(local, "r2/stops.csv", "new")
    bucket.put("labels/r3/stops.csv", b"there")
    done = run(env)
    assert done["forbidden"] == ["r1/stops.csv", "r2/stops.csv"]
    assert done["downloaded"] == ["r3/stops.csv"] and done["uploaded"] == []
    assert bucket.store["labels/r1/stops.csv"] == b"v1" and "labels/r2/stops.csv" not in bucket.store
    assert (local / "r1/stops.csv").read_text() == "mine"
    assert run(env)["forbidden"] == ["r1/stops.csv", "r2/stops.csv"]  # still changed here
    bucket.read_only = False
    assert run(env)["uploaded"] == ["r1/stops.csv", "r2/stops.csv"]


def test_dry_run_changes_nothing(env):
    bucket, local, state = env
    write(local, "r1/stops.csv", "here")
    bucket.put("labels/r2/stops.csv", b"there")
    done = run(env, dry_run=True)
    assert done["uploaded"] == ["r1/stops.csv"] and done["downloaded"] == ["r2/stops.csv"]
    assert "labels/r1/stops.csv" not in bucket.store and not (local / "r2").exists()
    assert not state.exists()


def test_stray_objects_outside_raw_and_labels(env):
    bucket, _, _ = env
    bucket.put("raw/r1/meta.json", b"{}")
    bucket.put("labels/RIDES.md", b"")
    bucket.put("r9/meta.json", b"{}")
    assert sync.stray(bucket) == ["r9/"]


def test_hidden_recordings_are_not_downloaded(tmp_path):
    from common import hide
    b = Bucket()
    for r in ("r1", "r2"):
        b.put(f"raw/{r}/meta.json", b"{}")
    listed = tmp_path / "HIDDEN"
    assert sync.hidden(listed) == set()
    assert hide.edit(["r2"], unhide=False, reason="junk", path=listed) == ["r2"]
    assert hide.edit(["r2"], unhide=False, reason=None, path=listed) == []  # already hidden
    listed.write_text(listed.read_text() + "# a comment\n\n")
    assert sync.hidden(listed) == {"r2"}
    assert sync.sync_raw(b, tmp_path / "raw", skip=sync.hidden(listed)) == {"r1": 1}
    assert hide.edit(["r2"], unhide=True, reason=None, path=listed) == ["r2"]
    assert listed.read_text() == "# a comment\n\n"
    assert sync.sync_raw(b, tmp_path / "raw", skip=sync.hidden(listed)) == {"r2": 1}
