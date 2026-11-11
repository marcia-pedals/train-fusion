"""The fetchers' cloud side (`--bucket/--metrics`): upload to GCS, and report fetch counts
to Cloud Monitoring.

Upload: every finished file under data/rt and data/gtfs is uploaded create-only (if the
object already exists, it's left alone), to the same path relative to the data dir. Since
fetchers never change a file once finished (archive.py), this makes the bucket the plain union of what
every fetcher ever wrote, whatever restarts or overlaps happened. Uploaded files are
deleted locally once older than KEEP_LOCAL_S (the fetcher seeds its rate limiter from the
last hour of its log), so the disk only holds what's recent or not yet uploaded.

A log segment is finished once its minute is over (plus a grace period), or at the final
pass on shutdown. Everything else is written via tmp + rename, so is finished on sight.

    python -m fetchers.cloud migrate --data-dir data --bucket B   # one-off: upload an
                                                                  # existing archive, deleting nothing
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.api_core import exceptions as gexc
from google.cloud import storage

from fetchers.archive import INSTANCE, LOG_DIR, log_minute

UPLOAD_EVERY_S = 30.0
KEEP_LOCAL_S = 2 * 3600.0   # > the rate limiter's 1 h window
SEGMENT_GRACE_S = 60.0      # a segment is finished this long after its minute ends
LEGACY_QUIET_S = 600.0      # migrate: a legacy fetch_log.jsonl must be untouched this long
METRIC = "custom.googleapis.com/train_fusion/fetches"


class Uploader:
    def __init__(self, data_dir: Path, bucket: str, instance: str = INSTANCE,
                 delete_local: bool = True, client=None):
        self.data_dir = data_dir
        self.bucket = (client or storage.Client()).bucket(bucket)
        self.instance = instance
        self.delete_local = delete_local
        self.uploaded: set[Path] = set()
        self.lock = threading.Lock()
        self.static_days: set[str] = set()

    def finished(self, p: Path, final: bool) -> bool:
        if p.name.endswith(".tmp"):
            return False
        if p.parent.name == LOG_DIR:
            m = log_minute(p.name)
            if m is None:
                return False
            if final or not p.name.endswith(f"_{self.instance}.jsonl"):
                return True  # another (earlier) process's segment: nobody writes it anymore
            return time.time() > (m + timedelta(minutes=1)).timestamp() + SEGMENT_GRACE_S
        if p.name == "fetch_log.jsonl":  # legacy single log: only when nothing appends to it
            return time.time() - p.stat().st_mtime > LEGACY_QUIET_S
        return True

    def candidates(self, final: bool) -> list[Path]:
        out = []
        for top in ("rt", "gtfs"):
            root = self.data_dir / top
            if root.is_dir():
                out += [p for p in sorted(root.rglob("*"))
                        if p.is_file() and not p.name.startswith(".") and self.finished(p, final)]
        return out

    def upload(self, p: Path) -> bool:
        """Create the object unless it exists. True if it's now in the bucket."""
        name = p.relative_to(self.data_dir).as_posix()
        blob = self.bucket.blob(name)
        try:
            blob.upload_from_filename(str(p), if_generation_match=0, timeout=60)
        except gexc.PreconditionFailed:
            pass  # already there (an earlier pass, before a restart)
        except gexc.Forbidden:
            # Overwriting needs delete permission, which the fetcher doesn't have; GCS may
            # report an existing object that way rather than as a failed precondition.
            if not blob.exists():
                raise
        return True

    def upload_pass(self, final: bool = False) -> int:
        n_new, errors = 0, 0
        with self.lock:
            for p in self.candidates(final):
                if p not in self.uploaded:
                    try:
                        self.upload(p)
                        self.uploaded.add(p)
                        n_new += 1
                    except Exception as e:  # network, auth, ...: retried next pass
                        errors += 1
                        if errors <= 3:
                            print(f"upload {p}: {type(e).__name__}: {e}", flush=True)
                        continue
                if self.delete_local and time.time() - p.stat().st_mtime > KEEP_LOCAL_S:
                    p.unlink()
                    self.uploaded.discard(p)
            if self.delete_local:
                # emptied directories (old days); a young one may be about to get its first file
                for top in ("rt", "gtfs"):
                    for d in sorted((self.data_dir / top).glob("**/"), reverse=True):
                        if (d != self.data_dir / top and not any(d.iterdir())
                                and time.time() - d.stat().st_mtime > KEEP_LOCAL_S):
                            d.rmdir()
        if errors:
            print(f"upload pass: {n_new} uploaded, {errors} failed", flush=True)
        return n_new

    def run(self, stop: threading.Event) -> None:
        while not stop.wait(UPLOAD_EVERY_S):
            self.upload_pass()

    def has_static(self, day: str) -> bool:
        """Whether the bucket has a static GTFS zip fetched on this local date."""
        if day not in self.static_days:
            try:
                if any(b.name.endswith(".zip") for b in self.bucket.list_blobs(prefix=f"gtfs/{day}/")):
                    self.static_days.add(day)
            except Exception as e:
                print(f"static check: {type(e).__name__}: {e}", flush=True)
                return False  # at worst, one extra fetch of the static feed
        return day in self.static_days


