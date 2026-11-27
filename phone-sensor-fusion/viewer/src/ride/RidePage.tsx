// One ride in detail: the audio and accelerometer spectrograms with the labelled door
// chimes, and under them, on the same time axis, the model's filtered and smoothed
// beliefs. Zoom and pan; click to put the cursor anywhere and play the audio from there.
//
// Zoom: pinch, or ⌘/ctrl + scroll, or + and −. Pan: drag, or scroll sideways. Space plays
// and pauses; ← → move the cursor 10 s (shift: 1 s); 0 shows the whole ride.
import {useCallback, useEffect, useMemo, useRef, useState, type ReactNode} from "react";
import {loadJson, useLoad} from "../lib/data";
import {readHash, replaceHash} from "../lib/hash";
import {loadRuns, runBase} from "../lib/runs";
import {rideStart, TIME_ZONE} from "../lib/rides";
import {stationCode} from "../lib/stations";
import Canvas, {type Draw} from "./Canvas";
import {DOOR_CLOSE, DOOR_OPEN, MODE_SHORT, drawEvents, drawGrid, drawLine, drawStack, drawStrip, drawTiles, makeTiles, type Tiles} from "./draw";
import {
  BLUES, BLUES_DARK, MAGMA, MODE_COLORS, clampView, clock, cssVar, tOf, ticks, xOf, zoomView,
} from "./timeline";
import type {Ride, RideMeta, View} from "./types";

const GUTTER = 168;  // px: the track names and their values at the cursor
const RATES = [1, 2, 4, 8];
const HIDE_KEY = "psf-ride-hide-identifying";

/** A ride as one run's inference saw it (by default the newest run's): what the model says
 * is in the run's folder, the spectrograms in a folder shared by every run (model/ride_view.py). */
