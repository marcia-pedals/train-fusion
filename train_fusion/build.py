"""`just build <dates>` / `just build --last-week`: rebuild data/derived from scratch for
exactly these service dates: static (the feed versions they use), decode, linearize, the
timetable, and the reference measured against them."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta

from train_fusion.common import paths
from train_fusion.common.timeutil import service_date
from train_fusion.stages.decode import stage as decode
from train_fusion.stages.linearize import stage as linearize
from train_fusion.stages.reference import stage as reference
from train_fusion.stages.static import stage as static
from train_fusion.stages.timetable import stage as timetable


def _decode(day: str) -> tuple[str, dict]:
    out = decode.run(day)
    return day, {k: len(v) for k, v in out.items() if k != "fetches"}


def _linearize(day: str) -> tuple[str, dict]:
    return day, linearize.run(day)


def main(dates: list[str]) -> None:
    t0 = time.time()
    shutil.rmtree(paths.DERIVED, ignore_errors=True)
    for z in sorted({static.zip_for_date(d) for d in dates}):
        print(f"static  {z.name} -> {static.run(z)}")
    with ProcessPoolExecutor() as ex:
        for day, n in ex.map(_decode, dates):
            print(f"decode  {day} {n}")
    with ProcessPoolExecutor() as ex:
        for day, info in ex.map(_linearize, dates):
            print(f"linearize {day} {info}")
    print(f"timetable {timetable.run(dates)}")
    print(f"reference {json.dumps(reference.run(dates), indent=1)}")
    print(f"build of {dates} done in {time.time() - t0:.1f}s")


def last_week() -> list[str]:
    """Archived service dates among the 7 days ending today."""
    today = date.fromisoformat(service_date(time.time()))
    week = {(today - timedelta(days=k)).isoformat() for k in range(7)}
    return [d for d in paths.archived_dates() if d in week]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="just build", description=__doc__)
    ap.add_argument("dates", nargs="*", help="service dates, e.g. 2026-11-07")
    ap.add_argument("--last-week", action="store_true", help="every archived date among the 7 days ending today")
    a = ap.parse_args()
    if bool(a.dates) == a.last_week:
        ap.error("give either dates or --last-week")
    dates = last_week() if a.last_week else sorted(set(a.dates))
    if not dates:
        sys.exit("no archived dates in the last week")
    if bad := [d for d in dates if d not in paths.archived_dates()]:
        sys.exit(f"no archive for {bad}; have {paths.archived_dates()}")
    main(dates)
