// Comparing a row with the same row (same ride, variant and leg) in a baseline run.
import type {Column} from "./columns";

/** same: equal; small: changed by less than the column's eps; better / worse: by more. */
export type Change = "same" | "small" | "better" | "worse";

const BIG = 1e15;  // stands in for "never" etc. (null badness), so that sorting still works

function badness<R>(c: Column<R>, x: unknown): number {
  if (typeof x !== "number" || !Number.isFinite(x)) return Infinity;  // null: as bad as it gets
  return c.diff?.badness ? c.diff.badness(x) : x;
}

/** How the metric moved from `old` to `x`, and by how much its badness grew (for sorting). */
export function compare<R>(c: Column<R>, x: unknown, old: unknown): {change: Change; delta: number} {
  const a = badness(c, old), b = badness(c, x);
  if (a === b) return {change: "same", delta: 0};
  const delta = a === Infinity ? -BIG : b === Infinity ? BIG : b - a;
  if (Math.abs(delta) < (c.diff?.eps ?? 0)) return {change: "small", delta};
  return {change: delta > 0 ? "worse" : "better", delta};
}

/** The row has no baseline, or a metric that moved by more than its eps. */
export function changed<R extends Record<string, any>>(columns: Column<R>[], row: R, base: R | undefined) {
  if (!base) return true;
  return columns.some((c) => {
    if (!c.diff) return false;
    const k = compare(c, row[c.key], base[c.key]).change;
    return k === "better" || k === "worse";
  });
}
