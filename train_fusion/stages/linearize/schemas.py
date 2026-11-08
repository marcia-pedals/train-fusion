"""Tables written by stage `linearize`."""
from train_fusion.common.schema_dsl import Table, col

VP_LIN = Table("vp_lin", "One vehicle-position fix placed on the line.", [
    col("trip_id", "str", "GTFS trip_id"),
    col("t_epoch", "f64", "content time", "s"),
    col("content_exact", "bool", "t_epoch is the exact content time"),
    col("lat", "f64", "latitude", "deg"),
    col("lon", "f64", "longitude", "deg"),
    col("s_m", "f64", "distance along the trip's shape", "m"),
    col("offset_m", "f64", "distance from the shape", "m"),
])
