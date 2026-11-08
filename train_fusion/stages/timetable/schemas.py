"""Tables written by stage `timetable`."""
from train_fusion.common.schema_dsl import Table, col

TIMETABLE = Table("timetable", "Scheduled departure of one served stop of a trip, as an interval holding the true timetable time: [t, t + 6] from 511's pre-departure trip updates, else [S, S + 60] from static GTFS.", [
    col("trip_id", "str", "GTFS trip_id"),
    col("stop_idx", "i64", "0-based index among the trip's served stops"),
    col("stop_id", "str", "platform stop_id"),
    col("station_id", "str", "parent station id"),
    col("static_s", "i64", "static GTFS time (whole minutes, truncated)", "s"),
    col("dep_lo_s", "f64", "earliest the true scheduled departure can be, after the service-day origin", "s"),
    col("dep_hi_s", "f64", "latest the true scheduled departure can be, after the service-day origin", "s"),
    col("source", "enum", "static GTFS only, or 511's trip updates", values=["static", "tu"]),
])
