// Charts comparing a reference (segment curves + dwells) with actual runs.
// Colour roles are fixed on every chart: reference = blue, observed = orange,
// schedule = neutral grey. Rows are the generated table types (just types).
import * as Plot from "npm:@observablehq/plot";
import * as d3 from "npm:d3";
import type {RefCruise, RefFit, RefFixes, RefKnots, RefLimits, RefPairs, RefSegments, RefTrips, Timetable} from "../../../packages/types/tables.ts";

export const REF = "#2a78d6";
export const OBS = "#eb6834";
export const SCHED = "var(--theme-foreground-muted)";
export const LINE_SPEED_MPS = 35.3; // 79 mph, Caltrain's maximum

/** Arrow table -> plain objects. Int64 columns arrive as BigInt; make them numbers. */
export function rows(table: Iterable<any>): any[] {
  return Array.from(table, (r) => {
    const o = r.toJSON();
    for (const k in o) if (typeof o[k] === "bigint") o[k] = Number(o[k]);
    return o;
  });
}

const date = (t: number) => new Date(t * 1000);
export const shortName = (n: string) => n.replace(/ Caltrain( Station)?/, "").replace(/ Station$/, "");
const key = (r: {date: string; trip_id: string}) => `${r.date} ${r.trip_id}`;

// ------------------------------------------------------------------ one segment
export type Align = "fit" | "schedule";

/** One traversal of a segment = one trip's fixes in it on one date, put on the
 *  segment's clock: "fit" shifts each traversal so its median (t - reference time) is
 *  0; "schedule" puts 0 at the trip's scheduled departure from the start stop. */
export function traversals(fixes: RefFixes[], schedDep: Map<string, number>, align: Align, length_m: number) {
  const out: (RefFixes & {x: number})[] = [];
  for (const [, g] of d3.group(fixes, key)) {
    let t0: number | undefined;
    if (align === "schedule") t0 = schedDep.get(key(g[0]));
    else {
      const moving = g.filter((r) => !r.repeat && r.p_m > 0.1 * length_m && r.p_m < 0.9 * length_m);
      t0 = d3.median(moving.length ? moving : g, (r) => r.t_epoch - r.seg_tau_s);
    }
    if (t0 === undefined) continue;
    for (const r of g) out.push({...r, x: r.t_epoch - t0});
  }
  return out;
}

export function segmentCurve(knots: RefKnots[], trav: (RefFixes & {x: number})[], seg: RefSegments,
                             {width, align, height = 420}: {width: number; align: Align; height?: number}) {
  const xs = trav.map((r) => r.x).concat(knots.map((k) => k.t_s));
  return Plot.plot({
    width, height,
    x: {label: align === "fit" ? "seconds since starting to move (each run aligned to the curve)"
                               : "seconds since the scheduled departure from the start stop",
        domain: [Math.max(-120, d3.min(xs) ?? 0), Math.min(seg.run_s + 400, d3.max(xs) ?? 0)], grid: true},
    y: {label: "metres from the start stop", grid: true},
    marks: [
      Plot.ruleY([0, seg.length_m], {stroke: "currentColor", strokeOpacity: 0.3}),
      Plot.line(trav, {x: "x", y: "p_m", z: key, stroke: OBS, strokeOpacity: 0.35, strokeWidth: 1}),
      Plot.dot(trav, {x: "x", y: "p_m", r: 3, stroke: OBS, fill: (r) => (r.repeat ? "none" : OBS),
        strokeOpacity: (r) => (r.cruise ? 1 : 0.35), fillOpacity: (r) => (r.cruise ? 1 : 0.35),
        channels: {trip: "trip_id", date: "date"},
        tip: {format: {x: (v: number) => `${v.toFixed(0)} s`, y: (v: number) => `${v.toFixed(0)} m`}}}),
      Plot.line(knots, {x: "t_s", y: "p_m", stroke: REF, strokeWidth: 2.5}),
      Plot.dot(knots, {x: "t_s", y: "p_m", r: 2, fill: REF}),
    ],
  });
}

/** Observed average speed between consecutive fixes (a horizontal stroke from p0 to p1)
 *  against the reference's instantaneous speed and the track speed limit (dashed). */
export interface Step { date: string; trip_id: string; s0_m: number; s1_m: number; p0: number; p1: number; dt_s: number; excess_s: number }

