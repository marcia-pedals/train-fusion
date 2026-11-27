// One ride in detail: the audio and accelerometer spectrograms with the labelled door
// chimes; what the features say about speed (each line spectrum's log-likelihood over
// speed, and each level against the curve the model expects it on); and under them, on
// the same time axis, the model's filtered and smoothed beliefs. Zoom and pan; click to put the cursor anywhere and play the audio from there.
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
import {DOOR_CLOSE, DOOR_OPEN, MODE_SHORT, drawBand, drawEvents, drawGrid, drawLine, drawStack, drawStrip, drawTiles, makeTiles, type Tiles} from "./draw";
import {
  BLUES, BLUES_DARK, DIVERGING, DIVERGING_DARK, MAGMA, MODE_COLORS, clampView, clock, cssVar, tOf, ticks, xOf, zoomView,
} from "./timeline";
import type {LevelCurve, Ride, RideMeta, View} from "./types";

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
  const [spec, accel, speedFilt, speedSmooth, combTone, combWheel] = await Promise.all(
    [`${shared}/${meta.spec.file}`, `${shared}/${meta.accel.file}`, `${base}/${meta.speed_post.filt}`, `${base}/${meta.speed_post.smooth}`,
      `${base}/${meta.comb.tone}`, `${base}/${meta.comb.wheel}`].map(bin));
  return {meta, spec, accel, speedFilt, speedSmooth, combTone, combWheel, run};
}

export default function RidePage({id}: {id: string}) {
  const ride = useLoad(() => loadRide(id, readHash().params.get("run")), [id]);
  if (ride.status === "loading") return <p className="status">Loading the ride…</p>;
  if (ride.status === "error") {
    return <div className="status error"><p>Couldn't load this ride.</p><pre>{ride.error}</pre>
      <p>Run <code>python -m pipeline</code> in <code>phone-sensor-fusion/</code> first. <a href="#/results">Back to the results</a></p></div>;
  }
  return <RideView key={ride.value.meta.id} ride={ride.value} />;  // a fresh view (and audio element) per ride
}

interface Track {
  key: string;
  name: string;
  short: string;  // on its switch in the track bar
  group: GroupKey;  // tracks of a group sit together; a gap between groups
  height: number;
  draw: Draw;
  value?: (i: number) => ReactNode;  // at second i
  // in the gutter, under the name; `sharedLegend`: the group's legend, shown on the first of
  // its tracks that is on
  legend?: ReactNode;
  sharedLegend?: boolean;
  // a row of its own under the track; `sharedBelow`: under the last of the group's tracks that is on
  below?: ReactNode;
  sharedBelow?: boolean;
}

const GROUPS = {
  spectra: "Spectra",
  evidence: "Evidence",
  levels: "Levels",
  belief: "Speed",
  mode: "Mode",
  regime: "Regime",
  loc: "Localiser",
} as const;
type GroupKey = keyof typeof GROUPS;

/** Track sets to switch to in one click: the tracks they show (null: all). */
const PRESETS: {name: string; on: string[] | null}[] = [
  {name: "All", on: null},
  {name: "Speed", on: ["comb-tone", "comb-wheel", "rumble", "vib-low", "vib-high", "speed-smooth"]},
  {name: "Stops", on: ["audio", "mode-label", "mode-filt", "mode-smooth", "mode-path"]},
];
const OFF_KEY = "psf-ride-tracks-off";
const COMB_LABEL_KEY = "psf-ride-comb-label";

