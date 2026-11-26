"""Where things live. Raw recordings and everything derived are under the repo's
(gitignored) data/, and so are the labels (a ride's stations and times are the rider's
private business); only code, docs and learned parameters are checked in."""

from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # phone-sensor-fusion/
REPO = HERE.parent
# data/recordings/ mirrors the recordings bucket: raw/ is what the phone uploaded, labels/
# what we made by hand. `just sync-recordings` (common/sync.py) keeps the two in step.
BUCKET = "train-fusion-recordings"  # var.recordings_bucket in infra/
RAW_PREFIX = "raw/"  # uploads go to raw/<recording id>-<8 hex>/ (backend/, docs/api.md)
LABELS_PREFIX = "labels/"
RECORDINGS_DATA = REPO / "data" / "recordings"
RECORDINGS = RECORDINGS_DATA / "raw"  # raw, as uploaded by the phone: bucket -> laptop only
LABELS = RECORDINGS_DATA / "labels"  # manual labels: not rebuildable, not checked in, synced both ways
DERIVED = REPO / "data" / "phone-sensor-fusion"  # caches and outputs, rebuildable
PARAMS = HERE / "params"  # all.json: fitted on every labelled recording (checked in)
CV_PARAMS = DERIVED / "params"  # leave-one-out fits, named after recordings (not checked in)
RUNS = DERIVED / "eval" / "runs"  # one folder per evaluation run (pipeline.py), kept to compare


def recording_ids() -> list[str]:
    return sorted(p.name for p in RECORDINGS.iterdir() if (p / "meta.json").exists())


def labelled_ids() -> list[str]:
    """The recordings that have been labelled (labels/README.md); the rest wait for it."""
    return [rid for rid in recording_ids() if (LABELS / rid / "stops.csv").exists()]


def derived(*parts: str) -> Path:
    p = DERIVED.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
