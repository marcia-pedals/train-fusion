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


FETCHES = Table("fetches", "One logged request to an RT feed (or an unlogged .pb file).", [
    col("feed", "enum", "which feed", values=["tripupdates", "vehiclepositions"]),
    col("request_epoch", "f64", "when the request was sent", "s"),
    col("status", "i64?", "HTTP status (null: network error / timeout)"),
    col("role", "str", "normal, parity_probe or unknown"),
    col("file", "str?", "raw snapshot file"),
    col("ok", "bool", "HTTP 200 and the body parsed"),
    col("stamp_epoch", "i64?", "entity timestamp", "s"),
    col("content_sha256", "str?", "digest of the content with timestamps stripped"),
    col("n_entities", "i64", "entities in the snapshot"),
    col("content_epoch", "f64?", "content time", "s"),
    col("content_exact", "bool", "content_epoch is the exact content time"),
    col("duplicate", "bool", "content repeats an earlier fetch of the same feed (dropped)"),
])
