"""Export everything the viewer shows, per recording, to data/phone-sensor-fusion/viewer/<id>/:

  meta.json      per-second series (features, labels, filtered and smoothed inference),
                 the station layer's beliefs, and descriptions of the binary files
  spec.bin       audio spectrogram, 10 frames/s, 0..5 kHz, uint8 [frame][bin]
  accel.bin      accelerometer line spectrum, 1 frame/s, 0..50 Hz, uint8 [second][bin]
  post_smooth.bin, post_filt.bin, tone_like.bin, wheel_like.bin
                 per second, over speed: the smoothed / filtered posterior, the motor
                 tone's likelihood and the wheel-rotation lines' likelihood, uint8 [second][0 = standing, 1.. = speed grid],
                 each second scaled to its own peak (sqrt)

Inference is cross-validated by default: each recording is run with the parameters fitted
without it (data/phone-sensor-fusion/params/without-<id>.json), i.e. exactly what the pipeline evaluates.

    python -m viewer.export [--params all]
"""

import argparse
import json

import numpy as np

from common import paths
from common.dsp import spectrogram_db
from common.recording import Recording
from features import comb, frames, motion, phone_state
from labels import load as label_load
from labels.frames import MODE_NAMES, frame_labels
from maps import track as trackmod
from model import emissions, evaluate, infer, states, stations

SPEC_FPS, SPEC_NWIN, SPEC_FMAX = 10, 1024, 5000.0
SPEC_DB = (-4.0, 30.0)  # dB over each frequency's median, mapped to 0..255
ACC_DB_MAX = 20.0  # accelerometer line prominence, 0..this dB mapped to 0..255


def r(x, nd=3):
    """Array -> JSON list, nan -> null."""
    x = np.asarray(x, dtype=float)
    return [None if not np.isfinite(v) else round(float(v), nd) for v in x.ravel()] if x.ndim == 1 else \
        [r(row, nd) for row in x]


def peak_scaled(p):
    """[T, K] non-negative -> uint8, each row relative to its peak, sqrt for visibility."""
    m = p.max(axis=1, keepdims=True)
    return np.round(255 * np.sqrt(p / np.maximum(m, 1e-300))).astype(np.uint8)


def over_speed(state_p):
    """[T, N] state probabilities -> [T, 1 + N_V]: standing (all stopped modes), then speeds."""
    return np.c_[state_p[:, :states.N_STOPPED].sum(axis=1), state_p[:, states.MOVING_SLICE]]


