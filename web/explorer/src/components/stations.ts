// Short station codes for compact labels. The GTFS feed has none (stop_code repeats the
// station id), so these are our own; keyed by parent station id.
export const STATION_CODES: Record<string, string> = {
  san_francisco: "SF", "22nd_street": "22ND", bayshore: "BAY", south_sf: "SSF", san_bruno: "SBR",
  place_MLBR: "MLB", broadway: "BWY", burlingame: "BUR", san_mateo: "SMT", hayward_park: "HPK",
  hillsdale: "HIL", belmont: "BEL", san_carlos: "SCS", redwood_city: "RWC", menlo_park: "MPK",
  palo_alto: "PAL", stanford: "STF", california_ave: "CAL", san_antonio: "SAN", mountain_view: "MV",
  sunnyvale: "SUN", lawrence: "LAW", santa_clara: "SCL", college_park: "CLP", sj_diridon: "SJ",
  tamien: "TAM",
};

/** The code for a station id, or the id itself if it has none. */
export const stationCode = (id: string) => STATION_CODES[id] ?? id;
