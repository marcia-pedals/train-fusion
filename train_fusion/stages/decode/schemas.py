"""Tables written by stage `decode`."""
from train_fusion.common.schema_dsl import Table, col

VP = Table("vp", "One vehicle-position entity (deduplicated by content).", [
    col("trip_id", "str", "GTFS trip_id"),
    col("route_id", "str", "route_id as given by the feed"),
    col("direction_id", "i64", "direction as given by the feed"),
    col("t_epoch", "f64", "content time", "s"),
    col("stamp_epoch", "i64", "raw entity timestamp", "s"),
    col("content_exact", "bool", "t_epoch is the exact content time"),
    col("lat", "f64", "latitude", "deg"),
    col("lon", "f64", "longitude", "deg"),
    col("file", "str", "raw snapshot file"),
])


TU = Table("tu", "One stop_time_update of a trip-update entity (deduplicated by content).", [
    col("trip_id", "str", "GTFS trip_id"),
    col("t_epoch", "f64", "content time", "s"),
    col("stamp_epoch", "i64", "raw entity timestamp", "s"),
    col("content_exact", "bool", "t_epoch is the exact content time"),
    col("stu_idx", "i64", "position in the entity's stop_time_update list"),
    col("stop_id", "str", "platform stop_id"),
    col("arr_epoch", "i64?", "predicted arrival", "s"),
    col("dep_epoch", "i64?", "predicted departure", "s"),
    col("file", "str", "raw snapshot file"),
])