def export(rid: str, params_name: str):
    out = paths.derived("viewer", rid, "meta.json").parent
    folder = paths.CV_PARAMS if params_name.startswith("without-") else paths.PARAMS
    p = json.loads((folder / f"{params_name}.json").read_text())
    rec, f, lab = Recording(rid), frames.load(rid), frame_labels(rid)
    res = infer.infer(p, f)
    T = len(res.v)

    # audio spectrogram on the recording's time axis
    x = frames.aligned_audio(rec)
    hop = frames.FS // SPEC_FPS
    fq, db = spectrogram_db(x, frames.FS, SPEC_NWIN, hop)
    keep = fq <= SPEC_FMAX
    db = db[keep] - np.median(db[keep], axis=1, keepdims=True)
    spec = np.round(255 * np.clip((db - SPEC_DB[0]) / (SPEC_DB[1] - SPEC_DB[0]), 0, 1)).astype(np.uint8).T
    spec.tofile(out / "spec.bin")

    peak_scaled(over_speed(res.gamma)).tofile(out / "post_smooth.bin")
    peak_scaled(over_speed(res.alpha)).tofile(out / "post_filt.bin")
    ll = comb.apply(p["tone"], f["tone_spec"], f["tone_f"], states.V_MOVING, frames.TONE_POOL_BINS)
    like = np.exp(ll - ll.max(axis=1, keepdims=True))
    peak_scaled(np.c_[np.zeros(T), like]).tofile(out / "tone_like.bin")

    # accelerometer line spectrum and the wheel-rotation likelihood as the model uses it
    acc = np.round(255 * np.clip(f["accel_spec"].astype(float) / ACC_DB_MAX, 0, 1)).astype(np.uint8)
    acc.tofile(out / "accel.bin")
    state = res.phone.argmax(axis=1)
    notes, ratios = [], []
    for k, name in ((phone_state.RESTING, "resting"), (phone_state.HELD, "held")):
        tables, borrowed = emissions.wheel_tables(p, name)
        share = float(np.mean(state == k))
        if tables and share > 0.005:
            ratios += tables["ratios"]
            notes.append(f"{share:.0%} of seconds {name}" + (", tables borrowed from the other phone state" if borrowed else ""))
    wheel_note = "used by this model: " + ("; ".join(notes) if notes else "never")
    wheel_ratios = sorted(set(round(x, 4) for x in ratios))
    ll = emissions.wheel_ll(p, f, state)
    peak_scaled(np.c_[np.zeros(T), np.exp(ll - ll.max(axis=1, keepdims=True))]).tofile(out / "wheel_like.bin")

    # along-track acceleration with the model's sign and offset applied
    mo = motion.extract(f, res.phone[:, phone_state.RESTING])
    a_long = np.full(T, np.nan)
    for s in res.stretches:
        if s["sign"]:
            sl = slice(s["t0"], s["t1"])
            a_long[sl] = (mo["a_long"][sl] - s["offset"]) * s["sign"]
    on = res.alpha[:, :states.N_ON]  # speed given that the rider is on the train, as res.v_filt
    v_filt_sd = np.sqrt(np.maximum((on @ states.V[:states.N_ON]**2) / np.maximum(on.sum(axis=1), 1e-300) - res.v_filt**2, 0))

    tr = trackmod.load()
    loc = stations.localise(res, f)
    ev = evaluate.evaluate(rid, res, lab)
    journey = label_load.journey(rid)
    meta = {
        "id": rid, "T": T, "params": ("fitted without this recording" if params_name == f"without-{rid}" else f"params/{params_name}.json"), "audio_t0": rec.audio(frames.FS)[0],
        "journey": {"direction": journey["direction"], "stations": journey["stations"], "phone": journey["phone"]},
        "summary": (f"{ev['stops_found']} of {ev['stops_labelled']} stops found, {ev['stops_spurious']} false · "
                    f"leg distance error {ev['leg_err_mean_abs']:.1%} mean, {ev['leg_err_max_abs']:.1%} max "
                    f"({ev['leg_err_mean_abs_filter']:.1%} filter only) · "
                    f"speed RMSE {ev['speed_rmse']:.2f} m/s smoothed, {ev['speed_rmse_filter']:.2f} filtered"),
        "legs": ev["legs"],
        "mode_names": MODE_NAMES, "phone_names": list(phone_state.STATES),
        "speed_grid": {"dv": states.DV, "n": states.N_V},
        "tone_ratios": p["tone"]["ratios"],
        "spec": {"file": "spec.bin", "fps": SPEC_FPS, "bins": int(spec.shape[1]), "frames": int(spec.shape[0]),
                 "fmax": float(fq[keep][-1])},
        "heat": {"smooth": "post_smooth.bin", "filt": "post_filt.bin", "tone": "tone_like.bin",
                 "wheel": "wheel_like.bin", "bins": states.N_V + 1},
        "accel": {"file": "accel.bin", "bins": int(acc.shape[1]), "fmax": float(f["accel_f"][-1]),
                  "wheel_ratios": wheel_ratios, "note": wheel_note},
        "stops": [{"station": s.station, "t_arrive": s.t_arrive, "t_open": s.t_open, "t_close": s.t_close,
                   "t_depart": s.t_depart} for s in label_load.stops(rid)],
        "series": {
            "v_smooth": r(res.v, 2), "v_smooth_sd": r(res.v_sd, 2),
            "v_filt": r(res.v_filt, 2), "v_filt_sd": r(v_filt_sd, 2),
            "v_label": r(lab.v, 2), "v_label_confident": [int(c) for c in lab.confident],
            "mode_smooth": r(res.mode_p, 3), "mode_filt": r(states.mode_probs(res.alpha), 3),
            "mode_label": [int(m) for m in lab.mode], "on_train_label": [int(m) for m in lab.on_train], "mode_path": [int(m) for m in res.path_mode],
            "phone": r(res.phone, 3), "phone_label": [int(m) for m in lab.phone],
            "dist_smooth": r(res.distance[:T], 0), "dist_filt": r(res.distance_filt[:T], 0),
            "open_chime": r(f["open_chime"], 2), "close_chime": r(f["close_chime"], 2),
            "rumble_db": r(f["rumble_db"], 1),
            "vib_low_db": r(f["vib_db"][:, 0], 1), "vib_high_db": r(f["vib_db"][:, 1], 1),
            "gyro_rms": r(f["gyro_rms"], 2),
            "a_long": r(a_long, 3), "dv_smooth": r(np.r_[np.diff(res.v), np.nan], 3),
            "yaw_rate": r(mo["yaw_rate"], 4),
        },
        "line": {
            "stations": list(tr.stations), "s_m": [float(s) for s in tr.station_s],
            "t_stop": [int(t) for t in loc.t_stop], "leg_m": r(loc.leg_m, 0),
            "filt": r(loc.filt.reshape(len(loc.t_stop), -1), 4),  # [stop][station * 2 + (0 south, 1 north)]
            "smooth": r(loc.smooth.reshape(len(loc.t_stop), -1), 4),
        },
    }
    for k in meta["stops"]:
        for kk, v in k.items():
            if isinstance(v, float) and not np.isfinite(v):
                k[kk] = None
    (out / "meta.json").write_text(json.dumps(meta, separators=(",", ":")))
    print(rid, f"T={T}", meta["summary"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--params", default=None, help="all, for params/all.json (default: the fit without this recording, i.e. cross-validated)")
    args = ap.parse_args()
    ids = paths.labelled_ids()
    for rid in ids:
        export(rid, args.params or f"without-{rid}")
    paths.derived("viewer", "index.json").write_text(json.dumps(ids))


if __name__ == "__main__":
    main()
