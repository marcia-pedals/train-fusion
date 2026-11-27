// The train dynamics a run learned (model/dynamics_view.py writes <run>/dynamics.json): per
// regime (braking / coasting / accelerating at part or full traction) and per speed, how the
// speed changes over the next second and how likely the regime is to change, against the
// labelled seconds they were fitted from. #/dynamics?run=<name>; the newest run by default.
import {useEffect, useMemo, useRef, useState, type ReactNode} from "react";
import {loadJson, useLoad} from "../lib/data";
import {readHash, replaceHash} from "../lib/hash";
import {loadRuns, runBase, runLabel, type RunMeta} from "../lib/runs";

interface Dynamics {
  dv: number;
  max_step: number;
  a_coast: number;
  regimes: string[];
  speeds: number[];
  p_step: number[][][];  // [regime][speed][step]
  p_regime: number[][][];  // [regime][speed][next regime]
  bandwidth: {step: number[][]; regime: number[][]};
  n: {steps: number; switches: number; of: number}[];
  points: {v: number[]; a: number[]; regime: number[]};
}

/** Each regime's colour, in model/states.py's order (braking, coasting, part traction, full
 * traction). Four colours can't all stay distinguishable to every eye, so every curve is
 * also named where it ends. */
interface Style { color: string }
const STYLES: Style[] = ["var(--reg-0)", "var(--reg-1)", "var(--reg-3)", "var(--reg-2)"].map((color) => ({color}));

function Swatch({s}: {s: Style}) {
  return <span className="sq" style={{background: s.color}} />;
}
const V_MAX = 38;
const A_LIM = [-1.6, 1.6] as const;
const M = {l: 46, r: 158, t: 10, b: 30};  // plot margins; the right one holds the direct labels

const BAND = 0.5;  // the central share of each step table the bands cover

/** Per regime and speed: mean and the central BAND of the acceleration over the next
 * second, each step of the table taken as spread evenly over its width. */
