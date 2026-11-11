// The pipeline-health waterfall for one date and feed, as two bars on one chart:
// - fetches: what we fetched, in time order along elapsed service time (the time axis on top;
//   time with no trip scheduled is left out);
// - trips: one thin row per route and direction, each trip spanning its scheduled run on the
//   same axis (later trips drawn over earlier ones), coloured by the share of its fetched run
//   it's in (red: never seen; dashed outline: no success / empty fetch during its run).
import * as d3 from "npm:d3";
import {ROUTES} from "./feed.js";

/** One row of data/funnel.parquet (train_fusion.explorer_data.funnel): stage totals. */
export interface FunnelRow {date: string; feed: string; stage: string; segment: string; value: number; n: number}

/** One row of data/funnel_pieces.parquet: a stretch of the service / fetches / trips stage. */
export interface FunnelPiece {date: string; feed: string; stage: string; segment: string; x0: number; x1: number;
  start_epoch: number; end_epoch: number; label: string | null; share: number | null;
  route_id: string | null; direction_id: number | null}

// blue ramp 100 -> 700, one step per coverage bin (0-10% ... 90-100%)
const BLUE = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#3987e5", "#2a78d6", "#1c5cab", "#184f95", "#0d366b"];
const NEUTRAL = "#c3c2b7", SERIOUS = "#ec835a", CRITICAL = "#d03b3b";

const FETCH = [
  {key: "success", label: "success", color: BLUE[7]},
  {key: "empty", label: "empty", color: NEUTRAL},
  {key: "failed", label: "failed", color: CRITICAL},
  {key: "no fetch", label: "no fetch", color: SERIOUS},
];
const BINS = d3.range(9, -1, -1).map((k) => ({key: String(k), label: k === 9 ? "90–100%" : `${k * 10}–${k * 10 + 10}%`, color: BLUE[k]}));
const NOT_SEEN = {key: "not seen", label: "never seen", color: CRITICAL};

