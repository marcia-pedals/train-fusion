// Three-letter codes for the line's stations (as named in maps/data/stations.csv), for
// compact tables. Our own: the GTFS feed has none.
export const STATION_CODES: Record<string, string> = {
  "San Francisco 4th & King Street": "SFC", "22nd Street": "22N", "Bayshore": "BAY",
  "South San Francisco": "SSF", "San Bruno": "SBR", "Millbrae": "MLB", "Broadway": "BWY",
  "Burlingame": "BUR", "San Mateo": "SMT", "Hayward Park": "HPK", "Hillsdale": "HIL",
  "Belmont": "BEL", "San Carlos": "SCS", "Redwood City": "RWC", "Menlo Park": "MPK",
  "Palo Alto": "PAL", "Stanford": "STF", "California Avenue": "CAL", "San Antonio": "SAN",
  "Mountain View": "MVW", "Sunnyvale": "SUN", "Lawrence": "LAW", "Santa Clara": "SCL",
  "College Park": "CLP", "San Jose Diridon": "SJD", "Tamien": "TAM",
};

/** The code for a station name, or the name itself if it has none. */
export const stationCode = (name: string) => STATION_CODES[name] ?? name;