async function loadRide(id: string, run: string | null): Promise<Ride> {
  if (!run) {
    const runs = await loadRuns();
    if (!runs.length) throw new Error("no evaluation runs yet");
    run = runs[0].name;
  }
  const base = `${runBase(run)}/rides/${encodeURIComponent(id)}`;
  const shared = `/data/rides/${encodeURIComponent(id)}`;
  const meta = await loadJson<RideMeta>(`${base}/meta.json`);
  const bin = async (url: string) => {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url}: ${res.status}`);
    return new Uint8Array(await res.arrayBuffer());
  };
  const [spec, accel, speedFilt, speedSmooth] = await Promise.all(
    [`${shared}/${meta.spec.file}`, `${shared}/${meta.accel.file}`, `${base}/${meta.speed_post.filt}`, `${base}/${meta.speed_post.smooth}`].map(bin));
  return {meta, spec, accel, speedFilt, speedSmooth, run};
}

export default function RidePage({id}: {id: string}) {
  const ride = useLoad(() => loadRide(id, readHash().params.get("run")), [id]);
  if (ride.status === "loading") return <p className="status">Loading the ride…</p>;
  if (ride.status === "error") {
    return <div className="status error"><p>Couldn't load this ride.</p><pre>{ride.error}</pre>
      <p>Run <code>python -m pipeline</code> in <code>phone-sensor-fusion/</code> first. <a href="#/results">Back to the results</a></p></div>;
  }
  return <RideView ride={ride.value} />;
}

interface Track {
  key: string;
  name: string;
  height: number;
  draw: Draw;
  value?: (i: number) => ReactNode;  // at second i
  legend?: ReactNode;  // in the gutter, under the name
  below?: ReactNode;  // a row of its own under the track
  newGroup?: boolean;  // a gap above it, between groups of related tracks
}

function RideView({ride}: {ride: Ride}) {
  const {meta} = ride;
  const T = meta.T;

  // where we are: from the address, else the whole ride
  const [view, setView] = useState<View>(() => {
    const p = readHash().params;
    const t0 = Number(p.get("t0")), t1 = Number(p.get("t1"));
    return clampView(p.has("t0") && p.has("t1") && t1 > t0 ? {t0, t1} : {t0: 0, t1: T}, T);
  });
  // the playback cursor; null: none (playing starts at the left edge of the view)
  const [cursor, setCursor] = useState<number | null>(() => {
    const t = Number(readHash().params.get("t"));
    return Number.isFinite(t) && readHash().params.has("t") ? t : null;
  });
  const [hover, setHover] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  // "hide identifying info": show nothing about the ride beyond the section in view (no date,
  // no endpoints, no whole-ride strip, times counted from the view's left edge); remembered
  const [hide, setHide] = useState(() => localStorage.getItem(HIDE_KEY) === "1");
  useEffect(() => localStorage.setItem(HIDE_KEY, hide ? "1" : "0"), [hide]);
  // a time as shown: since the recording started, or (hiding) since the view's left edge
  const fmtT = (t: number) => {
    const r = hide ? t - view.t0 : t;
    return r < 0 ? `−${clock(-r)}` : clock(r);
  };
  const [plotW, setPlotW] = useState(800);
  const viewRef = useRef(view);
  viewRef.current = view;

  // keep the address in step (no history entries)
  useEffect(() => {
    const id = setTimeout(() => replaceHash(["ride", meta.id],
      {run: ride.run, t0: view.t0.toFixed(1), t1: view.t1.toFixed(1), ...(cursor != null ? {t: cursor.toFixed(1)} : {})}), 300);
    return () => clearTimeout(id);
  }, [view, cursor, meta.id, ride.run]);

  // the spectrograms and beliefs over speed as coloured tiles, once
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const tiles = useMemo(() => ({
    spec: makeTiles(ride.spec, meta.spec.frames, meta.spec.bins, meta.spec.fps, MAGMA),
    accel: makeTiles(ride.accel, meta.accel.frames, meta.accel.bins, meta.accel.fps, MAGMA),
    speedFilt: makeTiles(ride.speedFilt, T, meta.speed_post.bins, 1, dark ? BLUES_DARK : BLUES),
    speedSmooth: makeTiles(ride.speedSmooth, T, meta.speed_post.bins, 1, dark ? BLUES_DARK : BLUES),
  }), [ride, meta, T, dark]);

  const tracks = useTracks(meta, tiles, view);
  const axis = useAxis(meta, view, hide);

  // ---- audio ----
  const audio = useRef<HTMLAudioElement>(null);
  const seek = useCallback((t: number) => {
    setCursor(t);
    const a = audio.current;
    if (a && !a.paused) a.currentTime = Math.max(t - meta.audio.t0, 0);
  }, [meta.audio.t0]);
  const toggle = useCallback(() => {
    const a = audio.current;
    if (!a) return;
    if (a.paused) {
      const from = cursor ?? Math.max(viewRef.current.t0, 0);
      setCursor(from);
      a.currentTime = Math.max(from - meta.audio.t0, 0);
      a.playbackRate = rate;
      a.play().catch(() => setPlaying(false));
    } else a.pause();
  }, [cursor, rate, meta.audio.t0]);
  useEffect(() => { if (audio.current) audio.current.playbackRate = rate; }, [rate]);
  const clearCursor = () => { audio.current?.pause(); setCursor(null); };
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const step = () => {
      const a = audio.current;
      // once paused (the pause event comes a little later), leave the cursor alone: the stop
      // button has just removed it
      if (a && !a.paused) {
        const t = a.currentTime + meta.audio.t0;
        setCursor(t);
        const v = viewRef.current, span = v.t1 - v.t0;
        if (t > v.t1 - 0.02 * span || t < v.t0) setView(clampView({t0: t - 0.1 * span, t1: t + 0.9 * span}, T));
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [playing, meta.audio.t0, T]);

  // ---- zoom and pan ----
  const area = useRef<HTMLDivElement>(null);
  const plotX = (clientX: number) => clientX - area.current!.getBoundingClientRect().left - GUTTER;
  useEffect(() => {
    const el = area.current!;
    const ro = new ResizeObserver(() => setPlotW(Math.max(el.clientWidth - GUTTER, 100)));
    ro.observe(el);
    const wheel = (e: WheelEvent) => {
      const x = plotX(e.clientX);
      if (x < 0) return;
      const v = viewRef.current;
      if (e.ctrlKey || e.metaKey) {  // pinch (Chrome, Firefox) or ⌘/ctrl + scroll
        e.preventDefault();
        setView(zoomView(v, Math.exp(e.deltaY * 0.01), tOf(x, v, el.clientWidth - GUTTER), T));
      } else if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {  // sideways: pan
        e.preventDefault();
        const dt = (e.deltaX / (el.clientWidth - GUTTER)) * (v.t1 - v.t0);
        setView(clampView({t0: v.t0 + dt, t1: v.t1 + dt}, T));
      }
    };
    // Safari's pinch: gesture events rather than ctrl + wheel
    let scale0 = 1, view0 = viewRef.current, around = 0;
    const gstart = (e: any) => {
      e.preventDefault();
      scale0 = e.scale; view0 = viewRef.current;
      around = tOf(Math.max(plotX(e.clientX ?? 0), 0), view0, el.clientWidth - GUTTER);
    };
    const gchange = (e: any) => { e.preventDefault(); setView(zoomView(view0, scale0 / e.scale, around, T)); };
    el.addEventListener("wheel", wheel, {passive: false});
    el.addEventListener("gesturestart", gstart as EventListener);
    el.addEventListener("gesturechange", gchange as EventListener);
    return () => {
      ro.disconnect();
      el.removeEventListener("wheel", wheel);
      el.removeEventListener("gesturestart", gstart as EventListener);
      el.removeEventListener("gesturechange", gchange as EventListener);
    };
  }, [T]);

  const drag = useRef<{x: number; view: View; moved: boolean} | null>(null);
  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0 || plotX(e.clientX) < 0) return;
    drag.current = {x: e.clientX, view, moved: false};
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const x = plotX(e.clientX);
    setHover(x >= 0 && x <= plotW ? tOf(x, view, plotW) : null);
    const d = drag.current;
    if (!d) return;
    if (Math.abs(e.clientX - d.x) > 3) d.moved = true;
    if (d.moved) {
      const dt = ((d.x - e.clientX) / plotW) * (d.view.t1 - d.view.t0);
      setView(clampView({t0: d.view.t0 + dt, t1: d.view.t1 + dt}, T));
    }
  };
  const onPointerUp = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    if (d && !d.moved) seek(Math.min(Math.max(tOf(plotX(e.clientX), view, plotW), 0), T));
  };

  // ---- keys ----
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, select, textarea")) return;
      const v = viewRef.current, mid = (v.t0 + v.t1) / 2;
      if (e.key === " ") { e.preventDefault(); toggle(); }
      else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        const t = Math.min(Math.max((cursor ?? v.t0) + (e.key === "ArrowRight" ? 1 : -1) * (e.shiftKey ? 1 : 10), 0), T);
        seek(t);
        if (t < v.t0 || t > v.t1) setView(clampView({t0: t - (v.t1 - v.t0) / 2, t1: t + (v.t1 - v.t0) / 2}, T));
      } else if (e.key === "+" || e.key === "=") setView(zoomView(v, 1 / 1.5, cursor != null && cursor >= v.t0 && cursor <= v.t1 ? cursor : mid, T));
      else if (e.key === "-" || e.key === "_") setView(zoomView(v, 1.5, mid, T));
      else if (e.key === "0") setView(clampView({t0: 0, t1: T}, T));
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [toggle, seek, cursor, T]);

  const atT = hover ?? cursor;  // the values are for the second under the mouse, else at the cursor
  const at = atT == null ? null : Math.min(Math.max(Math.floor(atT), 0), T - 1);
  const first = meta.stops[0]?.station, last = meta.stops[meta.stops.length - 1]?.station;
  const start = rideStart(meta.id);

  return (
    <div className="ride-page">
      {/* three columns: the controls, the ride (centred), the privacy toggle */}
      <div className="toolbar ride-bar">
        <div className="side">
          <a className="back" href={`#/results?run=${encodeURIComponent(ride.run)}`}>← Results</a>
          <div className="transport" role="group">
            <button onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
              {playing
                ? <svg viewBox="0 0 12 12" aria-hidden><rect x="2" y="1.5" width="3" height="9" rx=".6" /><rect x="7" y="1.5" width="3" height="9" rx=".6" /></svg>
                : <svg viewBox="0 0 12 12" aria-hidden><path d="M3 1.6v8.8a.6.6 0 0 0 .9.5l7-4.4a.6.6 0 0 0 0-1L3.9 1.1a.6.6 0 0 0-.9.5z" /></svg>}
            </button>
            <button onClick={clearCursor} disabled={cursor == null} aria-label="Clear cursor">
              <svg viewBox="0 0 12 12" aria-hidden><rect x="2.25" y="2.25" width="7.5" height="7.5" rx="1" /></svg>
            </button>
          </div>
          <div className="segmented" role="group" aria-label="Playback speed">
            {RATES.map((r) => <button key={r} aria-pressed={r === rate} onClick={() => setRate(r)}>{r}×</button>)}
          </div>
          <span className="times muted">{hide ? `${clock(0)}–${clock(view.t1 - view.t0)}` : `${clock(view.t0)}–${clock(view.t1)}`}</span>
          <div className="segmented" role="group">
            <button onClick={() => setView(zoomView(view, 1 / 1.5, (view.t0 + view.t1) / 2, T))} aria-label="Zoom in">+</button>
            <button onClick={() => setView(zoomView(view, 1.5, (view.t0 + view.t1) / 2, T))} aria-label="Zoom out">−</button>
            <button onClick={() => setView(clampView({t0: 0, t1: T}, T))}>Whole ride</button>
          </div>
        </div>
        {/* hidden but keeping its place */}
        <span className="ride-title" style={hide ? {visibility: "hidden"} : undefined} aria-hidden={hide}>
          {start ? new Intl.DateTimeFormat("en-GB", {timeZone: TIME_ZONE, weekday: "short", day: "numeric", month: "short"}).format(start).replace(",", "") : meta.id}
          {first && last ? ` · ${stationCode(first)}→${stationCode(last)}` : ""}
        </span>
        <div className="side end">
          <button className="hide-toggle" aria-pressed={hide} aria-label="Hide identifying info" onClick={() => setHide(!hide)}>
            {hide
              ? <svg viewBox="0 0 16 16" aria-hidden><path d="M2 8s2.2-4.5 6-4.5S14 8 14 8s-2.2 4.5-6 4.5S2 8 2 8z" /><circle cx="8" cy="8" r="2" /><path d="M2.5 2.5l11 11" /></svg>
              : <svg viewBox="0 0 16 16" aria-hidden><path d="M2 8s2.2-4.5 6-4.5S14 8 14 8s-2.2 4.5-6 4.5S2 8 2 8z" /><circle cx="8" cy="8" r="2" /></svg>}
          </button>
        </div>
      </div>
      <audio ref={audio} src={`/audio/${encodeURIComponent(meta.id)}/${encodeURIComponent(meta.audio.file)}`} preload="auto"
             onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} />

      <Overview meta={meta} view={view} hidden={hide} onMove={(t) => setView(clampView({t0: t - (view.t1 - view.t0) / 2, t1: t + (view.t1 - view.t0) / 2}, T))} />

      <div className="tracks" ref={area} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
           onPointerLeave={() => setHover(null)}>
        <div className="track axis">
          <div className="gutter"><span className="value">
            {hover != null ? fmtT(hover) : cursor != null ? fmtT(cursor) : ""}
          </span></div>
          <Canvas height={30} draw={axis} />
        </div>
        {tracks.map((t) => (
          <div className={t.newGroup ? "track new-group" : "track"} key={t.key}>
            <div className={t.height < 30 ? "gutter compact" : "gutter"}>
              <span className="name">{t.name}</span>
              {t.value && at != null && <span className="value">{t.value(at)}</span>}
              {t.legend && <span className="legend-in">{t.legend}</span>}
            </div>
            <Canvas height={t.height} draw={t.draw} />
            {t.below && <><div className="gutter" /><div className="below">{t.below}</div></>}
          </div>
        ))}
        {cursor != null && cursor >= view.t0 && cursor <= view.t1 &&
          <div className="cursor-line" style={{left: GUTTER + xOf(cursor, view, plotW)}} />}
        {hover != null && <div className="hover-line" style={{left: GUTTER + xOf(hover, view, plotW)}} />}
      </div>
    </div>
  );
}