/** The tracks switched off: from the address if it says, else as last time. */
function initialOff(): Set<string> {
  const p = readHash().params;
  const raw = p.has("off") ? p.get("off")! : localStorage.getItem(OFF_KEY) ?? "";
  return new Set(raw.split(",").filter(Boolean));
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
  const [off, setOff] = useState(initialOff);
  useEffect(() => localStorage.setItem(OFF_KEY, [...off].join(",")), [off]);
  // the labelled speed over the line-spectrum tracks; remembered
  const [combLabel, setCombLabel] = useState(() => localStorage.getItem(COMB_LABEL_KEY) !== "0");
  useEffect(() => localStorage.setItem(COMB_LABEL_KEY, combLabel ? "1" : "0"), [combLabel]);
  const [plotW, setPlotW] = useState(800);
  const viewRef = useRef(view);
  viewRef.current = view;

  // keep the address in step (no history entries)
  useEffect(() => {
    const id = setTimeout(() => replaceHash(["ride", meta.id],
      {run: ride.run, t0: view.t0.toFixed(1), t1: view.t1.toFixed(1), ...(cursor != null ? {t: cursor.toFixed(1)} : {}),
        ...(off.size ? {off: [...off].join(",")} : {})}), 300);
    return () => clearTimeout(id);
  }, [view, cursor, meta.id, ride.run, off]);

  // the spectrograms and beliefs over speed as coloured tiles, once
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const tiles = useMemo(() => ({
    spec: makeTiles(ride.spec, meta.spec.frames, meta.spec.bins, meta.spec.fps, MAGMA),
    accel: makeTiles(ride.accel, meta.accel.frames, meta.accel.bins, meta.accel.fps, MAGMA),
    speedFilt: makeTiles(ride.speedFilt, T, meta.speed_post.bins, 1, dark ? BLUES_DARK : BLUES),
    speedSmooth: makeTiles(ride.speedSmooth, T, meta.speed_post.bins, 1, dark ? BLUES_DARK : BLUES),
    combTone: makeTiles(ride.combTone, T, meta.speed_post.bins, 1, dark ? DIVERGING_DARK : DIVERGING),
    combWheel: makeTiles(ride.combWheel, T, meta.speed_post.bins, 1, dark ? DIVERGING_DARK : DIVERGING),
  }), [ride, meta, T, dark]);

  const allTracks = useTracks(ride, tiles, view, combLabel, setCombLabel);
  const tracks = allTracks.filter((t) => !off.has(t.key));
  const axis = useAxis(meta, view, hide, cursor, hover);

  // ---- audio ----
  // The audio element gets its file only when first played: loading it on opening the page
  // makes the system hand the audio output (headphones) to the page.
  const audio = useRef<HTMLAudioElement>(null);
  const audioUrl = `/audio/${encodeURIComponent(meta.id)}/${encodeURIComponent(meta.audio.file)}`;
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
      const start = () => {
        a.currentTime = Math.max(from - meta.audio.t0, 0);
        a.playbackRate = rate;
        a.play().catch(() => setPlaying(false));
      };
      if (a.getAttribute("src")) start();
      else {
        a.addEventListener("loadedmetadata", start, {once: true});
        a.src = audioUrl;
      }
    } else a.pause();
  }, [cursor, rate, meta.audio.t0, audioUrl]);
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
        <TrackBar tracks={allTracks} off={off} setOff={setOff} />
        <Overview meta={meta} view={view} hidden={hide} onMove={(t) => setView(clampView({t0: t - (view.t1 - view.t0) / 2, t1: t + (view.t1 - view.t0) / 2}, T))} />
        <div className="track axis">
          <div className="gutter"><span className="value">
            {hover != null ? fmtT(hover) : cursor != null ? fmtT(cursor) : ""}
          </span></div>
          <Canvas height={30} draw={axis} />
        </div>
      </div>
      <audio ref={audio} preload="none"
             onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} />


      <div className="tracks" ref={area} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
           onPointerLeave={() => setHover(null)}>
        {tracks.map((t, k) => {
          const prev = tracks[k - 1];
          const legend = t.legend && (!t.sharedLegend || !tracks.slice(0, k).some((u) => u.group === t.group && u.sharedLegend));
          const below = t.below && (!t.sharedBelow || !tracks.slice(k + 1).some((u) => u.group === t.group && u.sharedBelow));
          return (
            <div className={prev && prev.group !== t.group ? "track new-group" : "track"} key={t.key}>
              <div className={t.height < 30 ? "gutter compact" : "gutter"}>
                <span className="name">{t.name}</span>
                {t.value && at != null && <span className="value">{t.value(at)}</span>}
                {legend && <span className="legend-in">{t.legend}</span>}
              </div>
              <Canvas height={t.height} draw={t.draw} />
              {below && <><div className="gutter" /><div className="below">{t.below}</div></>}
            </div>
          );
        })}
        {!tracks.length && <p className="status">Every track is switched off: switch some on above.</p>}
        {cursor != null && cursor >= view.t0 && cursor <= view.t1 &&
          <div className="cursor-line" style={{left: GUTTER + xOf(cursor, view, plotW)}} />}
        {hover != null && <div className="hover-line" style={{left: GUTTER + xOf(hover, view, plotW)}} />}
      </div>
    </div>
  );
}

