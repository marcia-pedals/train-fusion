// How each day's cases (trips or snapshots) fared against a trip-update hypothesis (stage tu_checks,
// served as data/tu_checks.parquet by train_fusion.explorer_data.tu_checks), a row per date:
// the share of cases by outcome ("aggregate") or a dot per time (at_epoch) with failures, and how many,
// on the 03:00-03:00 time-of-day axis of the other health cards ("time").
import * as d3 from "npm:d3";
import * as Plot from "npm:@observablehq/plot";
import {toggleCard, type StatusView} from "./statusdays.js";

// outcome: "ok", "failed" or (as the caller likes) a kind of failure, e.g. from `failure`
export interface TuCheck {date: string; trip_id: string; check: string; outcome: string; failure: string | null; dep0_epoch: number;
  at_epoch: number; at_s: number; detail: string}

const dayLabel = (d: string) => new Date(`${d}T12:00`).toLocaleDateString("en-US", {weekday: "short", month: "short", day: "numeric"});
const hhmm = (s: number) => new Date(s * 1000).toLocaleTimeString("en-US", {hour: "2-digit", minute: "2-digit", hour12: false});

/** A card with a check's chart, as statusCard. */
export function checkCard(rows: TuCheck[], {title, subtitle, toggle, ...opts}: Parameters<typeof checkDays>[1] & {
  title: string; subtitle: string; toggle: HTMLElement;
}): HTMLElement {
  return toggleCard({title, subtitle, toggle, chart: checkDays(rows, {...opts, width: opts.width - 34})});
}

export function checkDays(rows: TuCheck[], {outcomes, colors, labels, view, caption, unit, at, width}: {
  outcomes: string[]; colors: string[]; labels: Record<string, string>; view: StatusView; caption: string;
  unit: string;  // what a row is, plural: "trips", "snapshots"
  at: string;  // what at_epoch is, e.g. "snapshot at"
  width: number;
}) {
  const dates = [...new Set(rows.map((r) => r.date))].sort();
  const totals = d3.flatRollup(rows, (v) => v, (r) => r.date, (r) => r.outcome)
    .map(([date, outcome, v]) => ({date, outcome, n: v.length, share: 0,
      trips: d3.rollups(v, (g) => g.length, (r) => r.trip_id).map(([t, k]) => (k > 1 ? `${t} (${k})` : t))}));
  const whole = d3.rollup(rows, (v) => v.length, (r) => r.date);
  for (const r of totals) r.share = r.n / whole.get(r.date)!;
  const color = {domain: outcomes, range: colors, tickFormat: (k: string) => labels[k]};
  const common = {
    width, marginLeft: 110, marginRight: 20, marginTop: 20, height: 20 + dates.length * 34,
    caption: Object.assign(document.createElement("div"), {style: "font-size: 11px; margin-top: 4px"}),
    y: {domain: dates, tickFormat: dayLabel, tickSize: 0, label: null, padding: 0.3},
    color,
  };
  common.caption.append(Plot.legend({color, style: "margin: 0 0 2px"} as Plot.LegendOptions), caption);
  const yAxis = Plot.axisY({tickFormat: dayLabel, tickSize: 0, label: null, fontWeight: 600});
  const failed = (o: string) => o !== "ok";  // "failed", or a kind of failure
  return view === "aggregate"
    ? Plot.plot({
      ...common,
      x: {domain: [0, 1], tickFormat: "%", label: null, grid: true, axis: "top"},
      marks: [
        Plot.barX(totals, Plot.stackX({order: outcomes}, {x: "share", y: "date", fill: "outcome",
          title: (r) => `${dayLabel(r.date)}: ${labels[r.outcome]}\n${r.n} of ${whole.get(r.date)} ${unit} (${(100 * r.share).toFixed(1)}%)`
            + (failed(r.outcome) ? `\ntrips ${r.trips.join(", ")}` : ""),
          tip: true})),
        yAxis,
      ],
    })
    : timePlot(rows, dates, {...common, title: (g: TuCheck[]) => `${dayLabel(g[0].date)}, ${at} ${hhmm(g[0].at_epoch)}: `
        + `${labels[g[0].outcome]}, ${g.length} trip${g.length > 1 ? "s" : ""}\n`
        + g.map((r) => `${r.trip_id} (${hhmm(r.dep0_epoch)})` + (r.detail ? `: ${r.detail}` : "")).join("\n")});
}

/** A dot per moment with failures (same date, time and kind) on a 03:00-03:00 axis, a row per
 *  date, with how many there were beside it if more than one (held cases aren't drawn). */
function timePlot(rows: TuCheck[], dates: string[], {title, ...common}: Record<string, any> & {
  title: (g: TuCheck[]) => string;
}) {
  const groups = d3.groups(rows.filter((r) => r.outcome !== "ok"), (r) => `${r.date} ${r.at_epoch} ${r.outcome}`)
    .map(([, g]) => g);
  const x = (g: TuCheck[]) => g[0].at_s / 3600;
  return Plot.plot({
    ...common,
    // a tip listing many trips can be taller than the chart: let it spill over the legend
    style: {overflow: "visible"},
    // hours after the service-day origin; 3 to 27 is the fetcher's 03:00-03:00 service day
    x: {domain: [3, 27], ticks: d3.range(3, 28, 3), tickFormat: (h: number) => `${h % 24}:00`, label: null, grid: true, axis: "top"},
    marks: [
      // a faint line along each day, to tell which day a lone dot is on
      Plot.ruleY(dates, {y: (d) => d, stroke: "currentColor", strokeOpacity: 0.15}),
      Plot.dot(groups, {x, y: (g) => g[0].date, fill: (g) => g[0].outcome, r: 4,
        stroke: "var(--theme-background)", strokeWidth: 1, title, tip: {anchor: "top", lineWidth: 40}}),
      // the count beside a dot that stands for several (only then: a row of "1"s would run together)
      Plot.text(groups.filter((g) => g.length > 1), {x, y: (g) => g[0].date, text: (g) => g.length, textAnchor: "start", dx: 6, dy: -7,
        fontSize: 9, fill: "currentColor", stroke: "var(--theme-background-alt)", strokeWidth: 3, paintOrder: "stroke"}),
      Plot.axisY({tickFormat: dayLabel, tickSize: 0, label: null, fontWeight: 600}),
    ],
  });
}
