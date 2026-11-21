"""`just sync-recordings`: bring data/recordings/ and gs://train-fusion-recordings in step.

The bucket and data/recordings/ have the same layout:

    raw/<id>/...       a recording, as the phone uploaded it
    labels/<id>/...    its labels (labels/README.md), made by hand on a laptop
    labels/RIDES.md    which ride is which

raw/ only ever flows bucket -> laptop: recordings appear in the bucket (the app uploads
them, create-only) and are never made or changed on a laptop. A recording is downloaded
once its meta.json is in the bucket (the app uploads it last), and files already here are
skipped.

labels/ flows both ways, file by file. data/recordings/.labels-sync.json remembers each
file's md5 as of the last sync, so a file that differs on the two sides can be told apart:

- changed here only (the bucket still has what we last synced) -> upload;
- changed in the bucket only (another checkout uploaded) -> download;
- changed on both -> a conflict: left alone and reported, until rerun with
  --conflicts local|remote.

Deletions are never propagated, in either direction: a file missing on one side is copied
from the other. So clearing data/ (or a fresh checkout) gets everything back. To really
delete a label file, delete it here and `gcloud storage rm` it from the bucket.

Auth: `gcloud auth application-default login` once.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import time
from pathlib import Path

from common import paths

STATE = paths.RECORDINGS_DATA / ".labels-sync.json"
SKIP = {".DS_Store"}


def md5(path: Path) -> str:
    """The file's md5 as GCS reports it (blob.md5_hash: base64)."""
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode()


