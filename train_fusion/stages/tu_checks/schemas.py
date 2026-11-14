"""Tables written by stage `tu_checks`."""
from train_fusion.common.schema_dsl import Table, col

TU_CHECKS = Table("tu_checks", "How one scheduled trip fared against a hypothesis about 511's trip updates that doesn't always hold (H1, H2; see the stage's docstring).", [
    col("date", "str", "service date of the trip"),
    col("trip_id", "str", "GTFS trip_id"),
    col("check", "enum", "the hypothesis", values=["H1", "H2"]),
    col("outcome", "enum", "ok; failed; or not judged: the trip has no trip update when the check applies "
        "(not seen), which is before or after the archive (outside archive)",
        values=["ok", "failed", "not seen", "outside archive"]),
    col("dep0_epoch", "f64", "the trip's scheduled first departure (static GTFS)", "s"),
    col("at_epoch", "f64", "when: the first failure (H2), else when the check starts to apply", "s"),
    col("n_failures", "i64", "how many times it failed: H1 0 or 1, H2 non-monotone changes of the stop list"),
    col("detail", "str", "what failed, in words (empty if nothing did)"),
])
