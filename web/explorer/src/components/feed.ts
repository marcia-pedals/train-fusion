// Helpers shared by the static-feed pages.
import * as d3 from "npm:d3";

/** Routes in a fixed order, each with a fixed colour (validated categorical palette, slots 1-4). */
export const ROUTES = ["Local Weekday", "Local Weekend", "Limited", "Express"];
export const ROUTE_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"];

export const dirName = (d: number) => (d === 0 ? "northbound" : "southbound");

/** "Tamien Caltrain Station" -> "Tamien". */
export const shortStationName = (name: string) => name.replace(/ Caltrain( Station)?$/, "").replace(/ Station$/, "");

/** Counts trips, treating trips with the same first-departure and last-arrival times as one. */
export function distinctTripCounter(stopsOf: Map<string, {sched_s: number}[]>) {
  return (ts: {trip_id: string}[]) => new Set(ts.map((t) => {
    const st = stopsOf.get(t.trip_id)!;
    return `${d3.min(st, (r) => r.sched_s)}-${d3.max(st, (r) => r.sched_s)}`;
  })).size;
}

/** Distance from San Francisco along the line (km) for every station on the shape that has
 *  the most stations and runs San Francisco <-> Tamien; also returns that shape's rows. */
export function routeKm(shapeStations: {shape_id: string; station_id: string; s_m: number}[]) {
  const byShape = d3.group(shapeStations, (r) => r.shape_id);
  const refShape = d3.greatest([...byShape.entries()].filter(([, rs]) =>
    rs.some((r) => r.station_id === "san_francisco") && rs.some((r) => r.station_id === "tamien")), ([, rs]) => rs.length)![1];
  const sfS = refShape.find((r) => r.station_id === "san_francisco")!.s_m;
  return {refShape, kmFromSF: new Map(refShape.map((r) => [r.station_id, Math.abs(r.s_m - sfS) / 1000]))};
}