const hours = (s: number) => (s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
const hhmm = (s: number) => new Date(s * 1000).toLocaleTimeString("en-US", {hour: "2-digit", minute: "2-digit", hour12: false});
const share = (v: number, total: number) => (!total ? "" : v / total < 0.005 ? " <1%" : ` ${Math.round((100 * v) / total)}%`);

export function funnel(rows: FunnelRow[], pieces: FunnelPiece[], width: number): SVGSVGElement {
  const LABEL = 112, AXIS = 16, BAR = 22, GAP = 6, TRIP = 11, TRIP_GAP = 2, LINE = 14;
  const by = (stage: string) => d3.sort(pieces.filter((p) => p.stage === stage), (p) => p.x0);
  const trips = by("trips");
  // trip rows: route (fixed order) then northbound, southbound; only those with trips
  const rowKey = (p: FunnelPiece) => `${p.route_id}|${p.direction_id}`;
  const tripRows = d3.sort(d3.groups(trips, rowKey), ([k]) => {
    const [route, dir] = k.split("|");
    return ROUTES.indexOf(route) * 2 + Number(dir);
  }).map(([k]) => k);
  const fetchY = AXIS, tripY = AXIS + BAR + GAP;
  const rowY = (k: string) => tripY + tripRows.indexOf(k) * (TRIP + TRIP_GAP);
  const tripsH = Math.max(tripRows.length, 1) * (TRIP + TRIP_GAP) - TRIP_GAP;
  const capY = tripY + tripsH + 16;
  const svg = d3.create("svg").attr("width", width).style("font", "11px var(--sans-serif)").style("overflow", "visible");
  const total = (stage: string) => d3.sum(rows.filter((r) => r.stage === stage), (r) => r.value);
  const get = (stage: string, segment: string) => rows.find((r) => r.stage === stage && r.segment === segment) ?? {value: 0, n: 0};

  // row labels
  svg.append("text").attr("x", 0).attr("y", fetchY + BAR / 2 + 4).attr("fill", "currentColor").attr("font-weight", 600).text("fetches");
  for (const k of tripRows) {
    const [route, dir] = k.split("|");
    svg.append("text").attr("x", 0).attr("y", rowY(k) + TRIP / 2 + 3.5).attr("fill", "currentColor").attr("font-size", 10)
      .text(`${route} ${dir === "0" ? "NB" : "SB"}`);
    svg.append("line").attr("x1", LABEL).attr("x2", width).attr("y1", rowY(k) + TRIP / 2).attr("y2", rowY(k) + TRIP / 2)
      .attr("stroke", "currentColor").attr("stroke-opacity", 0.12);
  }
  const empty = (y: number) => svg.append("rect").attr("x", LABEL).attr("y", y).attr("width", width - LABEL).attr("height", BAR)
    .attr("fill", "none").attr("stroke", "currentColor").attr("stroke-opacity", 0.3).attr("stroke-dasharray", "3,3");

  // time axis (elapsed service time) and the fetch bar
  const service = by("service");
  const serviceLen = d3.max(service, (p) => p.x1) ?? 0;
  const xt = d3.scaleLinear([0, serviceLen || 1], [LABEL, width]);
  const pxPerHour = ((width - LABEL) * 3600) / (serviceLen || 1);
  const every = [1, 2, 3, 4, 6].find((k) => k * pxPerHour >= 36) ?? 12;
  for (const p of service) {
    for (const h of d3.timeHour.every(1)!.range(new Date(p.start_epoch * 1000), new Date(p.end_epoch * 1000))) {
      const hx = xt(p.x0 + h.getTime() / 1000 - p.start_epoch);
      svg.append("line").attr("x1", hx).attr("x2", hx).attr("y1", AXIS - 4).attr("y2", AXIS)
        .attr("stroke", "currentColor").attr("stroke-opacity", 0.5);
      if (h.getHours() % every === 0 && hx > LABEL + 12 && hx < width - 14) {
        svg.append("text").attr("x", hx).attr("y", AXIS - 6).attr("text-anchor", "middle").attr("fill", "currentColor")
          .attr("fill-opacity", 0.7).attr("font-size", 10).text(`${h.getHours()}:00`);
      }
    }
  }
  // where elapsed service time skips ahead (no trip scheduled in between): a gap mark
  for (const p of service.slice(1)) {
    svg.append("line").attr("x1", xt(p.x0)).attr("x2", xt(p.x0)).attr("y1", AXIS - 8).attr("y2", tripY + tripsH)
      .attr("stroke", "currentColor").attr("stroke-dasharray", "2,2");
  }
  const fetchColor = new Map(FETCH.map((f) => [f.key, f]));
  const fetches = by("fetches");
  if (!fetches.length) empty(fetchY);
  for (const p of fetches) {
    const f = fetchColor.get(p.segment)!;
    svg.append("rect").attr("x", xt(p.x0)).attr("y", fetchY).attr("width", Math.max(xt(p.x1) - xt(p.x0), 0.5)).attr("height", BAR)
      .attr("fill", f.color).append("title").text(`${hhmm(p.start_epoch)}–${hhmm(p.end_epoch)}: ${f.label} (${hours(p.end_epoch - p.start_epoch)})`);
  }

  // the trips, on the time axis; later ones over earlier ones, with a surface ring so each
  // start shows
  if (!trips.length) empty(tripY);
  for (const p of trips) {
    const bin = p.share == null ? null : BINS.find((b) => b.key === String(Math.min(Math.floor(p.share! * 10), 9)))!;
    const noFetch = p.segment === "no fetch during run";
    svg.append("rect").attr("x", xt(p.x0)).attr("y", rowY(rowKey(p))).attr("width", Math.max(xt(p.x1) - xt(p.x0), 1))
      .attr("height", TRIP).attr("rx", 2)
      .attr("fill", noFetch ? "var(--theme-background-alt)" : bin ? bin.color : NOT_SEEN.color)
      .attr("stroke", noFetch ? "currentColor" : "var(--theme-background-alt)")
      .attr("stroke-opacity", noFetch ? 0.5 : 1).attr("stroke-dasharray", noFetch ? "2,2" : null)
      .append("title").text(`trip ${p.label} (${p.route_id} ${p.direction_id === 0 ? "northbound" : "southbound"}), `
        + `scheduled ${hhmm(p.start_epoch)}–${hhmm(p.end_epoch)}: `
        + (noFetch ? "no success / empty fetch during its run" : p.share == null ? "never seen"
          : `in ${Math.round(100 * p.share)}% of its fetched run`));
  }

  // captions: totals per segment with a swatch; wrapped (~5.6 px per character at 10 px)
  let cx = LABEL, cy = capY;
  const item = (color: string, text: string) => {
    const w = 5.6 * (text.length + 2);
    if (cx > LABEL && cx + w > width) { cx = LABEL; cy += LINE; }
    const t = svg.append("text").attr("x", cx).attr("y", cy).attr("font-size", 10);
    t.append("tspan").attr("fill", color).text("■ ");
    t.append("tspan").attr("fill", "currentColor").text(text);
    cx += w + 8;
  };
  const line = (label: string) => {
    if (cx > LABEL) { cx = LABEL; cy += LINE; }
    svg.append("text").attr("x", 0).attr("y", cy).attr("font-size", 10).attr("fill", "currentColor").attr("fill-opacity", 0.7).text(label);
  };
  line("fetches");
  const ft = total("fetches");
  for (const f of FETCH) {
    const r = get("fetches", f.key);
    if (r.value) item(f.color, `${f.label} ${hours(r.value)}${f.key === "no fetch" ? "" : ` (${r.n})`}${share(r.value, ft)}`);
  }
  line("trips");
  const seen = get("trips", "seen").n, notSeen = get("trips", "not seen").n, none = get("trips", "no fetch during run").n;
  for (const b of BINS) {
    const n = get("coverage", b.key).n;
    if (n) item(b.color, `${b.label} ${n}`);
  }
  if (notSeen) item(NOT_SEEN.color, `${NOT_SEEN.label} ${notSeen}`);
  cx = LABEL; cy += LINE;
  svg.append("text").attr("x", LABEL).attr("y", cy).attr("font-size", 10).attr("fill", "currentColor").attr("fill-opacity", 0.7)
    .text(`${seen + notSeen} trips whose run had success / empty fetches, by the share of that fetched run they're in`
      + (none ? `; ${none} more had no fetch during their run (dashed)` : ""));
  const height = cy + 6;
  svg.attr("height", height).attr("viewBox", [0, 0, width, height]);
  return svg.node()!;
}
