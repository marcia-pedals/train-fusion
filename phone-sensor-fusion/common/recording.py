"""Load one phone recording (format: docs/recorder.md).

Every stream is put on one time axis `t`: seconds of phone uptime since the recording
started (`meta.started_uptime_s`). `Recording.unix(t)` converts to true clock time.

This is the only module that reads the raw recording files.
"""

from __future__ import annotations

import csv
import gzip
import json
import subprocess
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np

from . import paths


def _read_csv_gz(path: Path) -> np.ndarray:
    with gzip.open(path, "rt") as f:
        return np.loadtxt(f, delimiter=",", skiprows=1)


@dataclass
class Recording:
    id: str

    @property
    def dir(self) -> Path:
        return paths.RECORDINGS / self.id

    @cached_property
    def meta(self) -> dict:
        return json.loads((self.dir / "meta.json").read_text())

    @property
    def t0_uptime(self) -> float:
        return self.meta["started_uptime_s"]

    @property
    def duration(self) -> float:
        return self.meta["ended_uptime_s"] - self.t0_uptime

    @cached_property
    def unix_minus_uptime(self) -> float:
        """True unix time minus uptime, from the NTP exchange with the smallest round trip
        (or the phone's own clock if there was never a network)."""
        rows = list(csv.DictReader((self.dir / "clock.csv").open()))
        ntp = [r for r in rows if r["ntp_offset_s"]]
        if ntp:
            r = min(ntp, key=lambda r: float(r["ntp_round_trip_s"]))
            return float(r["unix_s"]) + float(r["ntp_offset_s"]) - float(r["uptime_s"])
        r = rows[0]
        return float(r["unix_s"]) - float(r["uptime_s"])

    def unix(self, t):
        return np.asarray(t) + self.t0_uptime + self.unix_minus_uptime

    @cached_property
    def accel(self) -> tuple[np.ndarray, np.ndarray]:
        """(t [n], xyz in g [n,3]); gravity included; phone axes."""
        a = self._cached_npy("accel", lambda: _read_csv_gz(self.dir / "accel.csv.gz"))
        return a[:, 0] - self.t0_uptime, a[:, 1:]

    @cached_property
    def gyro(self) -> tuple[np.ndarray, np.ndarray]:
        """(t [n], xyz in rad/s [n,3]); phone axes."""
        g = self._cached_npy("gyro", lambda: _read_csv_gz(self.dir / "gyro.csv.gz"))
        return g[:, 0] - self.t0_uptime, g[:, 1:]

    # The sensors below exist only in newer recordings (`has` says which).

    def has(self, name: str) -> bool:
        return (self.dir / f"{name}.csv.gz").exists()

    @cached_property
    def mag(self) -> tuple[np.ndarray, np.ndarray]:
        """(t [n], xyz in microtesla [n,3]); raw magnetometer, phone axes, uncalibrated
        (includes the phone's own hard-iron offset, hundreds of uT)."""
        m = self._cached_npy("mag", lambda: _read_csv_gz(self.dir / "mag.csv.gz"))
        return m[:, 0] - self.t0_uptime, m[:, 1:]

    @cached_property
    def baro(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(t [n], pressure kPa [n], iOS relative altitude m [n]); about 0.8 Hz."""
        b = self._cached_npy("baro", lambda: _read_csv_gz(self.dir / "baro.csv.gz"))
        return b[:, 0] - self.t0_uptime, b[:, 1], b[:, 2]

    @cached_property
    def device_motion(self) -> dict[str, np.ndarray]:
        """iOS's fused motion, 100 Hz, reference frame x = magnetic north, z = up:
        t [n]; q [n,4] attitude quaternion (x, y, z, w); rot [n,3] bias-corrected rotation
        rate rad/s; gravity [n,3] and user_accel [n,3] in g, phone axes; mag [n,3]
        calibrated field uT; mag_accuracy [n]; heading [n] degrees."""
        d = self._cached_npy("device_motion", lambda: _read_csv_gz(self.dir / "device_motion.csv.gz"))
        return {"t": d[:, 0] - self.t0_uptime, "q": d[:, 1:5], "rot": d[:, 5:8], "gravity": d[:, 8:11],
                "user_accel": d[:, 11:14], "mag": d[:, 14:17], "mag_accuracy": d[:, 17], "heading": d[:, 18]}

    def _cached_npy(self, name: str, make) -> np.ndarray:
        p = paths.derived("cache", f"{self.id}.{name}.npy")
        if not p.exists():
            np.save(p, make())
        return np.load(p)

    def audio(self, sample_rate: int = 16000) -> tuple[float, np.ndarray]:
        """(t of sample 0, float32 samples) resampled to `sample_rate`.

        Only single-segment recordings so far (both we have are); the buffer timing file
        is checked for gaps so a dropped buffer can't silently shift everything after it.
        """
        segs = self.meta["audio"]["segments"]
        assert len(segs) == 1, "multi-segment audio not handled yet"
        timing = _read_csv_gz(self.dir / "audio_timing.csv.gz")
        native = segs[0]["sample_rate"]
        # uptime of frame 0 implied by every buffer; they must agree (no gaps, no drift)
        implied = timing[:, 3] - timing[:, 1] / native
        assert np.ptp(implied) < 0.05, f"audio timing drifts/gaps by {np.ptp(implied):.3f}s"
        t_first = float(np.median(implied)) - self.t0_uptime
        t_first -= self.meta["audio"].get("input_latency_s") or 0.0
        p = paths.derived("cache", f"{self.id}.audio{sample_rate}.npy")
        if not p.exists():
            raw = subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-i", str(self.dir / segs[0]["file"]),
                 "-ar", str(sample_rate), "-ac", "1", "-f", "f32le", "-"],
                check=True, capture_output=True).stdout
            np.save(p, np.frombuffer(raw, dtype=np.float32))
        return t_first, np.load(p, mmap_mode="r")


def load_all() -> list[Recording]:
    return [Recording(i) for i in paths.recording_ids()]