export function speedProfile(knots: RefKnots[], pairs: Step[], seg: RefSegments, limits: RefLimits[],
                             cruise: RefCruise[], margin: number, {width, height = 300}: {width: number; height?: number}) {
  const zones = cruise.filter((c) => c.p1_m - c.p0_m > 2 * margin).map((c) => ({x1: c.p0_m + margin, x2: c.p1_m - margin}));
  const pr = pairs.map((r) => ({...r, v: (r.s1_m - r.s0_m) / r.dt_s}));
  const steps = limits.flatMap((l) => [{p: l.p0_m, v: l.limit_mps}, {p: l.p1_m, v: l.limit_mps}]);
  return Plot.plot({
    width, height,
    x: {label: "metres from the start stop", domain: [0, seg.length_m]},
    y: {label: "speed (m/s)", grid: true, domain: [0, Math.max(LINE_SPEED_MPS, seg.v_max_mps, d3.max(pr, (r) => r.v) ?? 0) + 2]},
    marks: [
      Plot.rectX(zones, {x1: "x1", x2: "x2", fill: REF, fillOpacity: 0.08}),
      Plot.line(steps, {x: "p", y: "v", stroke: "currentColor", strokeDasharray: "4,3", strokeOpacity: 0.7}),
      Plot.text(limits.slice(0, 1), {x: seg.length_m, y: (l: RefLimits) => limits.at(-1)!.limit_mps,
        text: () => "speed limit", textAnchor: "end", dy: -6, fill: "currentColor", fillOpacity: 0.7}),
      Plot.link(pr, {x1: "p0", x2: "p1", y1: "v", y2: "v", stroke: OBS, strokeWidth: 2, strokeOpacity: 0.7,
        channels: {trip: "trip_id", date: "date", "Δt (s)": "dt_s", "excess (s)": "excess_s"}, tip: true}),
      Plot.line(knots, {x: "p_m", y: "v_mps", stroke: REF, strokeWidth: 2.5, curve: "monotone-x"}),
    ],
  });
}

/** Histogram of excess = Δt actual − Δt reference. Below 0 = faster than the "minimum". */
export function excessHist(pairs: RefPairs[], {width, height = 200, label = "excess (s)"}:
                             {width: number; height?: number; label?: string}) {
  const rows = pairs.map((r) => ({e: Math.max(-150, Math.min(250, r.excess_s)), beats: r.excess_s < 0}));
  return Plot.plot({
    width, height,
    x: {label: `${label}: excess (s)`},
    y: {label: "pairs", grid: true},
    color: {domain: [true, false], range: [OBS, "var(--theme-foreground-faint)"]},
    marks: [
      Plot.rectY(rows, Plot.binX({y: "count"}, {x: "e", fill: "beats", thresholds: d3.range(-150, 251, 10), insetLeft: 1, insetRight: 1})),
      Plot.ruleX([0], {stroke: "currentColor"}),
      Plot.ruleY([0]),
    ],
  });
}

// ------------------------------------------------------------------ one trip
export interface StopRow { station_id: string; s_m: number; t_epoch: number; name: string }

export function tripTimeDistance(refRows: RefTrips[], stops: StopRow[], fixes: RefFixes[],
                                 {width, height = 560}: {width: number; height?: number}) {
  const ts = [...refRows.map((r) => r.t_epoch), ...stops.map((s) => s.t_epoch), ...fixes.filter((f) => f.zone !== "origin").map((f) => f.t_epoch)];
  const [t0, t1] = [d3.min(ts)! - 300, d3.max(ts)! + 300];
  const shown = fixes.filter((f) => f.t_epoch >= t0 && f.t_epoch <= t1);
  return Plot.plot({
    width, height, marginRight: 110,
    x: {type: "time", label: null, grid: true, domain: [date(t0), date(t1)]},
    y: {label: "km along the trip", reverse: true, grid: false},
    marks: [
      Plot.ruleY(stops, {y: (s: StopRow) => s.s_m / 1000, stroke: "currentColor", strokeOpacity: 0.12}),
      Plot.axisY({anchor: "right", ticks: stops.map((s) => s.s_m / 1000), tickFormat: (_: number, i: number) => shortName(stops[i].name), tickSize: 0, label: null}),
      Plot.axisY({anchor: "left", label: "km along the trip"}),
      Plot.line(stops, {x: (s: StopRow) => date(s.t_epoch), y: (s: StopRow) => s.s_m / 1000, stroke: SCHED, strokeWidth: 1.5, strokeDasharray: "5,3"}),
      Plot.line(refRows, {x: (r: RefTrips) => date(r.t_epoch), y: (r: RefTrips) => r.s_m / 1000, stroke: REF, strokeWidth: 2}),
      Plot.dot(shown, {x: (r: RefFixes) => date(r.t_epoch), y: (r: RefFixes) => r.s_m / 1000, r: 3, stroke: OBS,
        fill: (r: RefFixes) => (r.repeat ? "none" : OBS),
        channels: {delay: (r: RefFixes) => `${r.delay_s.toFixed(0)} s`}, tip: true}),
    ],
  });
}

