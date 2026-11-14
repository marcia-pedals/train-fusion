"""Tables written by stage `static`."""
from train_fusion.common.schema_dsl import Table, col

TRIPS = Table("trips", "One trip in the static GTFS feed (South County excluded).", [
    col("trip_id", "str", "GTFS trip_id (== vehicle id in the RT feeds)"),
    col("route_id", "str", "service pattern, e.g. Local Weekend, Limited, Express"),
    col("service_id", "str", "GTFS service_id"),
    col("direction_id", "i64", "GTFS direction: 0 = northbound (to San Francisco), 1 = southbound"),
    col("shape_id", "str", "GTFS shape_id"),
    col("pattern_id", "str", "trip pattern: same direction, served stations and static times relative to the first departure"),
])


TRIP_STOPS = Table("trip_stops", "One served stop of a trip (stop_times.txt lists only served stops).", [
    col("trip_id", "str", "GTFS trip_id"),
    col("stop_seq", "i64", "GTFS stop_sequence"),
    col("stop_idx", "i64", "0-based index among the trip's served stops"),
    col("stop_id", "str", "platform stop_id"),
    col("station_id", "str", "parent station id"),
    col("sched_s", "i64", "scheduled time after the GTFS service-day origin (static arrival == departure)", "s"),
    col("s_m", "f64", "distance along the trip's shape (shape_dist_traveled)", "m"),
])


SHAPES = Table("shapes", "One point of a shape polyline.", [
    col("shape_id", "str", "GTFS shape_id"),
    col("pt_seq", "i64", "point order"),
    col("lat", "f64", "latitude", "deg"),
    col("lon", "f64", "longitude", "deg"),
    col("s_m", "f64", "shape_dist_traveled", "m"),
])


STATIONS = Table("stations", "A parent station.", [
    col("station_id", "str", "parent station id"),
    col("name", "str", "station name"),
    col("lat", "f64", "latitude", "deg"),
    col("lon", "f64", "longitude", "deg"),
])


SHAPE_STATIONS = Table("shape_stations", "A station projected onto a shape (only within 100 m of it).", [
    col("shape_id", "str", "GTFS shape_id"),
    col("station_id", "str", "parent station id"),
    col("s_m", "f64", "distance along the shape of the station's projection", "m"),
    col("offset_m", "f64", "distance from the station point to the shape", "m"),
    col("served_by_any", "bool", "some trip on this shape stops here"),
])


PLATFORMS = Table("platforms", "A platform: a stop with a parent station (stops.txt).", [
    col("stop_id", "str", "platform stop_id"),
    col("station_id", "str", "parent station id"),
    col("lat", "f64", "latitude", "deg"),
    col("lon", "f64", "longitude", "deg"),
])

CALENDAR = Table("calendar", "A service's regular weekly days and date range (calendar.txt).", [
    col("service_id", "str", "GTFS service_id"),
    col("monday", "bool", "runs on Mondays"),
    col("tuesday", "bool", "runs on Tuesdays"),
    col("wednesday", "bool", "runs on Wednesdays"),
    col("thursday", "bool", "runs on Thursdays"),
    col("friday", "bool", "runs on Fridays"),
    col("saturday", "bool", "runs on Saturdays"),
    col("sunday", "bool", "runs on Sundays"),
    col("start_date", "str", "first date, YYYY-MM-DD"),
    col("end_date", "str", "last date, YYYY-MM-DD"),
])

CALENDAR_DATES = Table("calendar_dates", "A date a service is added on or removed from (calendar_dates.txt).", [
    col("service_id", "str", "GTFS service_id"),
    col("date", "str", "YYYY-MM-DD"),
    col("added", "bool", "the service runs this date (else it doesn't)"),
])


EXCLUDED_TRIPS = Table("excluded_trips", "One trip in the static GTFS feed of a route the project leaves out (South County: the corridor is San Francisco <-> Tamien), so decode can drop its RT entities.", [
    col("trip_id", "str", "GTFS trip_id"),
    col("route_id", "str", "its route_id"),
])


FEED_VERSIONS = Table("feed_versions", "The static GTFS feed that applies to one service date being built (the newest zip fetched on or before it).", [
    col("date", "str", "service date"),
    col("feed_version", "str", "the feed's version: data/derived/static/<feed_version>/ holds its tables"),
    col("zip", "str", "the static GTFS zip it was built from"),
])