function summarise(d: Dynamics) {
  const steps = d.p_step[0][0].map((_, s) => (s - d.max_step) * d.dv);
  const quantile = (p: number[], q: number) => {
    let c = 0;
    for (let s = 0; s < p.length; s++) {
      if (c + p[s] >= q) return steps[s] - d.dv / 2 + (p[s] > 0 ? (q - c) / p[s] : 0.5) * d.dv;
      c += p[s];
    }
    return steps[steps.length - 1];
  };
  return d.p_step.map((rows) => rows.map((p) => {
    const tot = p.reduce((a, b) => a + b, 0);
    const pn = p.map((x) => x / tot);
    return {mean: pn.reduce((a, x, s) => a + x * steps[s], 0), lo: quantile(pn, (1 - BAND) / 2), hi: quantile(pn, (1 + BAND) / 2)};
  }));
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(900);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

const lin = (d0: number, d1: number, r0: number, r1: number) => (x: number) => r0 + ((x - d0) / (d1 - d0)) * (r1 - r0);
const ticks = (lo: number, hi: number, step: number) => {
  const out = [];
  for (let x = Math.ceil(lo / step) * step; x <= hi + 1e-9; x += step) out.push(+x.toFixed(6));
  return out;
};
const pathOf = (xs: number[], ys: number[]) => xs.map((x, i) => `${i ? "L" : "M"}${x.toFixed(1)},${ys[i].toFixed(1)}`).join("");

interface Frame { w: number; h: number; x: (v: number) => number; y: (a: number) => number }

/** An SVG plot with speed on x: axes, grid, the shared speed cursor, and mouse tracking. */
function Plot({width, height, y, yTicks, yFmt, yLabel, cursor, onCursor, children}: {
  width: number; height: number; y: [number, number]; yTicks: number[]; yFmt: (v: number) => string; yLabel: string;
  cursor: number | null; onCursor: (v: number | null) => void; children: (f: Frame) => ReactNode;
}) {
  const f: Frame = {w: width, h: height, x: lin(0, V_MAX, M.l, width - M.r), y: lin(y[0], y[1], height - M.b, M.t)};
  const move = (e: React.MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const v = ((e.clientX - box.left - M.l) / (width - M.r - M.l)) * V_MAX;
    onCursor(v >= 0 && v <= V_MAX ? v : null);
  };
  return (
    <svg className="dyn-plot" width={width} height={height} onMouseMove={move} onMouseLeave={() => onCursor(null)}>
      {yTicks.map((t) => <g key={t}>
        <line x1={M.l} x2={width - M.r} y1={f.y(t)} y2={f.y(t)} className={t === 0 ? "axis0" : "grid"} />
        <text x={M.l - 6} y={f.y(t)} className="tick" textAnchor="end" dominantBaseline="middle">{yFmt(t)}</text>
      </g>)}
      {ticks(0, V_MAX, 5).map((t) => <g key={t}>
        <line x1={f.x(t)} x2={f.x(t)} y1={M.t} y2={height - M.b} className="grid" />
        <text x={f.x(t)} y={height - M.b + 14} className="tick" textAnchor="middle">{t}</text>
      </g>)}
      <text x={width - M.r} y={height - 4} className="axis-label" textAnchor="end">speed (m/s)</text>
      <text x={10} y={M.t + 4} className="axis-label" transform={`rotate(-90 10 ${M.t + 4})`} textAnchor="end">{yLabel}</text>
      {children(f)}
      {cursor != null && <line x1={f.x(cursor)} x2={f.x(cursor)} y1={M.t} y2={height - M.b} className="cursor" />}
    </svg>
  );
}

/** Labels at the right end of curves, nudged apart so they don't overlap. */
function EndLabels({f, items}: {f: Frame; items: {y: number; text: string; style: Style}[]}) {
  const placed = [...items].sort((a, b) => a.y - b.y);
  for (let i = 1; i < placed.length; i++) placed[i] = {...placed[i], y: Math.max(placed[i].y, placed[i - 1].y + 13)};
  return <>{placed.map((it) => (
    <text key={it.text} x={f.w - M.r + 6} y={it.y} className="end-label" dominantBaseline="middle">
      <tspan style={{fill: it.style.color}}>■</tspan> {it.text}</text>
  ))}</>;
}

function AccelPlot({d, sum, width, cursor, onCursor}: {d: Dynamics; sum: ReturnType<typeof summarise>; width: number;
  cursor: number | null; onCursor: (v: number | null) => void}) {
  const height = 440;
  const yT = ticks(A_LIM[0], A_LIM[1], 0.5);
  // the dots don't depend on the cursor: built once per width, so moving the mouse doesn't redraw them
  const st = STYLES;
  const dots = useMemo(() => {
    const x = lin(0, V_MAX, M.l, width - M.r), y = lin(A_LIM[0], A_LIM[1], height - M.b, M.t);
    const {v, a, regime} = d.points;
    return <g className="dots">{v.map((vi, i) => a[i] >= A_LIM[0] && a[i] <= A_LIM[1] &&
      <circle key={i} cx={x(vi)} cy={y(a[i])} r={1.6} style={{fill: st[regime[i]].color}} />)}</g>;
  }, [d, width]);  // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Plot width={width} height={height} y={[A_LIM[0], A_LIM[1]]} yTicks={yT} yFmt={(t) => t.toFixed(1)}
          yLabel="acceleration (m/s²)" cursor={cursor} onCursor={onCursor}>
      {(f) => {
        const xs = d.speeds.map(f.x);
        return <>
          {dots}
          {sum.map((rows, r) => <g key={r}>
            <path className="band" style={{fill: st[r].color}}
                  d={pathOf(xs, rows.map((q) => f.y(q.hi))) + pathOf([...xs].reverse(), [...rows].reverse().map((q) => f.y(q.lo))).replace(/^M/, "L") + "Z"} />
          </g>)}
          {sum.map((rows, r) => <path key={r} className="mean-halo" d={pathOf(xs, rows.map((q) => f.y(q.mean)))} />)}
          {sum.map((rows, r) => <path key={r} className="mean" style={{stroke: st[r].color}}
                                      d={pathOf(xs, rows.map((q) => f.y(q.mean)))} />)}
          <EndLabels f={f} items={sum.map((rows, r) => ({y: f.y(rows[rows.length - 1].mean), text: d.regimes[r], style: st[r]}))} />
        </>;
      }}
    </Plot>
  );
}

function SwitchPlot({d, r, width, cursor, onCursor}: {d: Dynamics; r: number; width: number;
  cursor: number | null; onCursor: (v: number | null) => void}) {
  const height = 190;
  const st = STYLES;
  const others = d.regimes.map((_, k) => k).filter((k) => k !== r);
  const log = (p: number) => Math.log10(Math.max(p, 1e-7));
  return (
    <Plot width={width} height={height} y={[-7, 0]} yTicks={[-6, -4, -2, 0]} yFmt={(t) => (t === 0 ? "1" : `1e${t}`)}
          yLabel="per second" cursor={cursor} onCursor={onCursor}>
      {(f) => <>
        {others.map((k) => <path key={k} className="mean" style={{stroke: st[k].color}}
                                 d={pathOf(d.speeds.map(f.x), d.p_regime[r].map((p) => f.y(log(p[k]))))} />)}
        <EndLabels f={f} items={others.map((k) => ({y: f.y(log(d.p_regime[r][d.speeds.length - 1][k])),
                                                    text: `to ${d.regimes[k]}`, style: st[k]}))} />
      </>}
    </Plot>
  );
}

function BandwidthPlot({d, width, cursor, onCursor}: {d: Dynamics; width: number; cursor: number | null; onCursor: (v: number | null) => void}) {
  const height = 210;
  return (
    <Plot width={width} height={height} y={[0, 10.5]} yTicks={[0, 2, 4, 6, 8, 10]} yFmt={(t) => `${t}`}
          yLabel="kernel sd (m/s)" cursor={cursor} onCursor={onCursor}>
      {(f) => {
        const st = STYLES;
        return <>
          {d.regimes.map((_, r) => <path key={r} className="mean" style={{stroke: st[r].color}}
                                         d={pathOf(d.speeds.map(f.x), d.bandwidth.step[r].map(f.y))} />)}
          <EndLabels f={f} items={d.regimes.map((name, r) => ({y: f.y(d.bandwidth.step[r][d.speeds.length - 1]), text: name, style: st[r]}))} />
        </>;
      }}
    </Plot>
  );
}