/** The tracks, top to bottom, drawn for the current view. */
function useTracks(meta: RideMeta, tiles: Record<string, Tiles>, view: View): Track[] {
  const s = meta.series;
  const T = meta.T;
  const vMax = useMemo(() => {
    let m = 10;
    for (let i = 0; i < T; i++) m = Math.max(m, (s.v_smooth[i] ?? 0) + (s.v_smooth_sd[i] ?? 0), s.v_label[i] ?? 0, s.v_filt[i] ?? 0);
    return Math.ceil(m / 5) * 5;
  }, [s, T]);
  const speedBins = Math.round(vMax / meta.speed_post.dv) + 1;
  const argmax = (ps: number[]) => ps.reduce((b, p, k) => (p > ps[b] ? k : b), 0);
  const modeShort = ["moving", "stopped", "open chime", "open", "close chime", "closed"];

  return useMemo(() => {
    const colors = {smooth: cssVar("--accent"), filt: cssVar("--filt"), label: cssVar("--muted"), grid: cssVar("--line"),
      text: cssVar("--faint"), bad: cssVar("--bad"), good: cssVar("--good")};
    const yp = (h: number) => (x: number) => h - 2 - x * (h - 4);
    // frequency gridlines over a spectrogram (0 Hz at the bottom, fmax at the top), light on its dark colours
    const freqAxis = (ctx: CanvasRenderingContext2D, w: number, h: number, fmax: number, fs: number[], label: (f: number) => string) =>
      drawGrid(ctx, w, fs, (f) => h - (f / fmax) * h, label, "rgba(255,255,255,0.18)", "rgba(255,255,255,0.75)");
    // axis labels are plain numbers, with the unit on the top one only
    const unitOnTop = (values: number[], unit: string, scale = 1) => (v: number) =>
      v === Math.max(...values) ? `${v / scale} ${unit}` : `${v / scale}`;
    // speed grid rows: bin 0 standing at the bottom, bin k = k x dv m/s
    const beliefBins = Math.min(speedBins, meta.speed_post.bins);
    const beliefAxis = (ctx: CanvasRenderingContext2D, w: number, h: number) => {
      const y = (x: number) => h - (x / meta.speed_post.dv + 0.5) * (h / beliefBins);
      // 0 is the bottom row (standing); no line too close to the top edge to label
      const vs = Array.from({length: Math.floor(vMax / 10) + 1}, (_, k) => k * 10).filter((v) => y(v) >= 8);
      drawGrid(ctx, w, vs, y, unitOnTop(vs, "m/s"), colors.grid + "99", colors.text);
    };
    const tracksOut: Track[] = [
      {key: "audio", name: "Audio", height: 150,
        legend: <>
          <Key color={DOOR_OPEN}>opening starts</Key><Key color={DOOR_OPEN} dashed>opening ends</Key>
          <Key color={DOOR_CLOSE}>closing starts</Key><Key color={DOOR_CLOSE} dashed>closing ends</Key>
        </>,
        draw: (ctx, w, h) => {
          drawTiles(ctx, w, h, view, tiles.spec);
          freqAxis(ctx, w, h, meta.spec.fmax, [0, 1000, 2000, 3000, 4000], unitOnTop([0, 1000, 2000, 3000, 4000], "kHz", 1000));
          drawEvents(ctx, w, h, view, meta.events);
        }},
      {key: "accel", name: "Accelerometer", height: 110,
        draw: (ctx, w, h) => {
          drawTiles(ctx, w, h, view, tiles.accel);
          freqAxis(ctx, w, h, meta.accel.fmax, [0, 10, 20, 30, 40], unitOnTop([0, 10, 20, 30, 40], "Hz"));
          drawEvents(ctx, w, h, view, meta.events);
        }},
      {key: "speed-filt", name: "Speed belief, filtered", height: 120, newGroup: true,
        value: (i) => <Interval mean={s.v_filt[i]} lo={s.v_filt_lo[i]} hi={s.v_filt_hi[i]} />,
        draw: (ctx, w, h) => { drawTiles(ctx, w, h, view, tiles.speedFilt, beliefBins); beliefAxis(ctx, w, h); }},
      {key: "speed-smooth", name: "Speed belief, smoothed", height: 120,
        value: (i) => <Interval mean={s.v_smooth[i]} lo={s.v_smooth_lo[i]} hi={s.v_smooth_hi[i]} />,
        draw: (ctx, w, h) => { drawTiles(ctx, w, h, view, tiles.speedSmooth, beliefBins); beliefAxis(ctx, w, h); }},
      {key: "mode-label", name: "Mode, labelled", height: 36, newGroup: true,
        value: (i) => (s.mode_label[i] >= 0 ? modeShort[s.mode_label[i]] : "–"),
        draw: (ctx, w, h) => drawStrip(ctx, w, h, view, s.mode_label, MODE_COLORS)},
      {key: "mode-filt", name: "Mode, filtered", height: 44,
        value: (i) => { const k = argmax(s.mode_filt[i]); return `${modeShort[k]} ${s.mode_filt[i][k].toFixed(2)}`; },
        draw: (ctx, w, h) => drawStack(ctx, w, h, view, s.mode_filt, MODE_COLORS)},
      {key: "mode-smooth", name: "Mode, smoothed", height: 44,
        value: (i) => { const k = argmax(s.mode_smooth[i]); return `${modeShort[k]} ${s.mode_smooth[i][k].toFixed(2)}`; },
        below: <span className="legend-row">{MODE_SHORT.map((name, k) => <Key key={name} color={MODE_COLORS[k]} square>{name}</Key>)}</span>,
        draw: (ctx, w, h) => drawStack(ctx, w, h, view, s.mode_smooth, MODE_COLORS)},
      {key: "loc", name: "Localiser, live", height: 80, newGroup: true,
        value: (i) => <>
          <Key color={colors.good}>P(truth) <span className="v">{s.loc_p_true[i].toFixed(2)}</span></Key>
          <Key color={colors.label}>P(best guess) <span className="v">{s.loc_p_best[i].toFixed(2)}</span></Key>
          <Key color={colors.bad + "60"} square>confidently wrong</Key>
        </>,
        draw: (ctx, w, h) => {
          const y = yp(h);
          ctx.fillStyle = colors.bad + "40";
          for (let i = Math.max(Math.floor(view.t0), 0); i < Math.min(Math.ceil(view.t1), T); i++) {
            if (s.loc_wrong[i]) ctx.fillRect(xOf(i, view, w), 0, Math.max(xOf(i + 1, view, w) - xOf(i, view, w), 0.5) + 0.3, h);
          }
          drawGrid(ctx, w, [0, 0.5, 1], y, (x) => `${x}`, colors.grid, colors.text);
          ctx.save(); ctx.setLineDash([4, 3]); ctx.strokeStyle = colors.text;
          ctx.beginPath(); ctx.moveTo(0, y(0.9)); ctx.lineTo(w, y(0.9)); ctx.stroke(); ctx.restore();
          ctx.fillStyle = colors.text;
          for (const t of meta.loc_openings) if (t >= view.t0 && t <= view.t1) ctx.fillRect(xOf(t, view, w) - 0.5, 0, 1.5, 6);
          drawLine(ctx, w, view, s.loc_p_best, y, {color: colors.label, width: 1.25});
          drawLine(ctx, w, view, s.loc_p_true, y, {color: colors.good, width: 1.75});
        }},
    ];
    return tracksOut;
  }, [meta, tiles, view, vMax, speedBins, s, T]);
}