/** The tracks, top to bottom, drawn for the current view. */
function useTracks(ride: Ride, tiles: Record<string, Tiles>, view: View,
                   combLabel: boolean, setCombLabel: (on: boolean) => void): Track[] {
  const {meta} = ride;
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
    // speed lanes drawn as lines: 0 at the bottom, vMax at the top
    const ySpeed = (h: number) => (x: number) => h - 2 - (x / vMax) * (h - 4);
    const speedAxis = (ctx: CanvasRenderingContext2D, w: number, h: number) => {
      const vs = Array.from({length: Math.floor(vMax / 10) + 1}, (_, k) => k * 10).filter((v) => ySpeed(h)(v) >= 8);
      drawGrid(ctx, w, vs, ySpeed(h), unitOnTop(vs, "m/s"), colors.grid + "99", colors.text);
    };
    const vY = (h: number) => (x: number) => h - (x / meta.speed_post.dv + 0.5) * (h / beliefBins);

    // ---- what each line spectrum says about speed: the log-likelihood the HMM gets ----
    const llMax = meta.comb.ll_max, nb = meta.speed_post.bins;
    const llAt = (data: Uint8Array, i: number, k: number) => ((data[i * nb + k] - 128) / 127) * llMax;
    const combTrack = (key: string, name: string, short: string, data: Uint8Array, t: Tiles): Track => ({
      key, name, short, group: "evidence", height: 120, sharedLegend: true,
      legend: <>
        <span className="key"><i className="ramp" style={{background: rampCss()}} />−{llMax} … +{llMax} nats</span>
        <label className="check">
          <input type="checkbox" checked={combLabel} onChange={(e) => setCombLabel(e.target.checked)} />
          <Key color={colors.label}>labelled speed</Key>
        </label>
      </>,
      value: (i) => {
        if (i >= T) return null;
        let best = 1;
        for (let k = 2; k < nb; k++) if (data[i * nb + k] > data[i * nb + best]) best = k;
        const at = s.v_label[i];
        const fmt = (x: number) => `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(1)}`;
        return <>
          <span>
            peak {(best * meta.speed_post.dv).toFixed(1)} m/s {fmt(llAt(data, i, best))}</span>
          {at != null && at > 0 &&
            <span className="muted">
              at label {fmt(llAt(data, i, Math.min(Math.round(at / meta.speed_post.dv), nb - 1)))}</span>}
        </>;
      },
      draw: (ctx, w, h) => {
        drawTiles(ctx, w, h, view, t, beliefBins);
        beliefAxis(ctx, w, h);
        if (combLabel) drawLine(ctx, w, view, s.v_label, vY(h), {color: colors.label, width: 1.25});
      },
    });

    // ---- levels against the curve the model expects them on ----
    const levelTrack = (key: string, name: string, short: string, x: (number | null)[],
                        curveAt: (i: number) => LevelCurve | null): Track => {
      const exp = (i: number, v: number | null) => { const c = curveAt(i); return c && v != null ? levelMean(c, v) : null; };
      const lo = robustRange(x, (i) => exp(i, s.v_label[i]), curveAt, T);
      const y = (h: number) => (val: number) => h - 3 - ((val - lo[0]) / (lo[1] - lo[0])) * (h - 6);
      return {
        key, name, short, group: "levels", height: 90, sharedLegend: true,
        legend: <>
          <Key color={colors.smooth}>measured</Key>
          <Key color={colors.label + "55"} square>expected: label ± sd</Key>
          <Key color={colors.filt} dashed>expected: model</Key>
        </>,
        value: (i) => {
          if (x[i] == null) return "–";
          const c = curveAt(i), e = exp(i, s.v_label[i]);
          return <>
            <span>{x[i]!.toFixed(1)} dB</span>
            {c == null ? <span className="muted">not used</span>
              : e != null && <span className="muted">
                z {((x[i]! - e) / c.sd >= 0 ? "+" : "−") + Math.abs((x[i]! - e) / c.sd).toFixed(1)}</span>}
          </>;
        },
        draw: (ctx, w, h) => {
          const yy = y(h);
          const step = niceStep(lo[1] - lo[0]);
          const vals: number[] = [];
          for (let v = Math.ceil(lo[0] / step) * step; v <= lo[1]; v += step) vals.push(v);
          drawGrid(ctx, w, vals, yy, (v) => (v === vals[vals.length - 1] ? `${v} dB` : `${v}`), colors.grid, colors.text);
          drawBand(ctx, w, view, (i) => { const e = exp(i, s.v_label[i]); return e == null ? null : e - curveAt(i)!.sd; },
            (i) => { const e = exp(i, s.v_label[i]); return e == null ? null : e + curveAt(i)!.sd; }, T, yy, colors.label + "38");
          drawLine(ctx, w, view, s.v_smooth.map((v, i) => exp(i, v)), yy, {color: colors.filt, width: 1.25, dash: [4, 3]});
          drawLine(ctx, w, view, x, yy, {color: colors.smooth + "55", width: 1});  // faint where it doesn't count
          drawLine(ctx, w, view, x, yy, {color: colors.smooth, width: 1.25, where: (i) => curveAt(i) != null});
        },
      };
    };
    const phoneCurve = (cs: {resting: LevelCurve | null; held: LevelCurve | null}) => (i: number) =>
      s.phone[i] === 0 ? cs.resting : s.phone[i] === 1 ? cs.held : null;

    const doorLegend = <>
      <Key color={DOOR_OPEN}>opening starts</Key><Key color={DOOR_OPEN} dashed>opening ends</Key>
      <Key color={DOOR_CLOSE}>closing starts</Key><Key color={DOOR_CLOSE} dashed>closing ends</Key>
    </>;
    const modeLegend = <span className="legend-row">{MODE_SHORT.map((name, k) => <Key key={name} color={MODE_COLORS[k]} square>{name}</Key>)}</span>;
    // standing, then the regimes in model/states.py's order, coloured as on the dynamics page
    const regimeShort = ["standing", ...(meta.regime_names ?? [])];
    const regimeColors = [cssVar("--line-strong"), cssVar("--reg-0"), cssVar("--reg-1"), cssVar("--reg-3"), cssVar("--reg-2")];
    const regimeLegend = <span className="legend-row">{regimeShort.map((name, k) => <Key key={name} color={regimeColors[k]} square>{name}</Key>)}</span>;
    const regimeStack = (key: string, name: string, short: string, ps: number[][]): Track => ({
      key, name, short, group: "regime", height: 44, below: regimeLegend, sharedBelow: true,
      value: (i) => { const k = argmax(ps[i]); return `${regimeShort[k]} ${ps[i][k].toFixed(2)}`; },
      draw: (ctx, w, h) => drawStack(ctx, w, h, view, ps, regimeColors)});
    const tracksOut: Track[] = [
      {key: "audio", name: "Audio", short: "Audio", group: "spectra", height: 150, legend: doorLegend, sharedLegend: true,
        draw: (ctx, w, h) => {
          drawTiles(ctx, w, h, view, tiles.spec);
          freqAxis(ctx, w, h, meta.spec.fmax, [0, 1000, 2000, 3000, 4000], unitOnTop([0, 1000, 2000, 3000, 4000], "kHz", 1000));
          drawEvents(ctx, w, h, view, meta.events);
        }},
      {key: "accel", name: "Accelerometer", short: "Accel", group: "spectra", height: 110, legend: doorLegend, sharedLegend: true,
        draw: (ctx, w, h) => {
          drawTiles(ctx, w, h, view, tiles.accel);
          freqAxis(ctx, w, h, meta.accel.fmax, [0, 10, 20, 30, 40], unitOnTop([0, 10, 20, 30, 40], "Hz"));
          drawEvents(ctx, w, h, view, meta.events);
        }},
      combTrack("comb-tone", "Motor tone says", "Tone", ride.combTone, tiles.combTone),
      combTrack("comb-wheel", "Wheel lines say", "Wheel", ride.combWheel, tiles.combWheel),
      levelTrack("rumble", "Rumble level", "Rumble", meta.levels.rumble.x, () => meta.levels.rumble.curve),
      levelTrack("vib-low", "Vibration 2–10 Hz", "Vib lo", meta.levels.vib_low.x, phoneCurve(meta.levels.vib_low.curves)),
      levelTrack("vib-high", "Vibration 10–45 Hz", "Vib hi", meta.levels.vib_high.x, phoneCurve(meta.levels.vib_high.curves)),
      {key: "speed-filt", name: "Speed belief, filtered", short: "Filt", group: "belief", height: 120,
        value: (i) => <Interval mean={s.v_filt[i]} lo={s.v_filt_lo[i]} hi={s.v_filt_hi[i]} />,
        draw: (ctx, w, h) => { drawTiles(ctx, w, h, view, tiles.speedFilt, beliefBins); beliefAxis(ctx, w, h); }},
      {key: "speed-smooth", name: "Speed belief, smoothed", short: "Smooth", group: "belief", height: 120,
        value: (i) => <Interval mean={s.v_smooth[i]} lo={s.v_smooth_lo[i]} hi={s.v_smooth_hi[i]} />,
        draw: (ctx, w, h) => { drawTiles(ctx, w, h, view, tiles.speedSmooth, beliefBins); beliefAxis(ctx, w, h); }},
      ...(s.v_path ? [{key: "speed-path", name: "Speed, most likely path", short: "Path", group: "belief", height: 90,
        value: (i: number) => <>{s.v_path![i].toFixed(1)} m/s</>,
        draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => {
          speedAxis(ctx, w, h);
          drawLine(ctx, w, view, s.v_path!, ySpeed(h), {color: colors.smooth, width: 1.5});
        }} satisfies Track] : []),
      {key: "mode-label", name: "Mode, labelled", short: "Label", group: "mode", height: 36, below: modeLegend, sharedBelow: true,
        value: (i) => (s.mode_label[i] >= 0 ? modeShort[s.mode_label[i]] : "–"),
        draw: (ctx, w, h) => drawStrip(ctx, w, h, view, s.mode_label, MODE_COLORS)},
      {key: "mode-filt", name: "Mode, filtered", short: "Filt", group: "mode", height: 44, below: modeLegend, sharedBelow: true,
        value: (i) => { const k = argmax(s.mode_filt[i]); return `${modeShort[k]} ${s.mode_filt[i][k].toFixed(2)}`; },
        draw: (ctx, w, h) => drawStack(ctx, w, h, view, s.mode_filt, MODE_COLORS)},
      {key: "mode-smooth", name: "Mode, smoothed", short: "Smooth", group: "mode", height: 44, below: modeLegend, sharedBelow: true,
        value: (i) => { const k = argmax(s.mode_smooth[i]); return `${modeShort[k]} ${s.mode_smooth[i][k].toFixed(2)}`; },
        draw: (ctx, w, h) => drawStack(ctx, w, h, view, s.mode_smooth, MODE_COLORS)},
      ...(s.mode_path ? [{key: "mode-path", name: "Mode, most likely path", short: "Path", group: "mode", height: 36,
        value: (i: number) => modeShort[s.mode_path![i]],
        below: modeLegend, sharedBelow: true,
        draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => drawStrip(ctx, w, h, view, s.mode_path!, MODE_COLORS)} satisfies Track] : []),
      ...(s.regime_filt && s.regime_smooth && s.regime_path ? [
        regimeStack("regime-filt", "Regime, filtered", "Filt", s.regime_filt),
        regimeStack("regime-smooth", "Regime, smoothed", "Smooth", s.regime_smooth),
        {key: "regime-path", name: "Regime, most likely path", short: "Path", group: "regime", height: 36,
          value: (i: number) => regimeShort[s.regime_path![i]], below: regimeLegend, sharedBelow: true,
          draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => drawStrip(ctx, w, h, view, s.regime_path!, regimeColors)} satisfies Track,
      ] : []),
      {key: "loc", name: "Localiser, live", short: "Localiser", group: "loc", height: 80,
        value: (i) => <>
          <Key color={colors.good}>P(truth) <span className="v">{s.loc_p_true[i].toFixed(2)}</span></Key>
          <Key color={colors.label}>P(best guess) <span className="v">{s.loc_p_best[i].toFixed(2)}</span></Key>
          <Key color={colors.bad + "60"} square>confidently wrong</Key>
          {(() => {
            const g = meta.loc_wrong_guesses?.find((g) => i >= g.t0 && i < g.t1);
            return g ? <span className="key">best guess, wrong: <b>{stationCode(g.station)} {g.dir === "south" ? "SB" : "NB"}</b></span> : null;
          })()}
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
          // the best guess wherever it isn't the truth, at the start of each stretch it holds
          // (or the view's edge)
          ctx.save();
          ctx.font = "600 11px " + cssVar("--sans");
          ctx.fillStyle = colors.bad; ctx.textBaseline = "bottom";
          let free = -Infinity;  // where the last label ended: labels don't overlap
          for (const g of meta.loc_wrong_guesses ?? []) {
            if (g.t1 <= view.t0 || g.t0 >= view.t1) continue;
            const x = Math.max(xOf(g.t0, view, w), 0) + 3;
            if (x < free) continue;
            const text = `${stationCode(g.station)} ${g.dir === "south" ? "SB" : "NB"}`;
            ctx.fillText(text, x, h - 3);
            free = x + ctx.measureText(text).width + 6;
          }
          ctx.restore();
        }},
    ];
    return tracksOut;
  }, [ride, meta, tiles, view, vMax, speedBins, s, T, combLabel, setCombLabel]);
}

