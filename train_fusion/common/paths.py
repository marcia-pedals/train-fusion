"""Where everything lives on disk."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
RT = DATA / "rt"
GTFS = DATA / "gtfs"
DERIVED = DATA / "derived"
REFERENCE = DERIVED / "reference"
SPEED_LIMITS = ROOT / "track" / "speed_limits.toml"
# The raw archive in GCS (infra/); data/rt and data/gtfs are a local cache of it (`just sync`).
ARCHIVE_BUCKET = os.environ.get("ARCHIVE_BUCKET", "train-fusion-archive")


def archived_dates() -> list[str]:
    return sorted(p.name for p in RT.iterdir() if p.is_dir()) if RT.exists() else []


def day_dir(date: str) -> Path:
    return DERIVED / date


def static_dir(version: str) -> Path:
    return DERIVED / "static" / version


def built_dates() -> list[str]:
    return [d for d in archived_dates() if (day_dir(d) / "vp_lin.parquet").exists()]
