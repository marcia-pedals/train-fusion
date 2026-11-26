"""What the viewer's ride page shows, per test ride, written by pipeline.py next to the
evaluation (from the same cross-validated inference). What the model says goes into the
run's folder, <run>/rides/<id>/; the spectrograms, the same whatever the model, are
shared by every run, in data/phone-sensor-fusion/rides/<id>/:

  <run>/rides/<id>/
  meta.json        per-second series (speed, modes, the causal localiser's
                   belief about the truth), the labelled door events and stops, where the
                   audio is, and how to read the binary files (spec and accel: in the shared folder)
  speed_filt.bin, speed_smooth.bin
                   the belief over speed per second, filtered and smoothed, uint8
                   [second][0 = standing, 1.. = speed grid], each second scaled to its peak (sqrt)

  rides/<id>/
  spec.bin         audio spectrogram, SPEC_FPS frames/s, 0..SPEC_FMAX Hz, uint8 [frame][bin]
                   (dB over each frequency's median)
  accel.bin        accelerometer line spectrum, 1 frame/s, 0..50 Hz, uint8 [second][bin]

Recording time t is seconds since the recording started; frame n covers [n, n + 1).
The audio file itself is served from the recording (vite.config.ts); `audio_t0` is the
recording time of its first sample.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from common import paths
from common.dsp import spectrogram_db
from common.recording import Recording
from features import frames
from labels import load
from labels.frames import FrameLabels, MODE_NAMES
from model import states
from model.infer import Result

SPEC_FPS, SPEC_NWIN, SPEC_FMAX = 10, 1024, 5000.0
SPEC_DB = (-4.0, 30.0)  # dB over each frequency's median, mapped to 0..255
ACC_DB_MAX = 20.0  # accelerometer line prominence, 0..this dB mapped to 0..255


def _r(x, nd=3):
    """Array -> JSON list, nan -> null."""
    x = np.asarray(x, dtype=float)
    if x.ndim > 1:
        return [_r(row, nd) for row in x]
    return [None if not np.isfinite(v) else round(float(v), nd) for v in x]


def _peak_scaled(p: np.ndarray) -> np.ndarray:
    m = p.max(axis=1, keepdims=True)
    return np.round(255 * np.sqrt(p / np.maximum(m, 1e-300))).astype(np.uint8)


def _over_speed(post: np.ndarray) -> np.ndarray:
    """[T, N] state probabilities -> [T, 1 + N_V]: standing (all stopped modes), then speeds."""
    return np.c_[post[:, :states.N_STOPPED].sum(axis=1), post[:, states.N_STOPPED:]]


def _interval(post: np.ndarray, p: float = 0.95) -> tuple[np.ndarray, np.ndarray]:
    """Per second, the central `p` interval of the belief over speed (states.V is sorted:
    the standing modes at 0, then the grid)."""
    cdf = np.cumsum(post, axis=1)
    lo = states.V[np.argmax(cdf >= (1 - p) / 2, axis=1)]
    hi = states.V[np.argmax(cdf >= 1 - (1 - p) / 2, axis=1)]
    return lo, hi


def _events(rid: str, stops: list[load.Stop]) -> list[dict]:
    """Labelled door chimes: the start and end of each opening and closing chime, at the
    stops and wherever modes.csv has more (doors reopened during a hold)."""
    out = []
    for st in stops:
        for kind, t in (("open", st.t_open), ("open_end", st.t_open_end), ("close", st.t_close), ("close_end", st.t_close_end)):
            if np.isfinite(t):
                out.append({"t": float(t), "kind": kind, "station": st.station})
    for a, b, mode in load.modes(rid):
        if mode in ("open_chime", "close_chime"):
            kind = mode.split("_")[0]
            out += [{"t": a, "kind": kind, "station": None}, {"t": b, "kind": f"{kind}_end", "station": None}]
    return sorted(out, key=lambda e: e["t"])


def export(rid: str, res: Result, f: dict, lab: FrameLabels, stops: list[load.Stop], trace: dict, run: Path) -> None:
    """`trace`: what evaluate.ride() filled in about the causal localiser; `run`: the run's folder."""
    shared = paths.derived("rides", rid, "meta.json").parent
    out = run / "rides" / rid
    out.mkdir(parents=True, exist_ok=True)
    rec = Recording(rid)
    T = len(res.v)

    x = frames.aligned_audio(rec)
    fq, db = spectrogram_db(x, frames.FS, SPEC_NWIN, frames.FS // SPEC_FPS)
    keep = fq <= SPEC_FMAX
    db = db[keep] - np.median(db[keep], axis=1, keepdims=True)
    spec = np.round(255 * np.clip((db - SPEC_DB[0]) / (SPEC_DB[1] - SPEC_DB[0]), 0, 1)).astype(np.uint8).T
    spec.tofile(shared / "spec.bin")
    acc = np.round(255 * np.clip(f["accel_spec"].astype(float) / ACC_DB_MAX, 0, 1)).astype(np.uint8)
    acc.tofile(shared / "accel.bin")
    _peak_scaled(_over_speed(res.alpha)).tofile(out / "speed_filt.bin")
    _peak_scaled(_over_speed(res.gamma)).tofile(out / "speed_smooth.bin")

    v_filt_sd = np.sqrt(np.maximum(res.alpha @ states.V**2 - res.v_filt**2, 0))
    filt_lo, filt_hi = _interval(res.alpha)
    smooth_lo, smooth_hi = _interval(res.gamma)
    seg = rec.meta["audio"]["segments"][0]
    meta = {
        "id": rid, "T": T,
        "audio": {"file": seg["file"], "t0": float(rec.audio(frames.FS)[0])},
        "spec": {"file": "spec.bin", "fps": SPEC_FPS, "bins": int(spec.shape[1]), "frames": int(spec.shape[0]),
                 "fmax": float(fq[keep][-1])},
        "accel": {"file": "accel.bin", "fps": 1, "bins": int(acc.shape[1]), "frames": int(acc.shape[0]),
                  "fmax": float(f["accel_f"][-1])},
        "speed_post": {"filt": "speed_filt.bin", "smooth": "speed_smooth.bin", "bins": states.N_V + 1,
                       "dv": states.DV},
        "mode_names": MODE_NAMES,
        "stops": [{"station": s.station, **{k: (float(v) if np.isfinite(v) else None) for k, v in
                   (("t_arrive", s.t_arrive), ("t_open", s.t_open), ("t_close", s.t_close), ("t_depart", s.t_depart))}}
                  for s in stops],
        "events": _events(rid, stops),
        "series": {
            "v_smooth": _r(res.v, 2), "v_smooth_sd": _r(res.v_sd, 2),
            "v_filt": _r(res.v_filt, 2), "v_filt_sd": _r(v_filt_sd, 2),
            "v_filt_lo": _r(filt_lo, 2), "v_filt_hi": _r(filt_hi, 2),
            "v_smooth_lo": _r(smooth_lo, 2), "v_smooth_hi": _r(smooth_hi, 2),
            "v_label": _r(lab.v, 2), "v_label_confident": [int(c) for c in lab.confident],
            "mode_filt": _r(res.mode_p_filt, 3), "mode_smooth": _r(res.mode_p, 3),
            "mode_label": [int(m) for m in lab.mode],
            "loc_p_true": _r(trace["p_true"], 3), "loc_p_best": _r(trace["p_best"], 3),
            "loc_wrong": [int(w) for w in trace["wrong"]],
        },
        "loc_openings": [int(t) for t in trace["openings"]],
    }
    (out / "meta.json").write_text(json.dumps(meta, separators=(",", ":")))