/** A level curve's mean at speed v (0: standing), as model/emissions.py _level_ll has it. */
function levelMean(c: LevelCurve, v: number): number {
  if (v <= 0) return c.mean[0];
  const ks = c.knots, ms = c.mean;
  if (v <= ks[1]) return ms[1];
  for (let j = 2; j < ks.length; j++) if (v <= ks[j]) return ms[j - 1] + ((v - ks[j - 1]) / (ks[j] - ks[j - 1])) * (ms[j] - ms[j - 1]);
  return ms[ms.length - 1];
}

/** A y range covering the 1st–99th percentile of the measurement and the expected band. */
function robustRange(x: (number | null)[], exp: (i: number) => number | null, curveAt: (i: number) => LevelCurve | null,
                     T: number): [number, number] {
  const vals: number[] = [];
  for (let i = 0; i < T; i++) {
    if (x[i] != null) vals.push(x[i]!);
    const e = exp(i), c = curveAt(i);
    if (e != null && c) vals.push(e - c.sd, e + c.sd);
  }
  if (!vals.length) return [0, 1];
  vals.sort((a, b) => a - b);
  const a = vals[Math.floor(0.01 * (vals.length - 1))], b = vals[Math.ceil(0.99 * (vals.length - 1))];
  const pad = 0.05 * (b - a || 1);
  return [a - pad, b + pad];
}

