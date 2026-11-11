"""Service dates and GTFS time <-> epoch seconds. All times are epoch seconds internally."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
SERVICE_DAY_CUTOFF_HOURS = 3  # same as fetchers/gtfs/fetch.py


def service_date(epoch: float) -> str:
    """Caltrain operating day of an instant: local time minus 3 h."""
    local = datetime.fromtimestamp(epoch, TZ) - timedelta(hours=SERVICE_DAY_CUTOFF_HOURS)
    return local.strftime("%Y-%m-%d")


def service_day_origin(day: str) -> float:
    """Epoch of the GTFS service-day origin: local noon minus 12 h (correct on DST days)."""
    d = date.fromisoformat(day)
    noon = datetime.combine(d, time(12), TZ)
    return noon.timestamp() - 12 * 3600


def gtfs_seconds(hms: str) -> int:
    h, m, s = map(int, hms.split(":"))
    return h * 3600 + m * 60 + s


def local_hms(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, TZ).strftime("%H:%M:%S")


def day_type(day: str) -> str:
    return "weekend" if date.fromisoformat(day).weekday() >= 5 else "weekday"
