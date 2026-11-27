"""What the viewer's ride page shows, per test ride, written by pipeline.py next to the
evaluation (from the same cross-validated inference). What the model says goes into the
run's folder, <run>/rides/<id>/; the spectrograms, the same whatever the model, are
shared by every run, in data/phone-sensor-fusion/rides/<id>/:

  <run>/rides/<id>/
  meta.json        per-second series (speed, modes, regimes (standing first, then
                   states.REGIMES), the most likely (Viterbi) path's mode, regime and
                   speed, the causal localiser's
                   belief about the truth, and its best guess wherever that isn't the
                   truth), the labelled door events and stops, where the
                   audio is, and how to read the binary files (spec and accel: in the shared folder)
  speed_filt.bin, speed_smooth.bin
                   the belief over speed per second, filtered and smoothed, uint8
                   [second][0 = standing, 1.. = speed grid], each second scaled to its peak (sqrt)
  comb_tone.bin, comb_wheel.bin
                   what the audio (motor tone) and accelerometer (wheel) line spectra say
                   about speed: the log-likelihood terms the HMM gets, same layout as the
                   speed beliefs (standing gets 0), uint8 128 + 127 x ll / COMB_LL_MAX

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
from features import frames, phone_state
from labels import load
from labels.frames import FrameLabels, MODE_NAMES
from maps import track as trackmod
from model import emissions, states
from model.infer import Result

SPEC_FPS, SPEC_NWIN, SPEC_FMAX = 10, 1024, 5000.0
SPEC_DB = (-4.0, 30.0)  # dB over each frequency's median, mapped to 0..255
ACC_DB_MAX = 20.0  # accelerometer line prominence, 0..this dB mapped to 0..255
COMB_LL_MAX = 5.0  # comb log-likelihoods, nats: -this..this mapped to 1..255


def _r(x, nd=3):
    """Array -> JSON list, nan -> null."""
    x = np.asarray(x, dtype=float)
    if x.ndim > 1:
        return [_r(row, nd) for row in x]
    return [None if not np.isfinite(v) else round(float(v), nd) for v in x]


def _peak_scaled(p: np.ndarray) -> np.ndarray:
    m = p.max(axis=1, keepdims=True)
    return np.round(255 * np.sqrt(p / np.maximum(m, 1e-300))).astype(np.uint8)


_SPEEDS = np.r_[0.0, states.V_MOVING]  # the columns of states.speed_probs()


def _over_regimes(post: np.ndarray) -> np.ndarray:
    """[T, N] state probabilities -> [T, 1 + N_R]: standing, then each regime."""
    return np.c_[post[:, :states.N_STOPPED].sum(axis=1), states.regime_probs(post)]


def _wrong_guesses(wrong: np.ndarray, place: np.ndarray) -> list[dict]:
    """The stretches where the causal localiser's best guess is not the truth (however
    probable), one per (station, direction) it guesses there: [t0, t1) and that guess."""
    stations = trackmod.load().stations
    out = []
    for t in np.flatnonzero(wrong):
        a, d = (int(x) for x in place[t])
        if out and out[-1]["t1"] == t and (out[-1]["station"], out[-1]["dir"]) == (stations[a], "south" if d == 0 else "north"):
            out[-1]["t1"] = int(t) + 1
        else:
            out.append({"t0": int(t), "t1": int(t) + 1, "station": stations[a], "dir": "south" if d == 0 else "north"})
    return out


def _comb_u8(ll: np.ndarray) -> np.ndarray:
    """[T, N_V] log-likelihood over moving speeds -> [T, 1 + N_V] uint8, standing (0) first."""
    x = np.c_[np.zeros(len(ll)), ll]
    return np.round(128 + 127 * np.clip(x / COMB_LL_MAX, -1, 1)).astype(np.uint8)


def _levels(p: dict, f: dict) -> dict:
    """The level measurements and the curves the model expects them on (model/emissions.py
    _level_ll): per curve the speed knots, mean level at each, sd and the weight its
    log-likelihood gets; None where the training data had too little for one."""
    curve = lambda q: q and {k: q[k] for k in ("knots", "mean", "sd", "weight")}
    lv = p["level"]
    return {
        "rumble": {"x": _r(f["rumble_db"], 2), "curve": curve(lv["rumble"])},
        **{band: {"x": _r(f["vib_db"][:, i], 2),
                  "curves": {name: curve(lv[f"{band}/{name}"]) for name in ("resting", "held")}}
           for i, band in enumerate(("vib_low", "vib_high"))},
    }


def _interval(post: np.ndarray, p: float = 0.95) -> tuple[np.ndarray, np.ndarray]:
    """Per second, the central `p` interval of the belief over speed."""
    cdf = np.cumsum(states.speed_probs(post), axis=1)
    lo = _SPEEDS[np.argmax(cdf >= (1 - p) / 2, axis=1)]
    hi = _SPEEDS[np.argmax(cdf >= 1 - (1 - p) / 2, axis=1)]
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


def export(rid: str, p: dict, res: Result, f: dict, lab: FrameLabels, stops: list[load.Stop], trace: dict, run: Path) -> None:
    """`p`: the parameters `res` was inferred with; `trace`: what evaluate.ride() filled in
    about the causal localiser; `run`: the run's folder."""
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
    _peak_scaled(states.speed_probs(res.alpha)).tofile(out / "speed_filt.bin")
    _peak_scaled(states.speed_probs(res.gamma)).tofile(out / "speed_smooth.bin")
    phone_now = res.phone.argmax(axis=1)
    _comb_u8(emissions.tone_ll(p, f)).tofile(out / "comb_tone.bin")
    _comb_u8(emissions.wheel_ll(p, f, phone_now)).tofile(out / "comb_wheel.bin")

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
        "comb": {"tone": "comb_tone.bin", "wheel": "comb_wheel.bin", "ll_max": COMB_LL_MAX},
        "levels": _levels(p, f),
        "phone_states": list(phone_state.STATES),
        "mode_names": MODE_NAMES,
        "regime_names": list(states.REGIMES),
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
            "mode_path": [int(m) for m in res.path_mode], "v_path": _r(res.path_v, 2),
            "regime_filt": _r(_over_regimes(res.alpha), 3), "regime_smooth": _r(_over_regimes(res.gamma), 3),
            "regime_path": [int(r) + 1 for r in res.path_regime],
            "loc_p_true": _r(trace["p_true"], 3), "loc_p_best": _r(trace["p_best"], 3),
            "loc_wrong": [int(w) for w in trace["wrong"]],
            "phone": [int(k) for k in phone_now],
        },
        "loc_openings": [int(t) for t in trace["openings"]],
        "loc_wrong_guesses": _wrong_guesses(trace["best_wrong"], trace["best_place"]),
    }
    (out / "meta.json").write_text(json.dumps(meta, separators=(",", ":")))
