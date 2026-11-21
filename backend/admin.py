"""Admin commands for the recorder API, run on a laptop with your own credentials
(`gcloud auth application-default login`). Through the Justfile:

    just recording-deletions                 # pending deletion requests, and what to run for each
    just recording-deletions --done <id>     # after you've deleted that request's uploads by hand
    just block-account <uid>                 # disable the account and refuse it in the API

Nothing here deletes recordings: deleting an upload is done by hand, carefully, each time
(docs/accounts-and-uploads.md).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from api.app import URL_LIFETIME  # noqa: E402

PROJECT = os.environ.get("GCP_PROJECT", "train-fusion")
BUCKET = os.environ.get("RECORDINGS_BUCKET", "train-fusion-recordings")


def docs():
    from api.gcp import FirestoreDocs

    return FirestoreDocs(PROJECT)


def fmt(t: datetime | None) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if t else "-"


def deletions(args) -> None:
    d = docs()
    if args.done:
        path = f"deletion_requests/{args.done}"
        req = d.get(path)
        if req is None:
            sys.exit(f"no deletion request {args.done}")
        if req.get("done_at") is not None:
            sys.exit(f"{args.done} was already marked done at {fmt(req['done_at'])}")
        d.update(path, {"done_at": datetime.now(timezone.utc)})
        print(f"marked {args.done} done ({len(req['upload_ids'])} upload(s))")
        return

    pending = sorted(((i, r) for i, r in d.query("deletion_requests") if r.get("done_at") is None),
                     key=lambda x: x[1]["requested_at"])
    if not pending:
        print("no pending deletion requests")
        return
    now = datetime.now(timezone.utc)
    for request_id, r in pending:
        due = r["requested_at"] + timedelta(days=30)
        print(f"deletion request {request_id}: {r['reason']}, requested {fmt(r['requested_at'])}, "
              f"due by {fmt(due)}{'  OVERDUE' if due < now else ''}")
        print(f"  account: {r.get('uid') or '-'}")
        for upload_id in r["upload_ids"]:
            u = d.get(f"uploads/{upload_id}") or {}
            mb = u.get("total_bytes", 0) / 1e6
            print(f"  {upload_id}  {u.get('status', '?')}, {mb:.0f} MB")
        usable_until = r["requested_at"] + URL_LIFETIME
        if usable_until > now:
            print(f"  note: upload URLs handed out before the request work until {fmt(usable_until)};"
                  " deleting after then catches files still arriving")
        print("  run:")
        for upload_id in r["upload_ids"]:
            print(f"    gcloud storage rm -r gs://{BUCKET}/raw/{upload_id}/")
        print("  (a pending upload may have no files: 'no URLs matched' is fine.) If one was labelled,")
        print(f"  also gs://{BUCKET}/labels/<upload id>/ and its line in labels/RIDES.md. Then local copies")
        print("  (data/recordings/{raw,labels}/<upload id>/ on every laptop that synced). Then:")
        print(f"    just recording-deletions --done {request_id}")
        print()


def block(args) -> None:
    from api.gcp import IdentityPlatform

    IdentityPlatform(PROJECT, quota_project=PROJECT).disable_user(args.uid)
    d = docs()
    path = f"users/{args.uid}"
    if d.get(path) is None:
        d.set(path, {"created_at": datetime.now(timezone.utc), "email": None, "blocked": True})
    else:
        d.update(path, {"blocked": True})
    print(f"blocked {args.uid}: disabled in Identity Platform, and the API refuses it")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("deletions", help="list pending deletion requests, or mark one done")
    p.add_argument("--done", metavar="REQUEST_ID", help="mark this request done (after deleting by hand)")
    p.set_defaults(fn=deletions)
    p = sub.add_parser("block", help="disable an account and mark it blocked")
    p.add_argument("uid")
    p.set_defaults(fn=block)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
