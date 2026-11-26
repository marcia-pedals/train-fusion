"""common/recording.py: placing audio buffers in time (audio_runs), from made-up timing rows."""
import sys
from pathlib import Path

import numpy as np
from pytest import approx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.recording import audio_runs  # noqa: E402

FS = 48000
BUF = 4800  # 0.1 s


def rows(segment, n, uptime0, first_frame=0, jitter=0.0, seed=0):
    """n back-to-back buffers of one segment, the first captured at uptime0."""
    rng = np.random.default_rng(seed)
    frame = first_frame + BUF * np.arange(n)
    up = uptime0 + (frame - first_frame) / FS + rng.uniform(-jitter, jitter, n)
    return np.c_[np.full(n, segment), frame, np.full(n, BUF), up]


SEG = [{"sample_rate": FS}]


def test_one_clean_run():
    assert audio_runs(rows(0, 50, 100.3, jitter=1e-4), SEG, 100.0) == [(0, 0, None, approx(0.3, abs=1e-3))]


def test_dropped_buffer_leaves_a_gap():
    # frames continue in the file, but 0.1 s of capture time went missing after buffer 10
    t = rows(0, 30, 100.3)
    t[10:, 3] += 0.1
    runs = audio_runs(t, SEG, 100.0)
    assert [(s, f0, f1) for s, f0, f1, _ in runs] == [(0, 0, 10 * BUF), (0, 10 * BUF, None)]
    assert runs[0][3] == approx(0.3, abs=1e-3)
    assert runs[1][3] == approx(1.4, abs=1e-3)


def test_second_segment_at_its_own_time():
    t = np.r_[rows(0, 20, 100.3), rows(1, 5, 150.0)]
    runs = audio_runs(t, SEG * 2, 100.0)
    assert [(s, f0, f1) for s, f0, f1, _ in runs] == [(0, 0, None), (1, 0, None)]
    assert runs[1][3] == approx(50.0, abs=1e-3)


def test_slow_drift_is_one_run():
    t = rows(0, 3600, 100.0)
    t[:, 3] += np.linspace(0, 0.02, len(t))  # 20 ms over 6 minutes
    assert len(audio_runs(t, SEG, 100.0)) == 1

