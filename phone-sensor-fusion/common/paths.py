"""Where things live. Raw recordings and everything derived are under the repo's
(gitignored) data/, and so are the labels (a ride's stations and times are the rider's
private business); only code, docs and learned parameters are checked in."""

from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # phone-sensor-fusion/
REPO = HERE.parent
RECORDINGS = REPO / "data" / "app-recordings"  # raw, as uploaded by the phone
DERIVED = REPO / "data" / "phone-sensor-fusion"  # caches and outputs, rebuildable
LABELS = REPO / "data" / "phone-sensor-fusion-labels"  # manual labels: not rebuildable, and not checked in
PARAMS = HERE / "params"  # all.json: fitted on every labelled recording (checked in)
CV_PARAMS = DERIVED / "params"  # leave-one-out fits, named after recordings (not checked in)


def recording_ids() -> list[str]:
    return sorted(p.name for p in RECORDINGS.iterdir() if (p / "meta.json").exists())


def derived(*parts: str) -> Path:
    p = DERIVED.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
