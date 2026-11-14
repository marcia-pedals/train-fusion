"""Tables written by stage `tu_checks` (see the stage's docstring for the checks)."""
from train_fusion.common.schema_dsl import Table, col

TU_H1 = Table("tu_h1", "One snapshot of a trip, from 15 min before its scheduled departure: whether it lists the rest of the trip (H1).", [
    col("date", "str", "service date of the trip"),
    col("trip_id", "str", "GTFS trip_id"),
    col("dep0_epoch", "f64", "the trip's scheduled first departure (static GTFS)", "s"),
    col("t_epoch", "f64", "the snapshot's content time", "s"),
    col("outcome", "enum", "ok, or how the stop list isn't the rest of the trip: two adjacent stops swapped (the "
        "first two listed, or two others), cut short of the horizon, not starting at the origin by 10 min "
        "before departure, or anything else",
        values=["ok", "first two swapped", "other two swapped", "ends early", "not from origin", "other"]),
    col("detail", "str", "what failed, in words (empty if nothing did)"),
])

TU_LISTED = Table("tu_listed", "One scheduled trip expected in at least one trip update: how many of those it's missing from.", [
    col("date", "str", "service date of the trip"),
    col("trip_id", "str", "GTFS trip_id"),
    col("dep0_epoch", "f64", "the trip's scheduled first departure (static GTFS)", "s"),
    col("n_expected", "i64", "trip updates it's expected in"),
    col("n_missing", "i64", "of those, how many it's missing from"),
    col("first_missing_epoch", "f64?", "content time of the first trip update it's missing from", "s"),
    col("outcome", "enum", "in all of them, or missing from 1-10, from more but not all, or from all",
        values=["in all", "missing 1-10", "missing more", "never listed"]),
    col("detail", "str", "when it was missing, in words (empty if never)"),
])

TU_KEPT = Table("tu_kept", "One trip-update entity (each content once, at its first appearance; South County left out): kept for the checks, or discarded.", [
    col("date", "str", "service date of the snapshot"),
    col("trip_id", "str", "GTFS trip_id"),
    col("dep0_epoch", "f64?", "scheduled first departure of the trip it's assigned to (null: not in the static schedule)", "s"),
    col("t_epoch", "f64", "the snapshot's content time", "s"),
    col("outcome", "enum", "kept, or discarded as a ghost (all times over 1 h old) or as a trip not in the static schedule",
        values=["kept", "ghost", "not in schedule"]),
    col("detail", "str", "why it's discarded, in words (empty if kept)"),
])