def wanted(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return not (name in SKIP or name.endswith(".part"))


def download(blob, dest: Path) -> None:
    """Via a .part file, so an interrupted sync never leaves a truncated file under its name."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    blob.download_to_filename(str(tmp))
    tmp.replace(dest)


# ------------------------------------------------------------------ raw: bucket -> laptop

def sync_raw(bucket, dest: Path = paths.RECORDINGS, dry_run: bool = False) -> dict[str, int]:
    """Download every file of every complete recording in raw/ that isn't here yet.
    Returns {id: number of files downloaded}."""
    by_id: dict[str, list] = {}
    for b in bucket.list_blobs(prefix=paths.RAW_PREFIX):
        rel = b.name[len(paths.RAW_PREFIX):]
        if "/" in rel and wanted(rel):
            by_id.setdefault(rel.split("/", 1)[0], []).append(b)
    got = {}
    for rid, blobs in sorted(by_id.items()):
        if not any(b.name.endswith("/meta.json") for b in blobs):
            print(f"raw/{rid}: still uploading (no meta.json yet), skipped")
            continue
        todo = [b for b in blobs if not (dest / b.name[len(paths.RAW_PREFIX):]).exists()]
        if not todo:
            continue
        got[rid] = len(todo)
        if dry_run:
            continue
        # meta.json last, as in the bucket: a local meta.json also means the recording is all here
        for b in sorted(todo, key=lambda b: (b.name.endswith("/meta.json"), b.name)):
            download(b, dest / b.name[len(paths.RAW_PREFIX):])
    return got


# ------------------------------------------------------------------ labels: both ways

def load_state(path: Path) -> dict[str, str]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def save_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(dict(sorted(state.items())), indent=1) + "\n")
    tmp.replace(path)


def sync_labels(bucket, local: Path = paths.LABELS, state_path: Path = STATE,
                conflicts: str | None = None, dry_run: bool = False) -> dict[str, list[str]]:
    """Sync labels/ both ways (see the module docstring). Returns the relative paths by what
    happened to them: uploaded, downloaded, conflict."""
    state = load_state(state_path)
    remote = {}
    for b in bucket.list_blobs(prefix=paths.LABELS_PREFIX):
        rel = b.name[len(paths.LABELS_PREFIX):]
        if rel and not rel.endswith("/") and wanted(rel):
            remote[rel] = b
    here = {}
    if local.exists():
        for p in local.rglob("*"):
            rel = p.relative_to(local).as_posix()
            if p.is_file() and wanted(rel) and not any(part.startswith(".") for part in rel.split("/")):
                here[rel] = md5(p)

    done: dict[str, list[str]] = {"uploaded": [], "downloaded": [], "conflict": []}

    def upload(rel: str, generation: int) -> None:
        if not dry_run:
            from google.api_core.exceptions import PreconditionFailed
            try:
                # create-only for a new file, or replace exactly the version we compared
                # against: if another checkout uploaded in between, this fails instead of
                # overwriting it
                bucket.blob(paths.LABELS_PREFIX + rel).upload_from_filename(
                    str(local / rel), if_generation_match=generation)
            except PreconditionFailed:
                done["conflict"].append(rel)  # the next sync sorts it out
                return
            state[rel] = here[rel]
        done["uploaded"].append(rel)

    def fetch(rel: str) -> None:
        if not dry_run:
            download(remote[rel], local / rel)
            state[rel] = remote[rel].md5_hash
        done["downloaded"].append(rel)

    for rel in sorted(set(remote) | set(here)):
        mine, theirs, base = here.get(rel), remote.get(rel), state.get(rel)
        if theirs is None:
            upload(rel, 0)
        elif mine is None:
            fetch(rel)
        elif mine == theirs.md5_hash:
            state[rel] = mine
        elif base == theirs.md5_hash:
            upload(rel, theirs.generation)
        elif base == mine:
            fetch(rel)
        elif conflicts == "local":
            upload(rel, theirs.generation)
        elif conflicts == "remote":
            fetch(rel)
        else:
            done["conflict"].append(rel)

    if not dry_run:
        save_state(state_path, state)
    return done


def stray(bucket) -> list[str]:
    """Top-level names in the bucket outside raw/ and labels/ (an old app build uploading to
    <id>/, say)."""
    it = bucket.list_blobs(delimiter="/")
    names = [b.name for b in it]
    names += list(it.prefixes)
    return sorted(n for n in names if n not in (paths.RAW_PREFIX, paths.LABELS_PREFIX))


def main() -> None:
    ap = argparse.ArgumentParser(prog="just sync-recordings", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bucket", default=paths.BUCKET)
    ap.add_argument("--conflicts", choices=["local", "remote"],
                    help="resolve label files changed on both sides by keeping this side's")
    ap.add_argument("--dry-run", action="store_true", help="only say what would be done")
    a = ap.parse_args()

    from google.cloud import storage
    bucket = storage.Client().bucket(a.bucket)
    t0 = time.time()
    would = "would be " if a.dry_run else ""

    got = sync_raw(bucket, dry_run=a.dry_run)
    for rid, n in got.items():
        print(f"raw/{rid}: {n} file(s) {would}downloaded")
    labels = sync_labels(bucket, conflicts=a.conflicts, dry_run=a.dry_run)
    for what in ("uploaded", "downloaded"):
        for rel in labels[what]:
            print(f"labels/{rel}: {would}{what}")
    for rel in labels["conflict"]:
        print(f"labels/{rel}: CONFLICT, changed here and in the bucket since the last sync; "
              "left alone (rerun with --conflicts local|remote)")
    for name in stray(bucket):
        print(f"warning: gs://{a.bucket}/{name} is outside raw/ and labels/; an old app build? "
              f"(move it: gcloud storage mv gs://{a.bucket}/{name} gs://{a.bucket}/{paths.RAW_PREFIX})")

    print(f"sync with gs://{a.bucket}: {len(got)} recording(s), {len(labels['uploaded'])} label file(s) up, "
          f"{len(labels['downloaded'])} down, {len(labels['conflict'])} conflict(s) in {time.time() - t0:.1f}s"
          + (" (dry run)" if a.dry_run else ""))


if __name__ == "__main__":
    main()
