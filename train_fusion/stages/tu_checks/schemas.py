"""Tables written by stage `tu_checks`."""
from train_fusion.common.schema_dsl import Table, col

H2_FAILURES = ["first two swapped", "other two swapped", "ends early", "not from origin", "other"]
LISTED_FAILURES = ["missing 1-10", "missing more", "never listed"]
KEPT_FAILURES = ["ghost", "not in schedule"]

TU_CHECKS = Table("tu_checks", "How one case fared against a hypothesis about 511's trip updates that doesn't always hold (see the stage's docstring): H2 one snapshot of a trip, listed a scheduled trip expected in some trip update, kept one trip-update entity (each content once).", [
    col("date", "str", "service date of the trip (kept: of the snapshot)"),
    col("trip_id", "str", "GTFS trip_id"),
    col("check", "enum", "the hypothesis", values=["H2", "listed", "kept"]),
    col("outcome", "enum", "whether it held (H2: a trip with no trip update when the check applies has no row; listed: one expected in none)",
        values=["ok", "failed"]),
    col("failure", "enum?", "how it failed: H2, how the stop list differs from a suffix of the schedule (two "
        "adjacent stops swapped, the first two listed or two others; cut short of the horizon; not starting at "
        "the origin by dep - 10 min; anything else); "
        "listed, how many expected trip updates the trip is missing from; kept, why the entity is discarded",
        values=H2_FAILURES + LISTED_FAILURES + KEPT_FAILURES),
    col("dep0_epoch", "f64?", "the trip's scheduled first departure (static GTFS; null for a trip not in it)", "s"),
    col("at_epoch", "f64", "when: the snapshot's content time (H2), the first trip update the trip is missing from, else the start of its window (listed), the snapshot's content time (kept)", "s"),
    col("detail", "str", "what failed, in words (empty if nothing did)"),
])
