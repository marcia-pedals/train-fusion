// Number formats shared by tables and (later) plots.

export const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);

/** Signed relative error: "+4.2 %", "−0.8 %". */
export const pctSigned = (x: number) => `${x >= 0 ? "+" : "−"}${Math.abs(100 * x).toFixed(1)} %`;

export const pct = (x: number) => `${(100 * x).toFixed(1)} %`;

/** log10 of a probability, floored where floats give out (and at exact 0). */
export const LOG_FLOOR = -300;
export const log10p = (p: number) => Math.max(Math.log10(p), LOG_FLOOR);

/** A log10 probability: "−10.5", "−0.3", and "≤ −300" at the floor. */
export const logText = (l: number) => (l <= LOG_FLOOR ? `≤ ${LOG_FLOOR}`.replace("-", "−") : signed(l, 1));
const signed = (x: number, d: number) => (x < 0 ? `−${(-x).toFixed(d)}` : x.toFixed(d));

/** Seconds: "48 s", "6.1 min". */
export const duration = (s: number) => (Math.abs(s) >= 120 ? `${(s / 60).toFixed(1)} min` : `${s.toFixed(0)} s`);

export const km = (m: number) => `${(m / 1000).toFixed(2)} km`;

export const count = (x: number) => (Number.isInteger(x) ? String(x) : x.toFixed(2));

/** An expected count: two decimals, and plain 0 for anything that rounds to it. */
export const expected = (x: number) => (x < 0.005 ? "0" : x.toFixed(2));

/** A probability shown as its log10: 3e-11 -> "−10.5". */
export const logp = (p: number) => logText(log10p(p));
