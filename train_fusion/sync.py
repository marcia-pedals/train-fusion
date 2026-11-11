"""`just sync`: download what's new in the GCS archive into data/rt and data/gtfs.

Archive objects are never changed once written (see fetchers/archive.py), so syncing is just
downloading the names we don't have yet. A service date that ended long enough ago that
nothing more can arrive for it (FINAL_AFTER_DAYS) gets a `.final` marker once complete
and is never listed again, so a sync only lists the last couple of days plus gtfs/.

Auth: `gcloud auth application-default login` once.
"""
from __future__ import annotations

import argparse
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from train_fusion.common import paths
from train_fusion.common.timeutil import service_date

FINAL_AFTER_DAYS = 2  # a date is over at 03:00 the next day; the fetcher uploads within minutes
STALE_WARN_S = 15 * 60
MARKER = ".final"


def remote_dates(bucket) -> list[str]:
    it = bucket.list_blobs(prefix="rt/", delimiter="/")
    for _ in it.pages:  # prefixes fill in as pages are read
        pass
    return sorted(p.split("/")[1] for p in it.prefixes)


def missing(bucket, prefix: str, dest: Path) -> tuple[list[str], float | None]:
    """Names of objects under `prefix` that are missing locally under dest (the data dir),
    and the newest object's creation time."""
    todo, newest = [], None
    for b in bucket.list_blobs(prefix=prefix):
        t = b.time_created.timestamp() if b.time_created else None
        newest = t if newest is None or (t is not None and t > newest) else newest
        if not (dest / b.name).exists():
            todo.append(b.name)
    return todo, newest


def download(client_factory, bucket_name: str, names: list[str], dest: Path, workers: int = 16) -> None:
    """Download objects in parallel (one client per thread), each via a .part file so an
    interrupted sync never leaves a truncated file under its real name."""
    from tqdm import tqdm

    local = threading.local()

    def get(name: str) -> None:
        if not hasattr(local, "bucket"):
            local.bucket = client_factory().bucket(bucket_name)
        p = dest / name
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".part")
        local.bucket.blob(name).download_to_filename(str(tmp))
        tmp.rename(p)

    with ThreadPoolExecutor(workers) as ex:
        for _ in tqdm(ex.map(get, names), total=len(names), unit="file", desc="downloading",
                      disable=not names):
            pass


def is_final(day: str, today: str) -> bool:
    return date.fromisoformat(day) <= date.fromisoformat(today) - timedelta(days=FINAL_AFTER_DAYS)


def sync(bucket_name: str = paths.ARCHIVE_BUCKET, data: Path = paths.DATA, client=None) -> dict:
    """List what's new (every unfinished date, plus gtfs/), download it all, then mark the
    dates that can't get new objects anymore as final. Returns {date or "gtfs": count}."""
    if client is None:
        from google.cloud import storage
        client_factory = storage.Client
    else:
        client_factory = lambda: client  # noqa: E731  (tests)
    bucket = client_factory().bucket(bucket_name)
    today = service_date(time.time())
    todo, got, newest, finals = [], {}, None, []
    for day in remote_dates(bucket):
        d = data / "rt" / day
        if (d / MARKER).exists():
            continue
        names, t = missing(bucket, f"rt/{day}/", data)
        todo += names
        if names:
            got[day] = len(names)
        if t is not None and (newest is None or t > newest):
            newest = t
        if is_final(day, today):
            finals.append(d)
    names, _ = missing(bucket, "gtfs/", data)
    todo += names
    if names:
        got["gtfs"] = len(names)

    download(client_factory, bucket_name, todo, data)
    for d in finals:  # only once everything for them is down
        d.mkdir(parents=True, exist_ok=True)
        (d / MARKER).touch()

    if newest is None or time.time() - newest > STALE_WARN_S:
        age = "never" if newest is None else f"{(time.time() - newest) / 60:.0f} min ago"
        print(f"warning: newest RT object in gs://{bucket_name} was written {age}; is the fetcher running?")
    return got


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="just sync", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bucket", default=paths.ARCHIVE_BUCKET)
    a = ap.parse_args()
    t0 = time.time()
    got = sync(a.bucket)
    print(f"sync from gs://{a.bucket}: {got or 'nothing new'} in {time.time() - t0:.1f}s")
