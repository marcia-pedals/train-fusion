"""The evaluation's output tables (model/evaluate.py, written by pipeline.py to
a run's folder, data/phone-sensor-fusion/eval/runs/<run>/<table>.parquet; the viewer reads them).

Declared with the repo's schema language (train_fusion/common/schema_dsl.py), which also
gives the viewer its TypeScript types:

    python -m model.schemas        # -> viewer/src/generated/tables.ts (not checked in)

Filtered = the forward pass, using only measurements up to the moment in question (what a
phone running live would know). Smoothed = given the whole ride. Every per-leg and per-ride
metric is computed on a ride the model was not fitted on (leave one ride out).
"""

import sys
from pathlib import Path

from common import paths

if str(paths.REPO) not in sys.path:
    sys.path.append(str(paths.REPO))
from train_fusion.common.schema_dsl import Table, col, typescript  # noqa: E402

PHONE = ["resting", "held", "handled"]

_RIDE_KEY = [
    col("ride", "str", "recording id of the ride left out (the test ride)"),
    col("ride_name", "str", "the ride's letter in the docs (labels/RIDES.md); empty if not in it yet"),
    col("variant", "str", "which measurements the first layer used: \"all measurements\", or an ablation (pipeline.py --ablate)"),
]

legs = Table("legs", "One row per labelled leg (consecutive labelled stops) of each test ride.", _RIDE_KEY + [
    col("leg", "i64", "1 for the leg out of the ride's first labelled stop, and so on"),
    col("from_station", "str", "labelled station the leg starts at"),
    col("to_station", "str", "labelled station the leg ends at"),
    col("phone", "enum", "the labelled phone state for most of the leg's moving window", values=PHONE),
    col("t_from_s", "f64", "recording time of the first stop's door opening (or, not in the recording, its earliest labelled time)", "s"),
    col("t_to_s", "f64", "recording time of the second stop's door opening", "s"),
    col("map_m", "f64", "map distance between the two stations", "m"),
    col("duration_s", "f64", "from the start of the first stop's door opening to the start of the second's", "s"),
    col("filt_dist_m", "f64", "filtered speed summed over the leg (door opening to door opening)", "m"),
    col("filt_dist_err", "f64",
        "relative error of the leg's distance from the filtered speeds (each second's speed given only the "
        "measurements up to it), door opening to door opening, against the map: model / map - 1"),
    col("filt_p_missed_stop", "f64",
        "P(the doors did not start opening anywhere in the leg's end stop's window), given the measurements up to the "
        "end of that window"),
    col("filt_p_false_stop", "f64?",
        "P(the doors started opening at least once while moving between the two stops, away from any door opening "
        "labelled there), given the measurements up to the end of that window; null if the window is empty"),
    col("live_dist_m", "f64",
        "speed smoothed over everything up to the end stop's door opening, summed over the leg: what the causal "
        "localiser is given for the leg", "m"),
    col("live_dist_err", "f64",
        "relative error of the leg's distance from the speeds smoothed over everything up to the end stop's door "
        "opening (what the causal localiser is given), against the map: model / map - 1"),
    col("smooth_dist_m", "f64", "smoothed speed summed over the leg", "m"),
    col("smooth_dist_err", "f64",
        "relative error of the leg's distance from the speeds smoothed over the whole ride, door opening to door "
        "opening, against the map: model / map - 1"),
    col("smooth_p_missed_stop", "f64",
        "P(the doors did not start opening anywhere in the leg's end stop's window), given the whole ride"),
    col("smooth_p_false_stop", "f64?",
        "P(the doors started opening at least once while moving between the two stops, away from any door opening "
        "labelled there), given the whole ride; null if the window is empty"),
])

