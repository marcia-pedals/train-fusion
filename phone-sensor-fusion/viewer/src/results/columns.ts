// What each results table shows, and how: one entry per column (model/schemas.py has
// what the columns mean; that text is the header's tooltip).
import type {Legs, Rides} from "../generated/tables";
import * as f from "../lib/format";
import {rideHref} from "../lib/hash";
import {stationCode} from "../lib/stations";

/** A row as the page shows it: the table's row plus how to name its ride (lib/rides.ts) and the run it is from. */
export type WithRide<R> = R & {ride_date: string; ride_date_detail: string; ride_endpoints: string; ride_endpoints_detail: string;
  run_name: string};
export type LegRow = WithRide<Legs>;
export type RideRow = WithRide<Rides>;

export type Tone = "bad" | "warn" | undefined;

/** How to compare a metric between two runs: `badness` (default: the value itself; null
 * is worst) grows as the metric gets worse, and a change in it smaller than `eps` is too
 * small to call better or worse. */
export interface Diff {
  badness?: (x: number) => number;
  eps: number;
}

export interface Column<R> {
  key: keyof R & string;
  header: string;
  group: string;
  /** cell text; null/undefined values show a dash (or `nullText`) */
  format?: (x: any) => string;
  nullText?: string;
  /** the cell's tooltip */
  tip?: (row: R) => string;
  /** how worrying a value is */
  tone?: (x: any) => Tone;
  align?: "left" | "right";
  /** summarise in the footer: the mean (and median) of x, of |x|, or of log10 x */
  summary?: "value" | "abs" | "log";
  /** sort by this instead of the value */
  sortBy?: (row: R) => string | number | null | undefined;
  noSort?: boolean;
  /** a metric: compared against the baseline run, when there is one */
  diff?: Diff;
}

const errTone = (x: number | null): Tone => (x == null ? undefined : Math.abs(x) > 0.1 ? "bad" : Math.abs(x) > 0.03 ? "warn" : undefined);
const pLow = (x: number | null): Tone => (x == null ? undefined : x > 0.5 ? "bad" : x > 0.1 ? "warn" : undefined);
const above = (warn: number, bad: number) => (x: number | null): Tone =>
  x == null ? undefined : x > bad ? "bad" : x > warn ? "warn" : undefined;
const abs = (k: string) => (r: any) => (r[k] == null ? null : Math.abs(r[k]));

// what counts as a real change, per kind of metric
const errDiff: Diff = {badness: Math.abs, eps: 0.005};  // half a percentage point of |error|
const pDiff: Diff = {eps: 0.01};  // probabilities: on the probability scale, so 1e-7 -> 1e-5 is no change
const secDiff: Diff = {eps: 5};
const countDiff: Diff = {eps: 0.5};
const expDiff: Diff = {eps: 0.01};

const RIDE = "Ride";
const LEG = "Leg";

/** The ride's date, sorted by when it started; and its first and last stations. */
const dateColumn = <R extends WithRide<{ride: string}>>(): Column<R> => ({
  key: "ride_date" as keyof R & string, header: "Date", group: RIDE, align: "left",
  tip: (r) => r.ride_date_detail, sortBy: (r) => r.ride,
});
const endpointsColumn = <R extends WithRide<{ride: string}>>(): Column<R> => ({
  key: "ride_endpoints" as keyof R & string, header: "Endpoints", group: RIDE, align: "left",
  tip: (r) => r.ride_endpoints_detail,
});

/** The ride page: the whole ride, or a leg with a margin of a tenth of it (at least 15 s) either side. */
export const rideRowHref = (r: RideRow) => rideHref(r.ride, r.run_name);
export const legRowHref = (r: LegRow) => {
  const pad = Math.max(15, 0.1 * (r.t_to_s - r.t_from_s));
  return rideHref(r.ride, r.run_name, r.t_from_s - pad, r.t_to_s + pad);
};

/** Header tooltips for the columns that aren't in the schema. */
export const EXTRA_DESCRIPTIONS: Record<string, string> = {
  ride_date: "The day of the ride (Pacific time)",
  ride_endpoints: "The ride's first and last labelled stations",
};