function niceStep(span: number): number {
  return [1, 2, 5, 10, 20, 50].find((s) => span / s <= 5) ?? 100;
}

/** The comb tracks' colour scale, for the legend. */
function rampCss(): string {
  const lut = window.matchMedia("(prefers-color-scheme: dark)").matches ? DIVERGING_DARK : DIVERGING;
  const stops = [0, 64, 128, 192, 255].map((k) => `rgb(${lut[4 * k]},${lut[4 * k + 1]},${lut[4 * k + 2]})`);
  return `linear-gradient(to right, ${stops.join(",")})`;
}

/** Time ticks, and each labelled stop's station at its door opening. */
/** `hide`: times counted from the view's left edge rather than the recording's start.
 *  It sits in the toolbar, away from the tracks, so it draws the cursor and the mouse's time itself. */
function useAxis(meta: RideMeta, view: View, hide: boolean, cursor: number | null, hover: number | null): Draw {
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
    if (hover != null && hover >= view.t0 && hover <= view.t1) {
      const x = Math.round(xOf(hover, view, w)) + 0.5;
      ctx.save(); ctx.strokeStyle = text; ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); ctx.restore();
    }
    if (cursor != null && cursor >= view.t0 && cursor <= view.t1) {
      ctx.fillStyle = strong;
      ctx.fillRect(xOf(cursor, view, w) - 1, 0, 2, h);
    }
  }, [meta, view, hide, cursor, hover]);
}