const pct = (p: number) => (p >= 0.995 ? `${(100 * p).toFixed(1)}%` : p >= 0.01 ? `${(100 * p).toFixed(1)}%` : p.toExponential(0));
const sgn = (a: number) => `${a >= 0 ? "+" : "−"}${Math.abs(a).toFixed(2)}`;

/** What every regime's tables say at the cursor's speed, as visible text. */
function Readout({d, sum, cursor}: {d: Dynamics; sum: ReturnType<typeof summarise>; cursor: number | null}) {
  if (cursor == null) return <div className="dyn-readout" />;
  const j = Math.min(d.speeds.length - 1, Math.max(0, Math.round(cursor / d.dv)));
  return (
    <div className="dyn-readout">
      <b>{d.speeds[j].toFixed(2)} m/s</b>
      {d.regimes.map((name, r) => (
        <span key={name} className="key">
          <Swatch s={STYLES[r]} />
          {name}: {sgn(sum[r][j].mean)} m/s² ({sgn(sum[r][j].lo)} … {sgn(sum[r][j].hi)}), stays {pct(d.p_regime[r][j][r])},
          {" "}{d.regimes.map((n2, k) => k !== r && `→ ${n2} ${pct(d.p_regime[r][j][k])}`).filter(Boolean).join(", ")}
        </span>
      ))}
    </div>
  );
}

function Section({title, children}: {title: string; children: ReactNode}) {
  return <section className="dyn-section"><h2>{title}</h2>{children}</section>;
}

export default function DynamicsPage() {
  const runs = useLoad(loadRuns);
  if (runs.status === "loading") return <p className="status">Loading…</p>;
  if (runs.status === "error" || runs.value.length === 0) {
    return <div className="status error"><p>No evaluation runs yet.</p>
      {runs.status === "error" && <pre>{runs.error}</pre>}</div>;
  }
  return <Dyn runs={runs.value} />;
}

function Dyn({runs}: {runs: RunMeta[]}) {
  const known = (name: string | null) => (name && runs.some((r) => r.name === name) ? name : null);
  const [run, setRun] = useState(() => known(readHash().params.get("run")) ?? runs[0].name);
  useEffect(() => replaceHash(["dynamics"], {run}), [run]);
  const data = useLoad(() => loadJson<Dynamics>(`${runBase(run)}/dynamics.json`), [run]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [ref, width] = useWidth<HTMLDivElement>();
  const d = data.status === "ok" && data.value.regimes?.length === 4 ? data.value : null;
  const sum = useMemo(() => d && summarise(d), [d]);
  const w = Math.max(600, width - 40);

  return (
    <div className="dyn-page" ref={ref}>
      <div className="toolbar">
        <a className="back" href={`#/results?run=${encodeURIComponent(run)}`}>← Results</a>
        <label className="field">Run <select value={run} onChange={(e) => setRun(e.target.value)}>
          {runs.map((r) => <option key={r.name} value={r.name}>{runLabel(r)}</option>)}</select></label>
        {d && d.regimes.map((name, r) => <span key={name} className="key">
          <Swatch s={STYLES[r]} />{name}
          <span className="muted"> · {d.n[r].steps.toLocaleString()} s, {d.n[r].switches} switches out</span></span>)}
      </div>
      {data.status === "loading" && <p className="status">Loading…</p>}
      {(data.status === "error" || (data.status === "ok" && !d)) && <div className="status error">
        <p>This run has no dynamics, or an older form of them: rerun the pipeline.</p>
        {data.status === "error" && <pre>{data.error}</pre>}</div>}
      {d && sum && <>
        <div className="dyn-sticky"><Readout d={d} sum={sum} cursor={cursor} /></div>
        <Section title="Acceleration against speed">
          <AccelPlot d={d} sum={sum} width={w} cursor={cursor} onCursor={setCursor} />
          <div className="dyn-legend">
            <span className="key"><span className="dot" />observation</span>
            <span className="key"><span className="ln" />mean</span>
            <span className="key"><span className="bd" />middle {Math.round(100 * BAND)}%</span>
          </div>
        </Section>
        <Section title="Switching regime">
          {d.regimes.map((name, r) => <div key={name} className="dyn-sub">
            <h3><Swatch s={STYLES[r]} />from {name}</h3>
            <SwitchPlot d={d} r={r} width={w} cursor={cursor} onCursor={setCursor} />
          </div>)}
        </Section>
        <Section title="How far each speed borrows from its neighbours">
          <BandwidthPlot d={d} width={w} cursor={cursor} onCursor={setCursor} />
        </Section>
      </>}
    </div>
  );
}
