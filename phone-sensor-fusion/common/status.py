"""What recordings are synced locally: the unlabelled ones, then the labelled ones, newest
first, with id, start time (Pacific), size on disk, duration and phone. Labelled rides get
their letter and a short description from labels/RIDES.md (gitignored).
`just sync-recordings` prints the same for what it just downloaded.

    python -m common.status
"""

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from . import paths, sync

# Apple's model identifiers -> marketing names (Android reports the name itself)
DEVICES = {
    "iPhone15,4": "iPhone 15",
    "iPhone15,5": "iPhone 15 Plus",
    "iPhone16,1": "iPhone 15 Pro",
    "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone17,1": "iPhone 16 Pro",
    "iPhone17,2": "iPhone 16 Pro Max",
    "iPhone17,3": "iPhone 16",
    "iPhone17,4": "iPhone 16 Plus",
    "iPhone17,5": "iPhone 16e",
}


def rides() -> dict[str, tuple[str, str]]:
    """recording id -> (ride letter, notes) from the tables in RIDES.md."""
    f = paths.LABELS / "RIDES.md"
    out = {}
    if f.exists():
        for line in f.read_text().splitlines():
            m = re.match(r"\|\s*([A-Z]+)\s*\|\s*(\S+)\s*\|\s*(.*?)\s*\|\s*$", line)
            if m:
                out[m[2]] = (m[1], m[3])
    return out


def size(path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def fmt_size(n: int) -> str:
    return f"{n / 1e6:6.0f} MB" if n < 1e9 else f"{n / 1e9:6.2f} GB"


def fmt_dur(s: float) -> str:
    s = int(round(s))
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60:2d}m{s % 60:02d}s"


PACIFIC = ZoneInfo("America/Los_Angeles")


def fmt_start(unix_s: float) -> str:
    t = datetime.fromtimestamp(unix_s, PACIFIC)
    year = f" {t.year}" if t.year != datetime.now(PACIFIC).year else ""
    return f"{t:%b} {t.day}{year}, {t:%H:%M}"


def row(rid: str, hidden: set[str] = frozenset()) -> str:
    d = paths.RECORDINGS / rid
    m = json.loads((d / "meta.json").read_text())
    dur = m["ended_uptime_s"] - m["started_uptime_s"] if "ended_uptime_s" in m else None
    dev = m.get("device_model", "?")
    dev = DEVICES.get(dev, dev)
    flag = "" if m.get("complete", True) else "  (incomplete)"
    if rid in hidden:
        flag += "  (hidden)"
    return (f"  {rid:28s} {fmt_start(m['started_unix_s']):18s} {fmt_size(size(d))}  {fmt_dur(dur) if dur else '     ?':>6s}  "
            f"{dev:16s}{flag}")


def show(only=None) -> None:
    """Print the table, for every local recording or only those in `only`."""
    ids = sorted(paths.recording_ids(), reverse=True)
    if only is not None:
        ids = [r for r in ids if r in only]
    labelled = set(paths.labelled_ids())
    notes = rides()
    hidden = sync.hidden()
    todo = [r for r in ids if r not in labelled]
    done = [r for r in ids if r in labelled]

    if todo or only is None:
        print(f"unlabelled ({len(todo)})")
        for rid in todo:
            print(row(rid, hidden).rstrip())
    if done or only is None:
        print(f"\nlabelled ({len(done)})" if todo or only is None else f"labelled ({len(done)})")
        for rid in done:
            name, note = notes.get(rid, ("?", "not in RIDES.md"))
            short = note.split(";")[0]
            print(f"{row(rid, hidden)}  {name}: {short}")


if __name__ == "__main__":
    show()
