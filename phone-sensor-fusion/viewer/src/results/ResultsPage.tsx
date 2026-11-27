// The evaluation's two tables (model/evaluate.py): one row per test ride, or per leg; of
// one run (pipeline.py keeps every run), the newest unless the address names one
// (#/results?run=<name>), and optionally against a baseline run (&vs=<name>).
import {useEffect, useMemo, useRef, useState} from "react";
import {TABLES, type Legs, type Rides} from "../generated/tables";
import {loadParquet, useLoad} from "../lib/data";
import {readHash, replaceHash} from "../lib/hash";
import {rideInfo} from "../lib/rides";
import {loadRuns, runBase, runLabel, type RunMeta} from "../lib/runs";
import {stationCode} from "../lib/stations";
import {EXTRA_DESCRIPTIONS, LEG_COLUMNS, RIDE_COLUMNS, legRowHref, rideRowHref, type Column, type LegRow, type RideRow} from "./columns";
import ResultsTable from "./ResultsTable";
import {changed} from "./diff";

const describe = (table: keyof typeof TABLES) => ({...EXTRA_DESCRIPTIONS, ...Object.fromEntries(TABLES[table].columns.map((c) =>
  [c.name, c.unit ? `${c.desc} [${c.unit}]` : c.desc]))});

function Segmented<T extends string>({options, value, onChange}: {options: readonly T[]; value: T; onChange: (v: T) => void}) {
  return (
    <div className="segmented" role="group">
      {options.map((o) => (
        <button key={o} aria-pressed={o === value} onClick={() => onChange(o)}>{o}</button>
      ))}
    </div>
  );
}

/** Every word of the query appears in the row's text (its strings, station codes included). */
function matches(r: object, q: string) {
  if (!q) return true;
  const strings = Object.values(r).filter((v): v is string => typeof v === "string");
  const hay = [...strings, ...strings.map(stationCode)].join(" ").toLowerCase();
  return q.toLowerCase().split(/\s+/).every((w) => hay.includes(w));
}

/** Keeps --toolbar-h at the sticky toolbar's height, for the table header to stick under it. */
function useToolbarHeight() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => document.documentElement.style.setProperty("--toolbar-h", `${el.offsetHeight}px`));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return ref;
}

type View = "rides" | "legs";
type Tables = {rides: RideRow[]; legs: LegRow[]};

/** A run's two tables, each row with how to name its ride and the run's name. */
async function loadTables(run: string): Promise<Tables> {
  const base = runBase(run);
  const [rides, legs] = await Promise.all([loadParquet<Rides>(`${base}/rides.parquet`), loadParquet<Legs>(`${base}/legs.parquet`)]);
  const info = rideInfo(legs.filter((l) => l.variant === legs[0]?.variant));
  const add = <R extends {ride: string}>(r: R) => {
    const i = info.get(r.ride);
    return {...r, run_name: run, ride_date: i?.date ?? r.ride, ride_date_detail: i?.dateDetail ?? r.ride,
      ride_endpoints: i?.endpoints ?? "", ride_endpoints_detail: i?.endpointsDetail ?? ""};
  };
  return {rides: rides.map(add) as RideRow[], legs: legs.map(add) as LegRow[]};
}

const rideKey = (r: RideRow) => `${r.ride}|${r.variant}`;
const legKey = (r: LegRow) => `${r.ride}|${r.variant}|${r.leg}`;

export default function ResultsPage() {
  const runs = useLoad(loadRuns);
  if (runs.status === "loading") return <p className="status">Loading…</p>;
  if (runs.status === "error" || runs.value.length === 0) {
    return <div className="status error"><p>No evaluation runs yet.</p>
      {runs.status === "error" && <pre>{runs.error}</pre>}
      <p>Run <code>python -m pipeline</code> in <code>phone-sensor-fusion/</code> first.</p></div>;
  }
  return <Results runs={runs.value} />;
}