export function tripDelay(fixes: RefFixes[], stops: StopRow[], {width, height = 320}: {width: number; height?: number}) {
  const rows = fixes.filter((f) => f.zone === "line").flatMap((f) => [
    {s: f.s_m / 1000, d: f.delay_s, what: "vs reference", repeat: f.repeat, t: f.t_epoch},
    {s: f.s_m / 1000, d: f.sched_delay_s, what: "vs schedule (straight line)", repeat: f.repeat, t: f.t_epoch},
  ]);
  return Plot.plot({
    width, height,
    x: {label: "km along the trip", domain: d3.extent(stops, (s) => s.s_m / 1000) as [number, number]},
    y: {label: "delay (s)", grid: true},
    color: {domain: ["vs reference", "vs schedule (straight line)"], range: [OBS, SCHED], legend: true},
    marks: [
      Plot.ruleX(stops, {x: (s: StopRow) => s.s_m / 1000, stroke: "currentColor", strokeOpacity: 0.12}),
      Plot.ruleY([0], {stroke: "currentColor"}),
      Plot.line(rows, {x: "s", y: "d", z: "what", stroke: "what", strokeWidth: 1.5, sort: "t"}),
      Plot.dot(rows, {x: "s", y: "d", stroke: "what", fill: (r) => (r.repeat ? "none" : r.what), r: 3,
        channels: {time: (r) => date(r.t).toLocaleTimeString()}, tip: true}),
    ],
  });
}

// ------------------------------------------------------------------ timetable
/** Where 511's timetable puts each departure within the static minute (interval midpoint
 *  minus the static time), for stops narrowed by trip updates. */
export function timetableOffsets(rows: Timetable[], {width, height = 220}: {width: number; height?: number}) {
  const tu = rows.filter((r) => r.source === "tu").map((r) => ({off: (r.dep_lo_s + r.dep_hi_s) / 2 - r.static_s}));
  return Plot.plot({
    width, height,
    x: {label: "interval midpoint − static time (s)", domain: [-6, 66]},
    y: {label: "stops", grid: true},
    marks: [
      Plot.rectY(tu, Plot.binX({y: "count"}, {x: "off", thresholds: d3.range(-6, 67, 3), fill: OBS, insetLeft: 1, insetRight: 1})),
      Plot.ruleX([0, 60], {stroke: "currentColor", strokeDasharray: "4,3"}),
      Plot.ruleY([0]),
    ],
  });
}

/** Interval widths by route: static-only stops are 60 s wide, trip updates narrow them. */
export function timetableWidths(rows: (Timetable & {route_id: string})[], {width}: {width: number}) {
  const counts = d3.rollups(rows, (v) => v.length, (r) => r.route_id, (r) => Math.round(r.dep_hi_s - r.dep_lo_s))
    .flatMap(([route, ws]) => ws.map(([w, n]) => ({route, w: `${w} s`, n})));
  return Plot.plot({
    width, height: 40 * new Set(counts.map((c) => c.route)).size + 50, marginLeft: 110,
    x: {label: "stops", grid: true},
    y: {label: null},
    color: {legend: true, label: "interval width", scheme: "blues", reverse: true, type: "ordinal"},
    marks: [Plot.barX(counts, {y: "route", x: "n", fill: "w", tip: true}), Plot.ruleX([0])],
  });
}

/** Timetable fit: each pattern's fitted scheduled time over each of its segments against the
 *  physical minimum run time, with the fitted line Δ = (1 + p) R + W. Filled: the pattern's
 *  times were narrowed by 511 trip updates; hollow: static only; triangles: into a terminal
 *  (no W, so they sit below the line's offset). */
export function fitScatter(rows: RefFit[], p: number, W: number, {width, height = 380}: {width: number; height?: number}) {
  const xs = d3.extent(rows, (r) => r.phys_s) as [number, number];
  const line = [xs[0] - 10, xs[1] + 10].map((x) => ({x, y: (1 + p) * x + W}));
  return Plot.plot({
    width, height,
    x: {label: "physical minimum run time R (s)", grid: true},
    y: {label: "pattern's scheduled time over the segment, as fitted (s)", grid: true},
    marks: [
      Plot.line(xs.map((x) => ({x, y: x})), {x: "x", y: "y", stroke: "currentColor", strokeDasharray: "4,3", strokeOpacity: 0.5}),
      Plot.line(line, {x: "x", y: "y", stroke: REF, strokeWidth: 2}),
      Plot.dot(rows, {x: "phys_s", y: "sched_s", r: 4, fill: (r: RefFit) => (r.narrowed ? OBS : "none"), stroke: OBS,
        symbol: (r: RefFit) => (r.final ? "triangle" : "circle"),
        channels: {pattern: "pattern_id", segment: "segment_id", trips: "n_trips", narrowed: "narrowed",
          "padding (s)": (r: RefFit) => r.pad_s.toFixed(0)}, tip: true}),
    ],
  });
}
