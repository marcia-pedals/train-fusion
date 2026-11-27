// Drawing the ride page's tracks on a canvas, for a view of recording time.
import type {DoorEvent, View} from "./types";
import {xOf} from "./timeline";

type Ctx = CanvasRenderingContext2D;

/** A raster ([frame][bin], uint8) cut into canvases of at most TILE frames (browsers limit
 *  canvas widths), coloured through a lookup table, low bins at the bottom. */
export interface Tiles { fps: number; bins: number; tiles: {start: number; canvas: HTMLCanvasElement}[] }

const TILE = 4096;

export function makeTiles(data: Uint8Array, frames: number, bins: number, fps: number, lut: Uint8ClampedArray): Tiles {
  const tiles = [];
  for (let start = 0; start < frames; start += TILE) {
    const n = Math.min(TILE, frames - start);
    const canvas = document.createElement("canvas");
    canvas.width = n;
    canvas.height = bins;
    const ctx = canvas.getContext("2d")!;
    const img = ctx.createImageData(n, bins);
    const px = img.data;
    for (let i = 0; i < n; i++) {
      const row = (start + i) * bins;
      for (let b = 0; b < bins; b++) {
        const v = data[row + b] * 4;
        const o = ((bins - 1 - b) * n + i) * 4;
        px[o] = lut[v]; px[o + 1] = lut[v + 1]; px[o + 2] = lut[v + 2]; px[o + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    tiles.push({start, canvas});
  }
  return {fps, bins, tiles};
}

/** The part of a raster in view. `binsShown`: draw only the lowest this many bins. */
export function drawTiles(ctx: Ctx, w: number, h: number, v: View, r: Tiles, binsShown = r.bins) {
  const f0 = Math.floor(v.t0 * r.fps), f1 = Math.ceil(v.t1 * r.fps);
  ctx.imageSmoothingEnabled = (v.t1 - v.t0) * r.fps > w;  // smooth when shrinking, crisp when enlarging
  for (const {start, canvas} of r.tiles) {
    const s = Math.max(start, f0), e = Math.min(start + canvas.width, f1);
    if (e <= s) continue;
    const x0 = xOf(s / r.fps, v, w), x1 = xOf(e / r.fps, v, w);
    ctx.drawImage(canvas, s - start, r.bins - binsShown, e - s, binsShown, x0, 0, x1 - x0, h);
  }
}

export const DOOR_OPEN = "#3ddc84", DOOR_CLOSE = "#ff5a5f";
export const MODE_SHORT = ["moving", "stopped, doors not open yet", "door-open chime", "doors open", "door-close chime", "stopped, doors closed",
  "before boarding", "after alighting"];

/** Labelled door chimes as vertical lines: green opening, red closing; solid where the
 *  chime starts, dashed where it ends. */
export function drawEvents(ctx: Ctx, w: number, h: number, v: View, events: DoorEvent[]) {
  ctx.save();
  ctx.lineWidth = 1.25;
  for (const e of events) {
    if (e.t < v.t0 || e.t > v.t1) continue;
    const x = Math.round(xOf(e.t, v, w)) + 0.5;
    ctx.strokeStyle = e.kind.startsWith("open") ? DOOR_OPEN : DOOR_CLOSE;
    ctx.setLineDash(e.kind.endsWith("_end") ? [4, 3] : []);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
  }
  ctx.restore();
}

/** The frames [i0, i1) of per-second series that are in view (frame n covers [n, n + 1)). */
export function framesInView(v: View, T: number): [number, number] {
  return [Math.max(Math.floor(v.t0) - 1, 0), Math.min(Math.ceil(v.t1) + 1, T)];
}

/** A per-second series as a line through each second's middle; nulls break it. */
export function drawLine(ctx: Ctx, w: number, v: View, ys: (number | null)[], y: (val: number) => number,
                         style: {color: string; width?: number; dash?: number[]; where?: (i: number) => boolean}) {
  const [i0, i1] = framesInView(v, ys.length);
  ctx.save();
  ctx.strokeStyle = style.color;
  ctx.lineWidth = style.width ?? 1.5;
  ctx.setLineDash(style.dash ?? []);
  ctx.lineJoin = "round";
  ctx.beginPath();
  let pen = false;
  for (let i = i0; i < i1; i++) {
    const val = ys[i];
    if (val == null || (style.where && !style.where(i))) { pen = false; continue; }
    const x = xOf(i + 0.5, v, w), yy = y(val);
    if (pen) ctx.lineTo(x, yy); else ctx.moveTo(x, yy);
    pen = true;
  }
  ctx.stroke();
  ctx.restore();
}

/** A band between lo[i] and hi[i] (e.g. mean ± sd). */
export function drawBand(ctx: Ctx, w: number, v: View, lo: (i: number) => number | null, hi: (i: number) => number | null,
                         T: number, y: (val: number) => number, color: string) {
  const [i0, i1] = framesInView(v, T);
  ctx.save();
  ctx.fillStyle = color;
  let run: number[] = [];
  const flush = () => {
    if (run.length > 1) {
      ctx.beginPath();
      run.forEach((i, k) => (k ? ctx.lineTo : ctx.moveTo).call(ctx, xOf(i + 0.5, v, w), y(hi(i)!)));
      for (let k = run.length - 1; k >= 0; k--) ctx.lineTo(xOf(run[k] + 0.5, v, w), y(lo(run[k])!));
      ctx.closePath();
      ctx.fill();
    }
    run = [];
  };
  for (let i = i0; i < i1; i++) {
    if (lo(i) == null || hi(i) == null) flush(); else run.push(i);
  }
  flush();
  ctx.restore();
}

/** Probabilities per second ([frame][category]) stacked bottom to top, one colour each. */
export function drawStack(ctx: Ctx, w: number, h: number, v: View, ps: number[][], colors: string[]) {
  const [i0, i1] = framesInView(v, ps.length);
  for (let i = i0; i < i1; i++) {
    const x0 = xOf(i, v, w), x1 = xOf(i + 1, v, w);
    let y = h;
    ps[i].forEach((p, k) => {
      const dh = p * h;
      if (dh <= 0) return;
      ctx.fillStyle = colors[k];
      ctx.fillRect(x0, y - dh, Math.max(x1 - x0, 0.5) + 0.3, dh);
      y -= dh;
    });
  }
}

/** One category per second as a colour (none for -1: unlabelled). */
export function drawStrip(ctx: Ctx, w: number, h: number, v: View, cats: number[], colors: string[]) {
  const [i0, i1] = framesInView(v, cats.length);
  for (let i = i0; i < i1; i++) {
    if (cats[i] < 0) continue;
    ctx.fillStyle = colors[cats[i]];
    ctx.fillRect(xOf(i, v, w), 0, Math.max(xOf(i + 1, v, w) - xOf(i, v, w), 0.5) + 0.3, h);
  }
}

/** Faint horizontal grid lines with their values at the left edge. */
export function drawGrid(ctx: Ctx, w: number, values: number[], y: (val: number) => number, label: (val: number) => string,
                         color: string, text: string) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.fillStyle = text;
  ctx.font = "10px ui-sans-serif, -apple-system, sans-serif";
  ctx.textBaseline = "middle";
  for (const val of values) {
    const yy = Math.round(y(val)) + 0.5;
    if (yy < 8) continue;  // too close to the top edge to label
    ctx.beginPath();
    ctx.moveTo(0, yy);
    ctx.lineTo(w, yy);
    ctx.stroke();
    ctx.fillText(label(val), 3, yy > 10 ? yy - 6 : yy + 7);
  }
  ctx.restore();
}
