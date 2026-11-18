"""The whole pipeline, end to end:

    features  ->  fit on the training recordings  ->  infer on the test recordings  ->  evaluate

    python -m pipeline                 # cross-validation: leave one recording out, each in turn
    python -m pipeline --ablate        # ... and with single measurements removed / alone
    python -m pipeline --final         # fit on everything, write params/all.json

Assumes labels exist for every recording (labels/README.md) and the track model is built
(maps/). Features are (re)computed when missing or with --features.
"""

from __future__ import annotations

import argparse
import json

import numpy as np

from common import paths
from common.recording import Recording
from features import frames
from labels.frames import frame_labels
from labels import load as label_load
from model import emissions, evaluate, fit, infer, stations


STATION_VARIANTS = {
    "distance only": (),
    "+ heading": ("heading",),
    "+ baro": ("baro",),
    "+ heading + baro": ("heading", "baro"),
    "+ yaw": ("yaw",),
    "+ yaw + heading": ("yaw", "heading"),
    "+ yaw + baro": ("yaw", "baro"),
    "+ yaw + heading + baro": stations.ALL,
}


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", action="store_true", help="recompute the features")
    ap.add_argument("--ablate", action="store_true")
    ap.add_argument("--final", action="store_true")
    args = ap.parse_args()
    ids = paths.recording_ids()
    F = load_features(ids, args.features)
    L = {rid: frame_labels(rid) for rid in ids}

    if args.final:
        p = fit.fit([F[i] for i in ids], [L[i] for i in ids])
        save_json(paths.PARAMS / "all.json", p)
        print("wrote", paths.PARAMS / "all.json")
        return

    variants = {"all measurements": emissions.ALL}
    if args.ablate:
        for m in emissions.ALL:
            if m != "chime":
                variants[f"without {m}"] = tuple(x for x in emissions.ALL if x != m)
        for m in ("tone", "accel", "wheel", "rumble", "vib"):
            variants[f"chime + {m} only"] = ("chime", m)
        variants["chime only"] = ("chime",)
    report = {}
    for test in ids:
        train = [i for i in ids if i != test]
        p = fit.fit([F[i] for i in train], [L[i] for i in train])
        save_json(paths.CV_PARAMS / f"without-{test}.json", p)
        print(f"\n## test {test}   (trained on {', '.join(train)})")
        for name, use in variants.items():
            res = infer.infer(p, F[test], use)
            e = evaluate.evaluate(test, res, L[test])
            report.setdefault(test, {})[name] = e
            print(evaluate.summary_line(name, e))
            if name == "all measurements":
                save_result(test, "cv", res)
                for l in e["legs"]:
                    print(f"    {l['leg']:36s} map {l['map_m']:5.0f} m  model {l['model_m']:5.0f} m"
                          f" ({l['model_m'] / l['map_m'] - 1:+6.1%})  online {l['filter_m']:5.0f} m"
                          f" ({l['filter_m'] / l['map_m'] - 1:+6.1%})")
                # second layer: which station, starting from "anywhere on the line, either way"
                stops = label_load.stops(test)
                print("  which station (belief at each stop from the stops so far), by what the station layer may use:")
                for name2, use2 in STATION_VARIANTS.items():
                    loc = stations.localise(res, F[test], use=use2)
                    truth = {j: st.station for j, t in enumerate(loc.t_stop) for st in stops if abs(st.t_open - t) <= evaluate.MATCH_S}
                    sc = stations.score(loc, truth)
                    report[test].setdefault("stations", {})[name2] = sc
                    lock = "never" if sc["right_from_stop"] is None else f"from stop {sc['right_from_stop']} on"
                    print(f"    {name2:28s} right at {sc['right']:2d} of {sc['stops']:2d} stops; always right {lock:16s}"
                          f" p(truth) at stops 1-4: {' '.join(f'{x:.2f}' for x in sc['p_truth'][:4])}")
    save_json(paths.derived("model", "report.json"), report)


if __name__ == "__main__":
    main()
