"""`just hide` / `just unhide`: keep recordings in the bucket but stop downloading them
(labels/HIDDEN, see common/sync.py).

    python -m common.hide <upload id>... [--reason "test recording"]
    python -m common.hide --unhide <upload id>...

Hiding checks that each recording is in the bucket (raw/<id>/meta.json), adds it to
labels/HIDDEN and syncs. A local copy is left alone: delete it by hand if you want it gone.
Unhiding takes it off the list and syncs, which downloads it again if it isn't here.
"""
from __future__ import annotations

import argparse
import sys

from common import paths, sync


def edit(ids: list[str], unhide: bool, reason: str | None, path=sync.HIDDEN) -> list[str]:
    """Add ids to (or take them off) the HIDDEN file. Returns the ids that changed."""
    lines = path.read_text().splitlines() if path.exists() else []
    listed = sync.hidden(path)
    if unhide:
        gone = [i for i in ids if i in listed]
        lines = [line for line in lines if line.split("#", 1)[0].strip() not in ids]
    else:
        gone = [i for i in ids if i not in listed]
        lines += [f"{i}  # {reason}" if reason else i for i in gone]
    if gone:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(line + "\n" for line in lines))
    return gone


def main() -> None:
    ap = argparse.ArgumentParser(prog="just hide", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="+", metavar="upload-id")
    ap.add_argument("--reason", help="written after the id, as a comment")
    ap.add_argument("--unhide", action="store_true", help="take the ids off the list instead")
    ap.add_argument("--bucket", default=paths.BUCKET)
    a = ap.parse_args()

    bucket = sync.open_bucket(a.bucket)
    if not a.unhide:
        missing = [i for i in a.ids if bucket.get_blob(f"{paths.RAW_PREFIX}{i}/meta.json") is None]
        if missing:
            sys.exit("not in the bucket (raw/<id>/meta.json), nothing done: " + " ".join(missing))

    # bring HIDDEN up to date first, so our edit doesn't conflict with one made elsewhere
    got = sync.sync_labels(bucket)
    if got["conflict"]:
        sys.exit("label conflicts; sort them out with `just sync-recordings` first")
    changed = edit(a.ids, a.unhide, a.reason)
    for i in a.ids:
        print(f"{i}: {('unhidden' if a.unhide else 'hidden') if i in changed else 'already ' + ('not hidden' if a.unhide else 'hidden')}")
    print()
    sync.run(bucket)


if __name__ == "__main__":
    main()