/** Time ticks, and each labelled stop's station at its door opening. */
/** `hide`: times counted from the view's left edge rather than the recording's start. */
function useAxis(meta: RideMeta, view: View, hide: boolean): Draw {
  return useCallback((ctx, w, h) => {
    const text = cssVar("--faint"), strong = cssVar("--text"), line = cssVar("--line-strong");
    ctx.font = "11px ui-sans-serif, -apple-system, sans-serif";
    ctx.fillStyle = text;
    ctx.strokeStyle = line;
    ctx.textBaseline = "bottom";
    const origin = hide ? view.t0 : 0;
    for (const r of ticks({t0: view.t0 - origin, t1: view.t1 - origin}, w)) {
      const x = Math.round(xOf(r + origin, view, w)) + 0.5;
      ctx.beginPath(); ctx.moveTo(x, h - 5); ctx.lineTo(x, h); ctx.stroke();
      ctx.fillText(clock(r), x + 3, h - 1);
    }
    ctx.fillStyle = strong;
    ctx.font = "600 11px ui-sans-serif, -apple-system, sans-serif";
    ctx.textBaseline = "top";
    for (const st of meta.stops) {
      const t = st.t_open ?? st.t_arrive ?? st.t_close;
      if (t == null || t < view.t0 || t > view.t1) continue;
      ctx.fillText(stationCode(st.station), xOf(t, view, w) + 2, 2);
    }
  }, [meta, view, hide]);
}

