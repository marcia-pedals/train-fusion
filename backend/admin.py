"""Admin commands for the recorder API, run on a laptop with your own credentials
(`gcloud auth application-default login`). Through the Justfile:

    just deletions              # list what people asked to have deleted; type "delete" to delete it all
    just block-account <uid>    # disable the account and refuse it in the API

`just deletions` lists every pending deletion request (docs/accounts-and-uploads.md),
grouped by the account or device that asked, and then asks. Nothing is deleted unless you
type exactly "delete". Then, for each request:

- its Train Measurements (Firestore `measurements/`), skipping any shared again since;
- each Full Recording: `raw/<upload id>/` and `labels/<upload id>/` in the bucket, its
  lines in the bucket's `labels/RIDES.md` and `labels/HIDDEN`, and its upload record
  (Firestore `uploads/`);
- then the request is marked done.

It deletes only what's in the cloud: laptops' copies (`data/`) go when they're cleared and
fetched afresh.

A request whose upload URLs may still be in use (handed out up to 12 hours before it) waits
until they've expired, so files still arriving are caught. Running it again is safe: what's
gone already is skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from api.app import URL_LIFETIME  # noqa: E402

PROJECT = os.environ.get("GCP_PROJECT", "train-fusion")
BUCKET = os.environ.get("RECORDINGS_BUCKET", "train-fusion-recordings")
DUE_WITHIN = timedelta(days=30)  # what the app promises
RAW = "raw/"  # raw/<upload id>/: a recording as the phone uploaded it
LABELS = "labels/"  # labels/<upload id>/, and labels/RIDES.md (phone-sensor-fusion/common/sync.py)
RIDES = LABELS + "RIDES.md"
HIDDEN = LABELS + "HIDDEN"  # recordings laptops don't download (phone-sensor-fusion/common/sync.py)
CONFIRM = "delete"


# ------------------------------------------------------------------ the bucket

class Bucket:
    """What deleting needs of the recordings bucket (a google.cloud.storage Bucket)."""

    def __init__(self, bucket) -> None:
        self.bucket = bucket

    def sizes(self, prefix: str) -> dict[str, int]:
        return {b.name: b.size for b in self.bucket.list_blobs(prefix=prefix)}

    def read(self, name: str) -> tuple[bytes, int] | None:
        """The object's contents and generation, or None if there's no such object."""
        blob = self.bucket.get_blob(name)
        if blob is None:
            return None
        return blob.download_as_bytes(if_generation_match=blob.generation), blob.generation

    def write(self, name: str, data: bytes, generation: int) -> None:
        """Replace exactly that generation: fails if someone else changed it meanwhile."""
        self.bucket.blob(name).upload_from_string(data, if_generation_match=generation)

    def delete(self, name: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self.bucket.blob(name).delete()
        except NotFound:
            pass


# ------------------------------------------------------------------ what's asked for

@dataclass
class Group:
    """Everything one account or one device asked to have deleted."""

    who: str  # one line: "account <uid> (<email>)" or "device <hash…>"
    requests: list[tuple[str, dict]] = field(default_factory=list)
    measurements: dict[str, dict] = field(default_factory=dict)  # still there, still marked
    reshared: int = 0  # listed by a request, but shared again since: kept
    uploads: dict[str, dict] = field(default_factory=dict)


@dataclass
class Plan:
    groups: list[Group]
    waiting: list[tuple[str, datetime]]  # (request id, usable until): not yet
    rides_md: str  # the bucket's labels/RIDES.md, "" if none

    @property
    def requests(self) -> list[tuple[str, dict]]:
        return [r for g in self.groups for r in g.requests]

    def empty(self) -> bool:
        return not self.groups


def who(d, r: dict) -> tuple[str, str]:
    """The group a request belongs to: (key, line to show)."""
    uid = r.get("uid")
    if uid:
        user = d.get(f"users/{uid}")
        if user is None:
            about = "account deleted"
        else:
            about = user.get("email") or "no email"
            if user.get("blocked"):
                about += ", blocked"
        return f"account:{uid}", f"account {uid} ({about})"
    device = r.get("device_hash") or "?"
    return f"device:{device}", f"device {device[:12]}…"


def plan(d, bucket: Bucket, now: datetime) -> Plan:
    groups: dict[str, Group] = {}
    waiting = []
    for request_id, r in sorted(d.query("deletion_requests"), key=lambda x: x[1]["requested_at"]):
        if r.get("done_at") is not None:
            continue
        usable_until = r["requested_at"] + URL_LIFETIME
        if r.get("upload_ids") and usable_until > now:
            waiting.append((request_id, usable_until))
            continue
        key, line = who(d, r)
        g = groups.setdefault(key, Group(line))
        g.requests.append((request_id, r))
        for m in r.get("measurement_ids", []):
            doc = d.get(f"measurements/{m}")
            if doc is None or m in g.measurements:
                continue
            if doc.get("deletion_requested_at") is None:
                g.reshared += 1
            else:
                g.measurements[m] = doc
        for u in r.get("upload_ids", []):
            g.uploads[u] = d.get(f"uploads/{u}") or {}
    got = bucket.read(RIDES)
    return Plan(list(groups.values()), waiting, got[0].decode() if got else "")


# ------------------------------------------------------------------ the listing

def day(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%d")


def minutes(start, end) -> str:
    if start is None or end is None:
        return "? min"
    return f"{max(0, round((end - start) / 60))} min"


def mb(n: int) -> str:
    return f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{n / 1e6:.1f} MB"


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def recording_line(upload_id: str, bucket: Bucket, rides_md: str) -> str:
    raw = bucket.sizes(f"{RAW}{upload_id}/")
    parts = []
    if raw:
        meta = bucket.read(f"{RAW}{upload_id}/meta.json")
        m = json.loads(meta[0]) if meta else {}
        parts.append(f"{minutes(m.get('started_unix_s'), m.get('ended_unix_s'))}, {mb(sum(raw.values()))}")
    else:
        parts.append("nothing in the bucket")
    labels = bucket.sizes(f"{LABELS}{upload_id}/")
    if labels:
        parts.append(f"labels ({plural(len(labels), 'file')})")
    if upload_id in rides_md:
        parts.append("in RIDES.md")
    return f"    {upload_id}  " + "; ".join(parts)


def waiting_line(p: Plan) -> str:
    until = max(t for _, t in p.waiting)
    return (f"Not yet: {plural(len(p.waiting), 'request')} whose upload URLs work until "
            f"{until.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC; listed once they've expired.")


def show(p: Plan, bucket: Bucket, now: datetime, out: Callable[[str], None]) -> None:
    due = min(r["requested_at"] for _, r in p.requests) + DUE_WITHIN
    out(f"{plural(len(p.requests), 'deletion request')}, first due {day(due)}"
        + ("  OVERDUE" if due < now else "") + "\n")
    for g in p.groups:
        out(g.who)
        if g.measurements or g.reshared:
            kept = f" ({g.reshared} shared again since: kept)" if g.reshared else ""
            out(f"  Train Measurements: {len(g.measurements)}{kept}")
            for m, doc in sorted(g.measurements.items()):
                out(f"    {doc.get('ride_id', m)}  {minutes(doc.get('started_unix_s'), doc.get('ended_unix_s'))}, "
                    f"{plural(len(doc.get('stops') or []), 'stop')}")
        if g.uploads:
            out(f"  Full Recordings: {len(g.uploads)}")
            for u in sorted(g.uploads):
                out(recording_line(u, bucket, p.rides_md))
        out("")
    if p.waiting:
        out(waiting_line(p))


# ------------------------------------------------------------------ deleting

def delete_measurement(d, measurement_id: str) -> None:
    doc = d.get(f"measurements/{measurement_id}")
    if doc is not None and doc.get("deletion_requested_at") is not None:
        d.delete(f"measurements/{measurement_id}")


def strip_lines(bucket: Bucket, name: str, upload_id: str) -> None:
    """Drop the upload's lines from a list in the bucket, RIDES.md or HIDDEN (laptops get it
    at their next sync)."""
    got = bucket.read(name)
    if got is None:
        return
    data, generation = got
    lines = data.decode().splitlines(keepends=True)
    kept = [line for line in lines if upload_id not in line]
    if kept != lines:
        bucket.write(name, "".join(kept).encode(), generation)


def delete_upload(d, bucket: Bucket, upload_id: str) -> None:
    for prefix in (RAW, LABELS):
        for name in bucket.sizes(f"{prefix}{upload_id}/"):
            bucket.delete(name)
    for name in (RIDES, HIDDEN):
        strip_lines(bucket, name, upload_id)
    d.delete(f"uploads/{upload_id}")  # last: until it's gone, the app still shows it as asked to delete


def carry_out(p: Plan, d, bucket: Bucket, now: datetime, out: Callable[[str], None]) -> bool:
    """Delete everything in the plan, then mark done each request all of whose deletions
    worked. Returns whether everything worked."""
    failed: set[str] = set()

    def attempt(item: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as e:  # report it, carry on with the rest; rerunning picks it up
            failed.add(item)
            out(f"FAILED {item}: {e}")

    measurements = recordings = 0
    for g in p.groups:
        for m in g.measurements:
            attempt(m, lambda m=m: delete_measurement(d, m))
            measurements += m not in failed
        for u in g.uploads:
            attempt(u, lambda u=u: delete_upload(d, bucket, u))
            recordings += u not in failed

    done = 0
    for request_id, r in p.requests:
        if failed.isdisjoint(r.get("measurement_ids", [])) and failed.isdisjoint(r.get("upload_ids", [])):
            d.update(f"deletion_requests/{request_id}", {"done_at": now})
            done += 1
    out(f"Deleted {plural(measurements, 'Train Measurement')} and {plural(recordings, 'Full Recording')}; "
        f"{done} of {plural(len(p.requests), 'request')} done.")
    if recordings:
        out("The bucket keeps deleted objects for 7 days (soft delete).")
    return not failed


def deletions(d, bucket: Bucket, now: datetime,
              ask: Callable[[str], str] = input, out: Callable[[str], None] = print) -> bool:
    p = plan(d, bucket, now)
    if p.empty():
        out("Nothing to delete.")
        if p.waiting:
            out(waiting_line(p))
        return True
    show(p, bucket, now, out)
    try:
        answer = ask(f'Type "{CONFIRM}" to delete all of the above: ')
    except EOFError:
        answer = ""
    if answer.strip() != CONFIRM:
        out("Nothing deleted.")
        return True
    return carry_out(p, d, bucket, now, out)


# ------------------------------------------------------------------ commands

def firestore():
    from api.gcp import FirestoreDocs

    return FirestoreDocs(PROJECT)


def recordings_bucket() -> Bucket:
    from google.cloud import storage

    return Bucket(storage.Client(project=PROJECT).bucket(BUCKET))


def deletions_cmd(args) -> None:
    ok = deletions(firestore(), recordings_bucket(), datetime.now(timezone.utc))
    sys.exit(0 if ok else 1)


def block(args) -> None:
    from api.gcp import IdentityPlatform

    IdentityPlatform(PROJECT, quota_project=PROJECT).disable_user(args.uid)
    d = firestore()
    path = f"users/{args.uid}"
    if d.get(path) is None:
        d.set(path, {"created_at": datetime.now(timezone.utc), "email": None, "blocked": True})
    else:
        d.update(path, {"blocked": True})
    print(f"blocked {args.uid}: disabled in Identity Platform, and the API refuses it")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("deletions", help='list pending deletion requests; type "delete" to carry them all out')
    p.set_defaults(fn=deletions_cmd)
    p = sub.add_parser("block", help="disable an account and mark it blocked")
    p.add_argument("uid")
    p.set_defaults(fn=block)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
