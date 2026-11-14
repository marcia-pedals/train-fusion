// One row per date, a bar each for VP and TU, coloured by status: either the share of time in
// each status ("aggregate") or when each status held, on a 03:00-03:00 time-of-day axis ("time").
// Rows are spans of data/fetch_status.parquet / data/data_presence.parquet
// (train_fusion.explorer_data.STATUS_SCHEMA).
import * as d3 from "npm:d3";
import * as Plot from "npm:@observablehq/plot";
import {filterBar, segmented} from "./controls.js";

export interface StatusSpan {date: string; feed: string; status: string; start_epoch: number; end_epoch: number;
  start_s: number; end_s: number}
export type StatusView = "aggregate" | "time";

const FEEDS = ["vehiclepositions", "tripupdates"];
const FEED_NAME: Record<string, string> = {vehiclepositions: "VP", tripupdates: "TU"};

const dayLabel = (d: string) => new Date(`${d}T12:00`).toLocaleDateString("en-US", {weekday: "short", month: "short", day: "numeric"});
const dur = (s: number) => (s < 120 ? `${Math.round(s)} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
const hhmm = (s: number) => new Date(s * 1000).toLocaleTimeString("en-US", {hour: "2-digit", minute: "2-digit", hour12: false});

/** The Total | When toggle; statusCard puts it in its header. */
export function viewToggle(): HTMLElement & {value: StatusView} {
  return segmented<StatusView>(["aggregate", "time"], {label: (v) => (v === "aggregate" ? "Total" : "When")}) as HTMLElement & {value: StatusView};
}

/** A card: title and subtitle on the left of the header with the view toggle on the right,
 *  then the chart, with its legend and caption at the bottom. */
export function statusCard(spans: StatusSpan[], {title, subtitle, toggle, ...opts}: Parameters<typeof statusDays>[1] & {
  title: string; subtitle: string; toggle: HTMLElement;
}): HTMLElement {
  // inside the card's 1rem padding and border
  return toggleCard({title, subtitle, toggle, chart: statusDays(spans, {...opts, width: opts.width - 34})});
}

/** The card statusCard puts its chart in (the chart's width: the card's minus 34 px). */
export function toggleCard({title, subtitle, toggle, chart}: {title: string; subtitle: string; toggle: HTMLElement;
  chart: HTMLElement | SVGSVGElement}): HTMLElement {
  const card = document.createElement("div");
  card.className = "card";
  card.style.cssText = "display: grid; gap: 12px";
  const head = document.createElement("div");
  head.style.cssText = "display: flex; justify-content: space-between; align-items: flex-start; gap: 16px";
  const titles = document.createElement("div");
  const h2 = Object.assign(document.createElement("h2"), {textContent: title});
  const h3 = Object.assign(document.createElement("h3"), {textContent: subtitle});
  titles.append(h2, h3);
  // filterBar carries the toggle's styles; no margins in the header
  head.append(titles, Object.assign(filterBar([[["", toggle]]]), {style: "margin: 0"}));
  chart.style.margin = "0";  // Plot's <figure> (it has a caption) brings its own
  card.append(head, chart);
  return card;
}

export function statusDays(spans: StatusSpan[], {statuses, colors, labels, view, caption, width}: {
  statuses: string[]; colors: string[]; labels: Record<string, string>; view: StatusView;
  caption: string; width: number;
}) {
  const dates = [...new Set(spans.map((r) => r.date))].sort();
  const totals = d3.flatRollup(spans, (v) => d3.sum(v, (r) => r.end_s - r.start_s), (r) => r.date, (r) => r.feed, (r) => r.status)
    .map(([date, feed, status, s]) => ({date, feed, status, s, share: 0}));
  const whole = d3.rollup(totals, (v) => d3.sum(v, (r) => r.s), (r) => r.date, (r) => r.feed);
  for (const r of totals) r.share = r.s / whole.get(r.date)!.get(r.feed)!;
  const color = {domain: statuses, range: colors, tickFormat: (k: string) => labels[k]};
  const common = {
    width, marginLeft: 110, marginRight: 20, marginTop: 20, height: 20 + dates.length * 46,
    // the legend at the bottom, right above the caption
    caption: Object.assign(document.createElement("div"), {style: "font-size: 11px; margin-top: 4px"}),
    fy: {domain: dates, axis: null, padding: 0.25},
    y: {domain: FEEDS, tickFormat: (f: string) => FEED_NAME[f], label: null, padding: 0.1},
    color,
  };
  common.caption.append(Plot.legend({color, style: "margin: 0 0 2px"} as Plot.LegendOptions), caption);
  // the date on the left, outside the VP / TU labels
  const dayAxis = Plot.axisFy({anchor: "left", tickFormat: dayLabel, tickSize: 0, dx: -30, label: null, fontWeight: 600});
  const head = (r: {date: string; feed: string; status: string}) => `${dayLabel(r.date)} ${FEED_NAME[r.feed]}: ${labels[r.status]}`;
  return view === "aggregate"
    ? Plot.plot({
      ...common,
      x: {domain: [0, 1], tickFormat: "%", label: null, grid: true, axis: "top"},
      marks: [
        Plot.barX(totals, Plot.stackX({order: statuses}, {x: "share", y: "feed", fy: "date", fill: "status",
          title: (r) => `${head(r)}\n${dur(r.s)} (${(100 * r.share).toFixed(1)}%)`, tip: true})),
        dayAxis,
      ],
    })
    : Plot.plot({
      ...common,
      // hours after the service-day origin; 3 to 27 is the fetcher's 03:00-03:00 service day
      x: {domain: [3, 27], ticks: d3.range(3, 28, 3), tickFormat: (h: number) => `${h % 24}:00`, label: null, grid: true, axis: "top"},
      marks: [
        Plot.rectX(spans, {x1: (r) => r.start_s / 3600, x2: (r) => r.end_s / 3600, y: "feed", fy: "date", fill: "status",
          title: (r) => `${head(r)}\n${hhmm(r.start_epoch)}–${hhmm(r.end_epoch)} (${dur(r.end_s - r.start_s)})`, tip: true}),
        dayAxis,
      ],
    });
}
