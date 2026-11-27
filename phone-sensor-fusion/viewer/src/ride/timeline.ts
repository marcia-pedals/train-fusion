// Time-axis arithmetic and colours shared by the ride page's tracks.
import type {View} from "./types";

export const MIN_SPAN_S = 4;

/** x in [0, width] for recording time t. */
export const xOf = (t: number, v: View, width: number) => ((t - v.t0) / (v.t1 - v.t0)) * width;
export const tOf = (x: number, v: View, width: number) => v.t0 + (x / width) * (v.t1 - v.t0);

/** Keep a view inside the ride (with a little room past either end) and at least MIN_SPAN_S wide. */
export function clampView(v: View, T: number): View {
  const room = 0.02 * T;
  let span = Math.min(Math.max(v.t1 - v.t0, MIN_SPAN_S), T + 2 * room);
  let t0 = Math.min(Math.max(v.t0, -room), T + room - span);
  return {t0, t1: t0 + span};
}

export function zoomView(v: View, factor: number, around: number, T: number): View {
  return clampView({t0: around - (around - v.t0) * factor, t1: around + (v.t1 - around) * factor}, T);
}

/** "12:05" or "1:02:05": time since the recording started. */
export function clock(t: number): string {
  const s = Math.max(0, Math.round(t));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}` : `${m}:${String(ss).padStart(2, "0")}`;
}

/** Tick times for a view about `width` px wide, at least ~90 px apart. */
export function ticks(v: View, width: number): number[] {
  const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
  const want = ((v.t1 - v.t0) / width) * 90;
  const step = steps.find((s) => s >= want) ?? 3600;
  const out = [];
  for (let t = Math.ceil(v.t0 / step) * step; t <= v.t1; t += step) out.push(t);
  return out;
}

/** CSS custom property, for drawing on canvases in the current light/dark theme. */
export const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** 256-entry RGBA lookup table interpolating colour stops [position 0..1, r, g, b]. */
export function lut(stops: [number, number, number, number][]): Uint8ClampedArray {
  const out = new Uint8ClampedArray(256 * 4);
  for (let i = 0; i < 256; i++) {
    const x = i / 255;
    let k = 0;
    while (k < stops.length - 2 && x > stops[k + 1][0]) k++;
    const [p0, r0, g0, b0] = stops[k], [p1, r1, g1, b1] = stops[k + 1];
    const f = Math.min(Math.max((x - p0) / (p1 - p0), 0), 1);
    out.set([r0 + f * (r1 - r0), g0 + f * (g1 - g0), b0 + f * (b1 - b0), 255], i * 4);
  }
  return out;
}

/** For spectrograms: near-black through purple and orange to pale yellow (magma-like). */
export const MAGMA = lut([[0, 6, 5, 20], [0.25, 60, 15, 110], [0.5, 150, 40, 120], [0.75, 245, 110, 70], [1, 252, 245, 180]]);
/** For beliefs over speed: transparent-looking pale to deep blue. */
export const BLUES = lut([[0, 250, 250, 252], [0.3, 190, 210, 245], [0.7, 70, 120, 220], [1, 20, 40, 120]]);
export const BLUES_DARK = lut([[0, 24, 24, 26], [0.3, 30, 55, 110], [0.7, 70, 130, 235], [1, 200, 225, 255]]);

/** For log-likelihoods centred on 0 (128): blue below (speed unlikely), red above (likely). */
export const DIVERGING = lut([[0, 40, 90, 190], [0.3, 150, 185, 235], [0.5, 248, 248, 246], [0.7, 240, 160, 130], [1, 180, 30, 40]]);
export const DIVERGING_DARK = lut([[0, 90, 150, 255], [0.3, 35, 60, 115], [0.5, 22, 22, 24], [0.7, 125, 45, 35], [1, 255, 120, 90]]);

export const MODE_COLORS = ["#a7b0ba", "#f4a261", "#2a9d8f", "#8ecae6", "#e76f51", "#e9c46a"];