function Results({runs}: {runs: RunMeta[]}) {
  const toolbar = useToolbarHeight();
  const known = (name: string | null) => (name && runs.some((r) => r.name === name) ? name : null);
  const [run, setRun] = useState(() => known(readHash().params.get("run")) ?? runs[0].name);
  const [vs, setVs] = useState(() => known(readHash().params.get("vs")));
  const [view, setView] = useState<View>(() => (readHash().params.get("view") === "legs" ? "legs" : "rides"));
  const [variant, setVariant] = useState("all measurements");
  const [query, setQuery] = useState("");
  const [changedOnly, setChangedOnly] = useState(false);
  const [byChange, setByChange] = useState(false);
  const baseline = vs && vs !== run ? vs : null;
  useEffect(() => replaceHash(["results"], {run, ...(baseline ? {vs: baseline} : {}), ...(view === "legs" ? {view} : {})}),
    [run, baseline, view]);

  const data = useLoad(() => Promise.all([loadTables(run), baseline ? loadTables(baseline) : Promise.resolve(null)]),
    [run, baseline]);
  const [tables, baseTables] = data.status === "ok" ? data.value : [null, null];

  const variants = useMemo(() => [...new Set((tables?.rides ?? []).map((r) => r.variant))], [tables]);
  const shownVariant = variants.includes(variant) ? variant : variants[0];
  const base = useMemo(() => baseTables && {
    rides: new Map(baseTables.rides.map((r) => [rideKey(r), r])),
    legs: new Map(baseTables.legs.map((r) => [legKey(r), r])),
  }, [baseTables]);
  const pickRows = <R extends {variant: string}>(rs: R[], key: (r: R) => string, cols: Column<R>[], b?: Map<string, R>) => {
    const ofVariant = rs.filter((r) => r.variant === shownVariant);
    const shown = ofVariant.filter((r) => matches(r, query) && (!changedOnly || !b || changed(cols, r, b.get(key(r)))));
    return {shown, total: ofVariant.length};
  };
  const rides = useMemo(() => pickRows(tables?.rides ?? [], rideKey, RIDE_COLUMNS, base?.rides), [tables, base, shownVariant, query, changedOnly]);
  const legs = useMemo(() => pickRows(tables?.legs ?? [], legKey, LEG_COLUMNS, base?.legs), [tables, base, shownVariant, query, changedOnly]);
  const counts = view === "rides" ? rides : legs;

  const baseMeta = baseline ? runs.find((r) => r.name === baseline) : undefined;
  const pick = (value: string | null, onChange: (v: string) => void, none?: string) => (
    <select value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
      {none && <option value="">{none}</option>}
      {runs.map((r) => <option key={r.name} value={r.name} disabled={!!none && r.name === run}>{runLabel(r)}</option>)}
    </select>
  );

  return (
    <div className="results-page">
      <div className="toolbar" ref={toolbar}>
        <Segmented options={["rides", "legs"] as const} value={view} onChange={setView} />
        <label className="field">Run {pick(run, setRun)}</label>
        {runs.length > 1 && <label className="field">vs {pick(baseline, (v) => setVs(v || null), "no baseline")}</label>}
        {baseline && <>
          <label className="check">
            <input type="checkbox" checked={changedOnly} onChange={(e) => setChangedOnly(e.target.checked)} /> Changed only</label>
          <span className="field">
            Sort by <Segmented options={["value", "change"] as const} value={byChange ? "change" : "value"}
                               onChange={(v) => setByChange(v === "change")} /></span>
        </>}
        {variants.length > 1 && (
          <select value={shownVariant} onChange={(e) => setVariant(e.target.value)}>
            {variants.map((v) => <option key={v}>{v}</option>)}
          </select>
        )}
        <input type="search" placeholder="Search" value={query} onChange={(e) => setQuery(e.target.value)}
               />
        {tables && <span className="count">{counts.shown.length} / {counts.total}</span>}
      </div>

      {data.status === "loading" && <p className="status">Loading…</p>}
      {data.status === "error" && <div className="status error"><p>Couldn't load the run.</p><pre>{data.error}</pre></div>}
      {tables && (view === "rides" ? (
        <ResultsTable rows={rides.shown} columns={RIDE_COLUMNS} describe={describe("rides")} rowKey={rideKey} rowHref={rideRowHref}
                      base={base?.rides} baseName={baseMeta && runLabel(baseMeta)} byChange={!!baseline && byChange} />
      ) : (
        <ResultsTable rows={legs.shown} columns={LEG_COLUMNS} describe={describe("legs")} rowKey={legKey} rowHref={legRowHref}
                      base={base?.legs} baseName={baseMeta && runLabel(baseMeta)} byChange={!!baseline && byChange} />
      ))}
    </div>
  );
}
