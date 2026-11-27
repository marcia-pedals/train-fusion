"""Check the app's Swift port against this pipeline, from a replay dump.

In the simulator (see ios/README or docs/recorder.md): launch the app with
    -autoreplay <id> -replayspeed max -dump
and it writes Documents/replay_dump/<id>.jsonl. Then:

    python -m app.compare <path to the .jsonl> [...]

Prints, per recording: how closely each per-second feature matches (features/frames.py),
and how the app's online beliefs do against the labels and the map: speed error, and the
distance of every leg between door openings against the map distance.
"""

import json
import sys
from pathlib import Path

import numpy as np

from common import paths
from labels import load
from labels.frames import frame_labels


def main(files):
    for path in files:
        rid = Path(path).stem
        rows = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
        ref = json.loads(paths.derived("app", f"{rid}.ref.json").read_text())
        n = np.array([r["n"] for r in rows])
        assert (np.diff(n) == 1).all() and n[0] == 0, "seconds missing from the dump"
        T = min(len(rows), len(ref["rumble_db"]))
        print(f"## {rid}: {len(rows)} s from the app, {len(ref['rumble_db'])} s in the reference")

        def col(key, sub=None):
            return np.array([(r[key] if sub is None else r[key][sub]) for r in rows[:T]], float)

        def show(name, a, b, tol):
            d = a - np.asarray(b, float)[:T]
            print(f"   {name:22s} median |diff| {np.median(np.abs(d)):8.4f}   95 % {np.percentile(np.abs(d), 95):8.4f}   "
                  f"max {np.abs(d).max():8.3f}   {'ok' if np.percentile(np.abs(d), 95) <= tol else 'DIFFERENT'}")

        for key, tol in (("open_chime", 0.05), ("close_chime", 0.05), ("rumble_db", 0.3), ("vib_low", 0.3), ("vib_high", 0.3),
                         ("gyro_rms", 0.01)):
            show(key, col(key), ref[key], tol)
        show("acc_mean (x)", col("acc_mean", 0), np.array(ref["acc_mean"])[:, 0], 0.002)
        for i, b in enumerate(("150", "300", "475", "700")):
            show(f"tone_spec[{b}]", col("tone_spec", i), ref["tone_spec"][b], 1.5)
        # the accelerometer spectrum arrives ~3 s late: each line carries it for second accel_n
        late = {r["accel_n"]: r["accel_spec"] for r in rows if "accel_n" in r}
        ks = np.array(sorted(k for k in late if k < T))
        for i, b in enumerate(("100", "250", "500")):
            d = np.array([late[k][i] for k in ks]) - np.asarray(ref["accel_spec"][b])[ks]
            print(f"   accel_spec[{b}]        median |diff| {np.median(np.abs(d)):8.4f}   95 % {np.percentile(np.abs(d), 95):8.4f}   "
                  f"max {np.abs(d).max():8.3f}   {'ok' if np.percentile(np.abs(d), 95) <= 1.5 else 'DIFFERENT'}"
                  f"   (arrives {np.median([r['n'] - r['accel_n'] for r in rows if 'accel_n' in r]):.0f} s late)")
        if "baro_alt" in ref and "baro_alt" in rows[-1]:
            # the app can't wait for the barometer's next sample (~1.3 s apart), so it holds the last one
            show("baro_alt", np.array([r.get("baro_alt", np.nan) for r in rows[:T]]), ref["baro_alt"], 0.5)

        # beliefs against labels and the map
        lab = frame_labels(rid)
        v = col("v")
        m = lab.confident[:T] & np.isfinite(lab.v[:T])
        print(f"   online speed against labels: RMSE {np.sqrt(np.mean((v[m] - lab.v[:T][m]) ** 2)):.2f} m/s over {m.sum()} s; "
              f"accelerometer in use {np.mean(['a_long' in r for r in rows]):.0%} of the time")
        stops, s_of = load.stops(rid), load.station_s()
        stop_n = [rows[i]["n"] for i in range(1, len(rows)) if rows[i]["stops"] > rows[i - 1]["stops"]]
        legs = rows[-1]["legs"]
        truth_t = np.array([st.t_open if st.doors else np.inf for st in stops])  # the app hears openings only
        matched = [int(np.argmin(np.abs(truth_t - t))) if np.abs(truth_t - t).min() <= 6 else None for t in stop_n]
        print(f"   stops: app heard {len(stop_n)} at {stop_n}; labelled {len(stops)}; false {sum(x is None for x in matched)}")
        errs = []
        for k in range(len(legs)):
            a, b = matched[k], matched[k + 1]
            if a is None or b is None or b != a + 1:
                continue
            true = abs(s_of[stops[a].station] - s_of[stops[b].station])
            errs.append(legs[k] / true - 1)
            print(f"     {stops[a].station:18s} > {stops[b].station:18s} map {true:5.0f} m  app {legs[k]:5.0f} m ({errs[-1]:+6.1%})")
        if errs:
            print(f"   leg distance error: mean |{np.mean(np.abs(errs)):.1%}|  max |{np.max(np.abs(errs)):.1%}|  bias {np.mean(errs):+.1%}  ({len(errs)} legs)")
        # the station layer at each stop
        line = []
        for k, t in enumerate(stop_n):
            i = int(np.flatnonzero(n == t)[0])
            st = rows[i]["stations"]
            truth = stops[matched[k]].station if matched[k] is not None else "?"
            line.append(f"{k + 1}:{'-' if not st else ('OK' if st[0]['name'] == truth else 'no')}{'' if not st else f'({st[0][chr(112)]:.2f})'}")
        print("   station named at each stop (OK = right, with its probability):", " ".join(line))


if __name__ == "__main__":
    main(sys.argv[1:])
