"""The archive format shared by every fetcher (and read by the pipeline).

Each fetcher writes under a data dir, and never changes a file once it's finished: raw
responses are written via tmp + rename, and the fetch log goes to a new file each minute,
named by the writing process:

    <dir>/fetch_log/<utc_minute>_<instance>.jsonl

So archives from several fetcher processes (restarts, a replaced VM, two running at once)
merge by plain union, which is what fetchers/cloud.py's create-only upload relies on.
Archives from before 2026-11-11 have a single <dir>/fetch_log.jsonl instead; read_log()
reads both.
"""
from __future__ import annotations

import json
import secrets
import socket
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Names this process's log files, so concurrent or successive fetchers never share one.
INSTANCE = f"{socket.gethostname().split('.')[0]}-{secrets.token_hex(3)}"
LOG_DIR = "fetch_log"

log_lock = threading.Lock()


def log_minute(name: str) -> datetime | None:
    """The UTC minute a log segment covers, from its name (None for other files)."""
    try:
        return datetime.strptime(name.split("_", 1)[0], "%Y%m%dT%H%MZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def append_log(log_dir: Path, rec: dict) -> None:
    """Append to this process's segment for the current minute, under log_dir/fetch_log/."""
    now = datetime.now(timezone.utc)
    path = log_dir / LOG_DIR / f"{now:%Y%m%dT%H%MZ}_{INSTANCE}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with log_lock, path.open("a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def log_files(log_dir: Path, since: datetime | None = None) -> list[Path]:
    """The fetch-log files of a directory: the legacy single file, then the segments
    (those covering `since` or later, if given)."""
    out = [log_dir / "fetch_log.jsonl"] if (log_dir / "fetch_log.jsonl").exists() else []
    seg = log_dir / LOG_DIR
    if seg.is_dir():
        for p in sorted(seg.glob("*.jsonl")):
            m = log_minute(p.name)
            if since is None or (m is not None and m + timedelta(minutes=1) > since):
                out.append(p)
    return out


def read_log(log_dir: Path, since: datetime | None = None) -> list[dict]:
    """Every record in a directory's fetch log, skipping unparseable (e.g. torn) lines."""
    out = []
    for p in log_files(log_dir, since):
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out
