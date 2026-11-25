// The evaluation's two tables (model/evaluate.py): one row per test ride, or per leg.
import {useEffect, useMemo, useRef, useState} from "react";
import {TABLES, type Legs, type Rides} from "../generated/tables";
import {loadParquet, useLoad} from "../lib/data";
import {rideInfo} from "../lib/rides";
import {stationCode} from "../lib/stations";
import {EXTRA_DESCRIPTIONS, LEG_COLUMNS, RIDE_COLUMNS, type LegRow, type RideRow} from "./columns";
import ResultsTable from "./ResultsTable";

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

export default function ResultsPage() {
  const data = useLoad(() => Promise.all([
    loadParquet<Rides>("/data/eval/rides.parquet"),
    loadParquet<Legs>("/data/eval/legs.parquet"),
  ]));
  if (data.status === "loading") return <p className="status">Loading…</p>;
  if (data.status === "error") {
    return <div className="status error"><p>Couldn't load the results.</p><pre>{data.error}</pre>
      <p>Run <code>python -m pipeline</code> in <code>phone-sensor-fusion/</code> first.</p></div>;
  }
  const [rides, legs] = data.value;
  return <Results rides={rides} legs={legs} />;
}

function Results({rides: rawRides, legs: rawLegs}: {rides: Rides[]; legs: Legs[]}) {
  const toolbar = useToolbarHeight();
  const [rides, legs] = useMemo(() => {
    const info = rideInfo(rawLegs.filter((l) => l.variant === rawLegs[0]?.variant));
    const add = <R extends {ride: string}>(r: R) => {
      const i = info.get(r.ride);
      return {...r, ride_date: i?.date ?? r.ride, ride_date_detail: i?.dateDetail ?? r.ride,
        ride_endpoints: i?.endpoints ?? "", ride_endpoints_detail: i?.endpointsDetail ?? ""};
    };
    return [rawRides.map(add) as RideRow[], rawLegs.map(add) as LegRow[]];
  }, [rawRides, rawLegs]);
  const variants = useMemo(() => [...new Set(rides.map((r) => r.variant))], [rides]);
  const [view, setView] = useState<"rides" | "legs">("rides");
  const [variant, setVariant] = useState(variants.includes("all measurements") ? "all measurements" : variants[0]);
  const [query, setQuery] = useState("");

  const ofVariant = <R extends {variant: string}>(rs: R[]) => rs.filter((r) => r.variant === variant);
  const shownRides = useMemo(() => ofVariant(rides).filter((r) => matches(r, query)), [rides, variant, query]);
  const shownLegs = useMemo(() => ofVariant(legs).filter((r) => matches(r, query)), [legs, variant, query]);
  const total = (view === "rides" ? ofVariant(rides) : ofVariant(legs)).length;
  const shown = view === "rides" ? shownRides.length : shownLegs.length;

  return (
    <div className="results-page">
      <div className="toolbar" ref={toolbar}>
        <Segmented options={["rides", "legs"] as const} value={view} onChange={setView} />
        {variants.length > 1 && (
          <select value={variant} onChange={(e) => setVariant(e.target.value)} data-tip="Which measurements the model used">
            {variants.map((v) => <option key={v}>{v}</option>)}
          </select>
        )}
        <input type="search" placeholder="Search" value={query} onChange={(e) => setQuery(e.target.value)}
               data-tip="Rows whose text has every word: dates, station names or codes" />
        <span className="count">{shown} / {total}</span>
      </div>

      {view === "rides" ? (
        <ResultsTable rows={shownRides} columns={RIDE_COLUMNS} describe={describe("rides")}
                      rowKey={(r) => `${r.ride}|${r.variant}`} />
      ) : (
        <ResultsTable rows={shownLegs} columns={LEG_COLUMNS} describe={describe("legs")}
                      rowKey={(r) => `${r.ride}|${r.variant}|${r.leg}`} />
      )}
    </div>
  );
}