rides = Table("rides", "One row per test ride.", _RIDE_KEY + [
    col("stops", "i64", "labelled stops"),
    col("legs", "i64", "labelled legs (rows in legs)"),
    col("moving_s", "f64", "from leaving the first labelled stop to the end of the last labelled stop", "s"),
    # the localiser run causally (stations.localise(causal=True)): at each door opening, on the
    # speeds smoothed over everything up to it
    col("loc_filt_time_to_fix_s", "f64?",
        "from leaving the first labelled stop until the causal localiser first gives the true last station and "
        "direction at least fix_p (meta.json); null if it never does", "s"),
    col("loc_filt_lost_s", "f64?",
        "after that fix, total time the same probability is below lost_p (meta.json), up to the end of the last "
        "labelled stop; null if never fixed", "s"),
    col("loc_filt_confident_wrong_s", "f64",
        "from leaving the first labelled stop to the end of the last, total time the causal localiser's most probable "
        "station and direction is above fix_p (meta.json) and wrong: what the app would show as a confident wrong "
        "place", "s"),
    col("loc_filt_openings", "i64", "door openings the causal localiser was given (onsets in the filtered modes)"),
    # the localiser's most likely path over the whole ride (stations.localise(), Localisation.path):
    # one explanation of every door opening at once
    col("loc_ml_stops_missing", "i64",
        "labelled stops where the localiser's most likely path has no stop within the stop's window"),
    col("loc_ml_stops_spurious", "i64",
        "stops in the localiser's most likely path where there was no labelled stop: outside every labelled stop's "
        "window (between the first and the last), or a second stop within a labelled stop's window"),
    col("loc_ml_openings", "i64", "door openings the localiser was given (onsets in the first layer's Viterbi path)"),
    # summaries of the ride's legs
    col("filt_dist_err_mean_abs", "f64?",
        "mean over the ride's legs of the relative error of each leg's distance from the filtered speeds (each second's "
        "speed given only the measurements up to it), against the map"),
    col("live_dist_err_mean_abs", "f64?",
        "mean over the ride's legs of the relative error of each leg's distance from the speeds smoothed over "
        "everything up to its end stop's door opening (what the causal localiser is given), against the map"),
    col("smooth_dist_err_mean_abs", "f64?",
        "mean over the ride's legs of the relative error of each leg's distance from the speeds smoothed over the "
        "whole ride, against the map"),
    col("filt_missed_stops", "f64",
        "expected number of the ride's stops (each leg's end stop) where the doors were not heard opening, from the "
        "filter: the sum over legs of P(no door opening in the end stop's window | measurements up to its end)"),
    col("filt_false_stops", "f64",
        "expected number of the ride's legs with a door opening while moving (a false stop), from the filter: the sum "
        "over legs of P(a door opening in the moving window | measurements up to its end)"),
    col("smooth_missed_stops", "f64",
        "expected number of the ride's stops (each leg's end stop) where the doors were not heard opening, given the "
        "whole ride: the sum over legs of P(no door opening in the end stop's window)"),
    col("smooth_false_stops", "f64",
        "expected number of the ride's legs with a door opening while moving (a false stop), given the whole ride: the "
        "sum over legs of P(a door opening in the moving window)"),
])

TABLES = [legs, rides]


def path(table: Table, run: Path) -> Path:
    """Where a run (pipeline.py: a folder under paths.RUNS) keeps the table."""
    return run / f"{table.name}.parquet"


def _ts_columns(tables) -> str:
    """Each table's columns at runtime: name, description, unit, enum values."""
    import json
    out = {t.name: {"desc": t.desc, "columns": [{"name": c.name, "desc": c.desc, "unit": c.unit,
                                                    "nullable": c.nullable, "values": list(c.values) if c.values else None}
                                                   for c in t.cols]} for t in tables}
    return f"export const TABLES = {json.dumps(out, indent=2)} as const;\n"


if __name__ == "__main__":
    out = paths.HERE / "viewer" / "src" / "generated" / "tables.ts"
    out.parent.mkdir(parents=True, exist_ok=True)
    ts = typescript(TABLES).replace("train_fusion/stages/*/schemas.py by `just types`",
                                    "phone-sensor-fusion/model/schemas.py by `python -m model.schemas`")
    out.write_text(ts + "\n" + _ts_columns(TABLES))
    print("wrote", out.relative_to(paths.REPO))