/** Switches for the tracks, by group: click a track to switch it, ⌥-click to show it alone;
 *  click a group's name to switch the whole group (⌥-click: that group alone); or a preset. */
function TrackBar({tracks, off, setOff}: {tracks: Track[]; off: Set<string>; setOff: (s: Set<string>) => void}) {
  const groups = (Object.keys(GROUPS) as GroupKey[]).map((g) => ({g, ts: tracks.filter((t) => t.group === g)})).filter((x) => x.ts.length);
  const only = (keys: string[]) => new Set(tracks.map((t) => t.key).filter((k) => !keys.includes(k)));
  const flip = (keys: string[], on: boolean) => {
    const next = new Set(off);
    for (const k of keys) if (on) next.delete(k); else next.add(k);
    setOff(next);
  };
  const same = (a: Set<string>, b: Set<string>) => a.size === b.size && [...a].every((k) => b.has(k));
  return (
    <div className="track-bar">
      {groups.map(({g, ts}) => {
        const keys = ts.map((t) => t.key), allOn = keys.every((k) => !off.has(k));
        return (
          <div className="track-group" key={g}>
            {ts.length > 1 &&
              <button className="group-name" onClick={(e) => (e.altKey ? setOff(only(keys)) : flip(keys, !allOn))}>{GROUPS[g]}</button>}
            <div className="chips" role="group" aria-label={GROUPS[g]}>
              {ts.map((t) => (
                <button key={t.key} aria-pressed={!off.has(t.key)} aria-label={t.name}
                        onClick={(e) => (e.altKey ? setOff(only([t.key])) : flip([t.key], off.has(t.key)))}>
                  {t.short}
                </button>
              ))}
            </div>
          </div>
        );
      })}
      <div className="segmented presets" role="group" aria-label="Track presets">
        {PRESETS.map((p) => {
          const target = p.on ? only(p.on) : new Set<string>();
          return <button key={p.name} aria-pressed={same(off, target)} onClick={() => setOff(target)}>{p.name}</button>;
        })}
      </div>
    </div>
  );
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