class Metrics:
    """Successful fetches per feed as a cumulative counter, one time series per feed and
    process (two processes must not write the same series). Alerts sum over processes."""

    EVERY_S = 60.0

    def __init__(self, instance: str, counts, lock: threading.Lock, feeds: list[str]):
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        creds, self.project = google.auth.default(scopes=["https://www.googleapis.com/auth/monitoring.write"])
        self.session = AuthorizedSession(creds)
        self.instance, self.counts, self.lock, self.feeds = instance, counts, lock, feeds
        self.start = datetime.now(timezone.utc)

    def write(self) -> None:
        with self.lock:
            counts = {f: self.counts[f] for f in self.feeds}
        now = datetime.now(timezone.utc)
        if (now - self.start).total_seconds() < 1:
            now = self.start + timedelta(seconds=1)  # cumulative points need end > start
        series = [{
            "metric": {"type": METRIC, "labels": {"feed": f, "instance": self.instance}},
            "resource": {"type": "global", "labels": {"project_id": self.project}},
            "metricKind": "CUMULATIVE", "valueType": "INT64",
            "points": [{"interval": {"startTime": self.start.isoformat(), "endTime": now.isoformat()},
                        "value": {"int64Value": str(n)}}],
        } for f, n in counts.items()]
        r = self.session.post(f"https://monitoring.googleapis.com/v3/projects/{self.project}/timeSeries",
                              json={"timeSeries": series}, timeout=30)
        r.raise_for_status()

    def run(self, stop: threading.Event) -> None:
        while not stop.wait(self.EVERY_S):
            try:
                self.write()
            except Exception as e:  # a missed point only blurs the alert window
                print(f"metrics: {type(e).__name__}: {e}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["migrate"])
    ap.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "data")))
    ap.add_argument("--bucket", required=True)
    a = ap.parse_args()
    up = Uploader(a.data_dir, a.bucket, instance="migrate", delete_local=False)
    # A legacy fetch_log.jsonl can't be updated once uploaded, so the fetcher appending to
    # it must be stopped first. (The gtfs one only changes daily, so this can't catch it.)
    skipped = [p for p in (a.data_dir / "rt").glob("*/fetch_log.jsonl") if not up.finished(p, True)]
    if skipped:
        sys.exit(f"{[str(p) for p in skipped]} changed in the last {LEGACY_QUIET_S:.0f} s: "
                 "stop the old fetcher first")
    n = up.upload_pass(final=True)
    print(f"uploaded {n} new files")


if __name__ == "__main__":
    main()