export const LEG_COLUMNS: Column<LegRow>[] = [
  dateColumn<LegRow>(),
  {key: "from_station", header: "From", group: LEG, align: "left", format: stationCode, tip: (r) => r.from_station},
  {key: "to_station", header: "To", group: LEG, align: "left", format: stationCode, tip: (r) => r.to_station},
  {key: "map_m", header: "Map", group: LEG, format: f.km},
  {key: "duration_s", header: "Duration", group: LEG, format: f.duration},
  {key: "filt_dist_err", header: "Dist err", group: "Filtered", format: f.pctSigned, tone: errTone, diff: errDiff, summary: "abs",
    sortBy: abs("filt_dist_err")},
  {key: "filt_p_missed_stop", header: "log P(miss)", group: "Filtered", format: f.logp, tone: pLow, diff: pDiff, summary: "log"},
  {key: "filt_p_false_stop", header: "log P(false stop)", group: "Filtered", format: f.logp, tone: pLow, diff: pDiff, summary: "log"},
  {key: "live_dist_err", header: "Dist err", group: "Live", format: f.pctSigned, tone: errTone, diff: errDiff, summary: "abs",
    sortBy: abs("live_dist_err")},
  {key: "smooth_dist_err", header: "Dist err", group: "Smoothed", format: f.pctSigned, tone: errTone, diff: errDiff, summary: "abs",
    sortBy: abs("smooth_dist_err")},
  {key: "smooth_p_missed_stop", header: "log P(miss)", group: "Smoothed", format: f.logp, tone: pLow, diff: pDiff, summary: "log"},
  {key: "smooth_p_false_stop", header: "log P(false stop)", group: "Smoothed", format: f.logp, tone: pLow, diff: pDiff, summary: "log"},
];

export const RIDE_COLUMNS: Column<RideRow>[] = [
  dateColumn<RideRow>(),
  endpointsColumn<RideRow>(),
  {key: "stops", header: "Stops", group: RIDE},
  {key: "moving_s", header: "Moving", group: RIDE, format: f.duration},
  {key: "loc_filt_time_to_fix_s", header: "Time to fix", group: "Localiser, filtered", format: f.duration,
    nullText: "never", diff: secDiff, tone: (x) => (x == null ? "bad" : above(300, 600)(x)), summary: "value"},
  {key: "loc_filt_lost_s", header: "Lost after fix", group: "Localiser, filtered", format: f.duration,
    tone: above(10, 60), diff: secDiff, summary: "value"},
  {key: "loc_filt_confident_wrong_s", header: "Confidently wrong", group: "Localiser, filtered", format: f.duration,
    tone: above(0, 60), diff: secDiff, summary: "value"},
  {key: "loc_smooth_stops_missing", header: "Missing", group: "Localiser, smoothed", tone: above(0, 0), diff: countDiff, summary: "value",
    format: f.count},
  {key: "loc_smooth_stops_spurious", header: "Spurious", group: "Localiser, smoothed", tone: above(0, 0), diff: countDiff, summary: "value",
    format: f.count},
  {key: "filt_dist_err_mean_abs", header: "Mean |dist err|", group: "Legs, filtered", format: f.pct, tone: errTone, diff: errDiff, summary: "value"},
  {key: "filt_missed_stops", header: "E[missed]", group: "Legs, filtered", format: f.expected, tone: above(0.1, 0.5), diff: expDiff, summary: "value"},
  {key: "filt_false_stops", header: "E[false]", group: "Legs, filtered", format: f.expected, tone: above(0.1, 0.5), diff: expDiff, summary: "value"},
  {key: "live_dist_err_mean_abs", header: "Mean |dist err|", group: "Legs, live", format: f.pct, tone: errTone, diff: errDiff, summary: "value"},
  {key: "smooth_dist_err_mean_abs", header: "Mean |dist err|", group: "Legs, smoothed", format: f.pct, tone: errTone, diff: errDiff, summary: "value"},
  {key: "smooth_missed_stops", header: "E[missed]", group: "Legs, smoothed", format: f.expected, tone: above(0.1, 0.5), diff: expDiff, summary: "value"},
  {key: "smooth_false_stops", header: "E[false]", group: "Legs, smoothed", format: f.expected, tone: above(0.1, 0.5), diff: expDiff, summary: "value"},
];