/** The whole ride, small, with the part in view: click or drag to move there. */
function Overview({meta, view, onMove, hidden}: {meta: RideMeta; view: View; onMove: (t: number) => void; hidden: boolean}) {
  const whole = useMemo(() => ({t0: 0, t1: meta.T}), [meta.T]);
  const draw = useCallback<Draw>((ctx, w, h) => {
    if (hidden) return;  // blank, but keeping its place: nothing below jumps
    const s = meta.series;
    let m = 10;
    for (const v of s.v_smooth) m = Math.max(m, v ?? 0);
    drawStrip(ctx, w, h, whole, s.mode_smooth.map((ps) => (ps[0] < 0.5 ? 1 : -1)), ["", cssVar("--surface-2")]);
    drawLine(ctx, w, whole, s.v_smooth, (x) => h - 2 - (x / m) * (h - 4), {color: cssVar("--accent"), width: 1});
    ctx.fillStyle = cssVar("--accent") + "22";
    ctx.strokeStyle = cssVar("--accent");
    const x0 = xOf(view.t0, whole, w), x1 = xOf(view.t1, whole, w);
    ctx.fillRect(x0, 0, x1 - x0, h);
    ctx.strokeRect(x0 + 0.5, 0.5, Math.max(x1 - x0 - 1, 1), h - 1);
  }, [meta, view, whole, hidden]);
  const ref = useRef<HTMLDivElement>(null);
  const move = (e: React.PointerEvent) => {
    if (hidden) return;
    const r = ref.current!.getBoundingClientRect();
    onMove(((e.clientX - r.left) / r.width) * meta.T);
  };
  return (
    <div className="track overview">
      <div className="gutter"><span className="name" style={hidden ? {visibility: "hidden"} : undefined}>Whole ride</span></div>
      <div ref={ref} onPointerDown={(e) => { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); move(e); }}
           onPointerMove={(e) => e.buttons & 1 && move(e)}>
        <Canvas height={28} draw={draw} />
      </div>
    </div>
  );
}

/** A speed belief at one second: its mean and central 95 %, m/s. */
function Interval({mean, lo, hi}: {mean: number | null; lo: number | undefined; hi: number | undefined}) {
  if (mean == null) return <>–</>;
  return <>
    <span data-tip="Mean of the belief">{mean.toFixed(1)} m/s</span>
    {lo != null && hi != null &&
      <span className="muted" data-tip="The speeds that hold the central 95 % of the belief">95%: [{lo.toFixed(1)}, {hi.toFixed(1)}]</span>}
  </>;
}

/** One entry of a legend: a line (solid or dashed) or a square of colour, then the text. */
function Key({color, dashed, square, children}: {color: string; dashed?: boolean; square?: boolean; children: ReactNode}) {
  return (
    <span className="key">
      {square ? <i className="sq" style={{background: color}} />
        : <i className="ln" style={{borderTop: `2px ${dashed ? "dashed" : "solid"} ${color}`}} />}
      {children}
    </span>
  );
}
