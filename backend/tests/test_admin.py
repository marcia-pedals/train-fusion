"""`just deletions` (backend/admin.py) against in-memory Firestore and bucket."""
import json
from datetime import datetime, timedelta, timezone

import admin
from api.ports import MemoryDocs

NOW = datetime(2030, 1, 10, 12, 0, tzinfo=timezone.utc)
UPLOAD = "20300101T080000Z-3f9a0c1d"
OTHER_UPLOAD = "20300102T080000Z-0badc0de"


class MemoryBucket:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, int]] = {}
        self.generations = 0

    def put(self, name: str, data: bytes) -> None:
        self.generations += 1
        self.objects[name] = (data, self.generations)

    def sizes(self, prefix: str) -> dict[str, int]:
        return {n: len(d) for n, (d, _) in self.objects.items() if n.startswith(prefix)}

    def read(self, name: str):
        return self.objects.get(name)

    def write(self, name: str, data: bytes, generation: int) -> None:
        assert self.objects[name][1] == generation
        self.put(name, data)

    def delete(self, name: str) -> None:
        self.objects.pop(name, None)


def setup():
    d, bucket = MemoryDocs(), MemoryBucket()
    d.set("users/u1", {"email": "someone@example.com", "blocked": False})
    marked = {"deletion_requested_at": NOW - timedelta(days=2)}
    d.set("measurements/m1", {"ride_id": "20300101T080000Z", "started_unix_s": 0, "ended_unix_s": 2460,
                              "stops": [{}, {}, {}], **marked})
    d.set("measurements/m2", {"ride_id": "20300102T080000Z", "started_unix_s": 0, "ended_unix_s": None,
                              "stops": [], "deletion_requested_at": None})  # shared again since
    d.set(f"uploads/{UPLOAD}", {"total_bytes": 5, **marked})
    d.set("deletion_requests/r1", {"requested_at": NOW - timedelta(days=2), "reason": "account_deleted",
                                   "uid": "u1", "device_hash": None, "upload_ids": [UPLOAD],
                                   "measurement_ids": ["m1", "m2"], "done_at": None})
    # too recent: its upload URLs may still be in use
    d.set("deletion_requests/r2", {"requested_at": NOW - timedelta(hours=1), "reason": "requested",
                                   "uid": None, "device_hash": "ab" * 32, "upload_ids": [OTHER_UPLOAD],
                                   "measurement_ids": [], "done_at": None})
    bucket.put(f"raw/{UPLOAD}/meta.json", json.dumps({"started_unix_s": 0, "ended_unix_s": 1800}).encode())
    bucket.put(f"raw/{UPLOAD}/audio_000.m4a", b"x" * 2_000_000)
    bucket.put(f"labels/{UPLOAD}/stops.csv", b"a")
    rides = f"# rides\nA {UPLOAD}\nB other\n".encode()
    bucket.put("labels/RIDES.md", rides)
    return d, bucket


def run(d, bucket, answer):
    lines = []
    ok = admin.deletions(d, bucket, NOW, ask=lambda _: answer, out=lines.append)
    return ok, "\n".join(lines)


def test_lists_grouped_and_deletes_nothing_without_confirmation():
    d, bucket = setup()
    before = (dict(d.data), dict(bucket.objects))
    ok, text = run(d, bucket, "yes")
    assert ok and "Nothing deleted." in text
    assert (dict(d.data), dict(bucket.objects)) == before
    assert "account u1 (someone@example.com)" in text
    assert "20300101T080000Z  41 min, 3 stops" in text
    assert "1 shared again since: kept" in text
    assert f"{UPLOAD}  30 min, 2 MB; labels (1 file); in RIDES.md" in text
    assert "Not yet: request r2" in text


def test_delete_does_everything():
    d, bucket = setup()
    ok, text = run(d, bucket, "delete")
    assert ok, text
    assert d.get("measurements/m1") is None
    assert d.get("measurements/m2") is not None  # shared again: kept
    assert not [n for n in bucket.objects if UPLOAD in n]
    assert d.get(f"uploads/{UPLOAD}") is None
    assert bucket.objects["labels/RIDES.md"][0] == b"# rides\nB other\n"
    assert d.get("deletion_requests/r1")["done_at"] == NOW
    assert d.get("deletion_requests/r2")["done_at"] is None  # waits for its upload URLs to expire

    ok, text = run(d, bucket, "delete")
    assert ok and "Nothing to delete." in text
