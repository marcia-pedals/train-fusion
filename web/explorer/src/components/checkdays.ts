// How each day's scheduled trips fared against one trip-update hypothesis (stage tu_checks,
// served as data/tu_checks.parquet by train_fusion.explorer_data.tu_checks), a row per date:
// the share of trips by outcome ("aggregate") or a dot per trip at when it was checked, or
// first failed, on the 03:00-03:00 time-of-day axis of the other health cards ("time").
import * as d3 from "npm:d3";
import * as Plot from "npm:@observablehq/plot";
import {toggleCard, type StatusView} from "./statusdays.js";

export interface TuCheck {date: string; trip_id: string; check: string; outcome: string; dep0_epoch: number;
  at_epoch: number; at_s: number; n_failures: number; detail: string}

const dayLabel = (d: string) => new Date(`${d}T12:00`).toLocaleDateString("en-US", {weekday: "short", month: "short", day: "numeric"});
const hhmm = (s: number) => new Date(s * 1000).toLocaleTimeString("en-US", {hour: "2-digit", minute: "2-digit", hour12: false});

/** A card with a check's chart, as statusCard. */
export function checkCard(rows: TuCheck[], {title, subtitle, toggle, ...opts}: Parameters<typeof checkDays>[1] & {
  title: string; subtitle: string; toggle: HTMLElement;
}): HTMLElement {
  return toggleCard({title, subtitle, toggle, chart: checkDays(rows, {...opts, width: opts.width - 34})});
}

export function checkDays(rows: TuCheck[], {outcomes, colors, labels, view, caption, failures, width}: {
  outcomes: string[]; colors: string[]; labels: Record<string, string>; view: StatusView; caption: string;
  failures: string;  // what n_failures counts, e.g. "changes"
  width: number;
}) {
  const dates = [...new Set(rows.map((r) => r.date))].sort();
  const totals = d3.flatRollup(rows, (v) => v, (r) => r.date, (r) => r.outcome)
    .map(([date, outcome, v]) => ({date, outcome, n: v.length, share: 0, trips: v.map((r) => r.trip_id),
      failures: d3.sum(v, (r) => r.n_failures)}));
  const whole = d3.rollup(rows, (v) => v.length, (r) => r.date);
  for (const r of totals) r.share = r.n / whole.get(r.date)!;
  const color = {domain: outcomes, range: colors, tickFormat: (k: string) => labels[k]};
  const common = {
    width, marginLeft: 110, marginRight: 70, marginTop: 20, height: 20 + dates.length * 34,
    caption: Object.assign(document.createElement("div"), {style: "font-size: 11px; margin-top: 4px"}),
    y: {domain: dates, tickFormat: dayLabel, tickSize: 0, label: null, padding: 0.3},
    color,
  };
  common.caption.append(Plot.legend({color, style: "margin: 0 0 2px"} as Plot.LegendOptions), caption);
  const yAxis = Plot.axisY({tickFormat: dayLabel, tickSize: 0, label: null, fontWeight: 600});
  const failed = (o: string) => o === "failed";
  return view === "aggregate"
    ? Plot.plot({
      ...common,
      x: {domain: [0, 1], tickFormat: "%", label: null, grid: true, axis: "top"},
      marks: [
        Plot.barX(totals, Plot.stackX({order: outcomes}, {x: "share", y: "date", fill: "outcome",
          title: (r) => `${dayLabel(r.date)}: ${labels[r.outcome]}\n${r.n} of ${whole.get(r.date)} trips (${(100 * r.share).toFixed(1)}%)`
            + (failed(r.outcome) ? `, ${r.failures} ${failures}\ntrips ${r.trips.join(", ")}` : ""),
          tip: true})),
        // the failure count beside its row, so a thin sliver is still read
        Plot.text(dates, {x: 1, y: (d) => d, dx: 8, textAnchor: "start", fill: "currentColor",
          text: (d) => `${totals.find((r) => r.date === d && failed(r.outcome))?.n ?? 0} failed`}),
        yAxis,
      ],
    })
    : Plot.plot({
      ...common,
      // hours after the service-day origin; 3 to 27 is the fetcher's 03:00-03:00 service day
      x: {domain: [3, 27], ticks: d3.range(3, 28, 3), tickFormat: (h: number) => `${h % 24}:00`, label: null, grid: true, axis: "top"},
      marks: [
        // failures last, on top
        Plot.dot(d3.sort(rows, (r) => failed(r.outcome)), {x: (r) => r.at_s / 3600, y: "date", fill: "outcome", r: 4,
          stroke: "var(--theme-background)", strokeWidth: 1,
          title: (r) => `${dayLabel(r.date)} trip ${r.trip_id} (scheduled ${hhmm(r.dep0_epoch)}): ${labels[r.outcome]}`
            + (r.detail ? `\n${r.detail}` : `\nfrom ${hhmm(r.at_epoch)}`),
          tip: true}),
        yAxis,
      ],
    });
}
