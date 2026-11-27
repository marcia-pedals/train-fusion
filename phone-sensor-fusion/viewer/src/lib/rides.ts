// How a ride is named on screen: its date ("Sat 1 Mar") and where it ran from and to
// ("SFC→TAM"). A recording id is the ride's start time in UTC (e.g. 20250301T120000Z…).
import {stationCode} from "./stations";

export const TIME_ZONE = "America/Los_Angeles";

export function rideStart(id: string): Date | null {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z/.exec(id);
  return m ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6])) : null;
}

const dateFmt = new Intl.DateTimeFormat("en-GB", {timeZone: TIME_ZONE, weekday: "short", day: "numeric", month: "short"});

export interface RideInfo {
  date: string;  // "Sat 1 Mar"
  endpoints: string;  // "SFC→TAM"
}

/** Ride id -> how to show it, its endpoints being its first and last labelled stations. */
export function rideInfo(legs: {ride: string; from_station: string; to_station: string}[]): Map<string, RideInfo> {
  const ends = new Map<string, {from: string; to: string}>();
  for (const l of legs) {
    const e = ends.get(l.ride);
    if (!e) ends.set(l.ride, {from: l.from_station, to: l.to_station});
    else e.to = l.to_station;
  }
  const out = new Map<string, RideInfo>();
  for (const [id, e] of ends) {
    const t = rideStart(id);
    out.set(id, {
      date: t ? dateFmt.format(t).replace(",", "") : id,
      endpoints: `${stationCode(e.from)}→${stationCode(e.to)}`,
    });
  }
  return out;
}
