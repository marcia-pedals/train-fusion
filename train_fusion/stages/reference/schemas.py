"""Tables written by stage `reference`."""
from train_fusion.common.schema_dsl import Table, col

REF_SEGMENTS = Table("ref_segments", "One segment: from a served stop to the next served stop.", [
    col("segment_id", "str", "'<from_station>><to_station>'"),
    col("direction_id", "i64", "GTFS direction"),
    col("from_station", "str", "station the train starts moving from"),
    col("to_station", "str", "station the train stops at"),
    col("length_m", "f64", "median stop-to-stop distance over the trips that run it", "m"),
    col("n_trips", "i64", "trips in the static feed that run this segment"),
    col("run_s", "f64", "reference running time: start moving to stop moving", "s"),
    col("v_max_mps", "f64", "highest speed reached", "m/s"),
])


REF_KNOTS = Table("ref_knots", "One knot of a segment's reference curve; time is linear in between.", [
    col("segment_id", "str", "'<from_station>><to_station>'"),
    col("knot", "i64", "0 = starts moving at from_station, last = stops at to_station"),
    col("p_m", "f64", "distance from from_station", "m"),
    col("t_s", "f64", "time since starting to move", "s"),
    col("v_mps", "f64", "speed at the knot (for plotting; not used for interpolation)", "m/s"),
])


REF_LIMITS = Table("ref_limits", "Track speed limit along a segment (track/speed_limits.toml), piecewise constant.", [
    col("segment_id", "str", "'<from_station>><to_station>'"),
    col("p0_m", "f64", "start, from from_station", "m"),
    col("p1_m", "f64", "end, from from_station", "m"),
    col("limit_mps", "f64", "speed limit", "m/s"),
])


REF_CRUISE = Table("ref_cruise", "A stretch of a segment where the reference runs at constant speed.", [
    col("segment_id", "str", "'<from_station>><to_station>'"),
    col("p0_m", "f64", "start, from from_station", "m"),
    col("p1_m", "f64", "end, from from_station", "m"),
])


REF_FIT = Table("ref_fit", "Timetable fit: one segment of one trip pattern (all its trips share these scheduled times).", [
    col("pattern_id", "str", "trip pattern"),
    col("seg_idx", "i64", "segment index along the pattern (0 = first stop to second)"),
    col("segment_id", "str", "'<from_station>><to_station>'"),
    col("final", "bool", "ends at the pattern's final stop (scheduled arrival, so no dwell term)"),
    col("n_trips", "i64", "trips of this pattern"),
    col("narrowed", "bool", "the pattern's times were narrowed by 511's trip updates (else whole static minutes)"),
    col("phys_s", "f64", "physical minimum run time R", "s"),
    col("model_s", "f64", "(1 + p) R + W (no W if final)", "s"),
    col("pad_s", "f64", "padding ε: scheduled time beyond model_s", "s"),
    col("sched_s", "f64", "the pattern's scheduled time over this segment as fitted: model_s + pad_s", "s"),
])


REF_TRIPS = Table("ref_trips", "One knot of a trip's chained reference trajectory on one date.", [
    col("date", "str", "service date"),
    col("trip_id", "str", "GTFS trip_id"),
    col("i", "i64", "knot order along the trip"),
    col("s_m", "f64", "position on the trip's shape", "m"),
    col("tau_s", "f64", "reference time since starting to move at the origin", "s"),
    col("t_epoch", "f64", "tau anchored at the origin's scheduled departure", "s"),
    col("phase", "enum", "what happens from this knot to the next", values=["run", "dwell", "end"]),
    col("run_idx", "i64", "segment index along the trip (0 = origin to second stop)"),
])


REF_FIXES = Table("ref_fixes", "One VP fix of an active trip, measured against the trip's reference.", [
    col("date", "str", "service date"),
    col("trip_id", "str", "GTFS trip_id"),
    col("t_epoch", "f64", "content time", "s"),
    col("content_exact", "bool", "t_epoch is the exact content time"),
    col("repeat", "bool", "same coordinate as the trip's previous fix (standing, or a stale position)"),
    col("cruise", "bool", "the reference runs at constant speed for 300 m either side (see ref_pairs)"),
    col("s_m", "f64", "position on the trip's shape", "m"),
    col("zone", "enum", "parked at / near the origin, between the end stops, or at / past the final stop",
        values=["origin", "line", "final"]),
    col("run_idx", "i64", "segment the fix lies in (by position)"),
    col("segment_id", "str", "'<from_station>><to_station>'"),
    col("p_m", "f64", "distance from the segment's start stop", "m"),
    col("tau_s", "f64", "reference time to reach s_m (earliest, if s_m is a stop)", "s"),
    col("seg_tau_s", "f64", "reference time to reach s_m since starting to move at the segment's start stop", "s"),
    col("delay_s", "f64", "t - (scheduled origin departure + tau)", "s"),
    col("sched_delay_s", "f64", "t - scheduled time at s_m (straight line between scheduled stops)", "s"),
])


REF_PAIRS = Table("ref_pairs", "Two consecutive usable cruise fixes of a trip (any distance apart): does the train beat the reference between them? Independent of where exactly trains stop.", [
    col("date", "str", "service date"),
    col("trip_id", "str", "GTFS trip_id"),
    col("t0_epoch", "f64", "first fix time", "s"),
    col("t1_epoch", "f64", "second fix time", "s"),
    col("s0_m", "f64", "first fix position", "m"),
    col("s1_m", "f64", "second fix position", "m"),
    col("run0", "i64", "segment of the first fix"),
    col("run1", "i64", "segment of the second fix"),
    col("stops", "i64", "served stops passed between the two fixes (run1 - run0)"),
    col("segment_id", "str?", "segment if both fixes are in the same one"),
    col("dt_s", "f64", "actual elapsed time", "s"),
    col("dtau_s", "f64", "reference time between the two positions", "s"),
    col("excess_s", "f64", "dt - dtau: time lost against the reference (< 0 means faster than the 'minimum')", "s"),
])
