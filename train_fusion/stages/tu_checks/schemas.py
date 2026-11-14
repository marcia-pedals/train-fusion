"""Tables written by stage `tu_checks`."""
from train_fusion.common.schema_dsl import Table, col

H2_FAILURES = ["first two swapped", "other two swapped", "ends early", "other"]
LISTED_FAILURES = ["missing 1-10", "missing more", "never listed"]

TU_CHECKS = Table("tu_checks", "How one case fared against a hypothesis about 511's trip updates that doesn't always hold (see the stage's docstring): H1 a scheduled trip seen in its window, H2 one snapshot of a trip, listed a scheduled trip expected in some trip update.", [
    col("date", "str", "service date of the trip"),
    col("trip_id", "str", "GTFS trip_id"),
    col("check", "enum", "the hypothesis", values=["H1", "H2", "listed"]),
    col("outcome", "enum", "whether it held (H1, H2: a trip with no trip update when the check applies has no row; listed: one expected in none)",
        values=["ok", "failed"]),
    col("failure", "enum?", "how it failed: H2, how the stop list differs from a suffix of the schedule (two "
        "adjacent stops swapped, the first two listed or two others; cut short of the horizon; anything else); "
        "listed, how many expected trip updates the trip is missing from", values=H2_FAILURES + LISTED_FAILURES),
    col("dep0_epoch", "f64", "the trip's scheduled first departure (static GTFS)", "s"),
    col("at_epoch", "f64", "when: the start of the trip's window (H1), the snapshot's content time (H2), the first trip update the trip is missing from, else the start of its window (listed)", "s"),
    col("detail", "str", "what failed, in words (empty if nothing did)"),
])
