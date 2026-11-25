"""The whole pipeline, end to end:

    features  ->  fit on the training recordings  ->  infer on the test recording  ->  evaluate

    python -m pipeline                 # cross-validation: leave one recording out, each in turn
    python -m pipeline --ablate        # ... and with single measurements removed / alone
    python -m pipeline --final         # fit on everything, write params/all.json

Uses the recordings that have labels (labels/README.md) and leaves the rest out; assumes
the track model is built (maps/). Features are (re)computed when missing or with --features.

The evaluation (model/evaluate.py) goes to data/phone-sensor-fusion/eval/: legs.parquet and
rides.parquet (model/schemas.py), and meta.json (its thresholds); what the viewer's ride page
shows to data/phone-sensor-fusion/rides/<id>/ (model/ride_view.py).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import numpy as np
import polars as pl

from common import paths
from common.recording import Recording
from features import frames
from labels import load as label_load
from labels.frames import frame_labels
from model import emissions, evaluate, fit, infer, ride_view, schemas


class _Enc(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
        return super().default(o)


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, cls=_Enc, indent=1))


def load_features(ids, recompute=False):
    out = {}
    for rid in ids:
        path = paths.derived("features", f"{rid}.frames.npz")
        if recompute or not path.exists():
            np.savez_compressed(path, **frames.extract(Recording(rid)))
        out[rid] = frames.load(rid)
    return out


def save_result(rid, name, res):
    np.savez_compressed(paths.derived("model", f"{rid}.{name}.npz"), v=res.v, v_sd=res.v_sd, v_filt=res.v_filt,
                        mode_p=res.mode_p, path_mode=res.path_mode, path_v=res.path_v, phone=res.phone)


def variants(ablate: bool) -> dict[str, tuple]:
    out = {"all measurements": emissions.ALL}
    if ablate:
        for m in emissions.ALL:
            if m != "chime":
                out[f"without {m}"] = tuple(x for x in emissions.ALL if x != m)
        for m in ("tone", "accel", "wheel", "rumble", "vib"):
            out[f"chime + {m} only"] = ("chime", m)
        out["chime only"] = ("chime",)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", action="store_true", help="recompute the features")
    ap.add_argument("--ablate", action="store_true")
    ap.add_argument("--final", action="store_true")
    args = ap.parse_args()
    ids = paths.labelled_ids()
    if waiting := sorted(set(paths.recording_ids()) - set(ids)):
        print(f"leaving out {len(waiting)} recording(s) not labelled yet: {', '.join(waiting)}")
    F = load_features(ids, args.features)
    L = {rid: frame_labels(rid) for rid in ids}

    if args.final:
        p = fit.fit([F[i] for i in ids], [L[i] for i in ids])
        save_json(paths.PARAMS / "all.json", p)
        print("wrote", paths.PARAMS / "all.json")
        return

    names = label_load.ride_names()
    leg_rows, ride_rows = [], []
    for test in ids:
        train = [i for i in ids if i != test]
        p = fit.fit([F[i] for i in train], [L[i] for i in train])
        save_json(paths.CV_PARAMS / f"without-{test}.json", p)
        stops = label_load.stops(test)
        print(f"\n## test {names.get(test, test)}   (trained on {len(train)} others)")
        for name, use in variants(args.ablate).items():
            res = infer.infer(p, F[test], use)
            if name == "all measurements":
                save_result(test, "cv", res)
            key = {"ride": test, "ride_name": names.get(test, ""), "variant": name}
            legs = evaluate.legs(res, L[test], stops, key)
            trace = {}
            r = evaluate.ride(res, F[test], L[test], stops, legs, key, trace)
            if name == "all measurements":
                ride_view.export(test, res, F[test], L[test], stops, trace)
            leg_rows += legs
            ride_rows.append(r)
            print(evaluate.summary_line(r))
    for table, rows in ((schemas.legs, leg_rows), (schemas.rides, ride_rows)):
        df = pl.DataFrame(rows, schema=table.polars_schema(), strict=False)
        table.write(df, schemas.path(table))
        print("wrote", schemas.path(table))
    save_json(paths.derived("eval", "meta.json"),
              {**evaluate.CONSTANTS, "ablate": args.ablate, "written": datetime.now().astimezone().isoformat(timespec="seconds")})


if __name__ == "__main__":
    main()
