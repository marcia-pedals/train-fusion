// The toolbar's "Columns" menu: which of a table's columns are shown, by group, remembered
// per table in localStorage.
import {useEffect, useRef, useState} from "react";

const STORE = "psf-viewer.hidden-columns";

/** Hidden column keys per table ("rides", "legs"), and a setter that remembers them. */
export function useHiddenColumns(): [Record<string, string[]>, (table: string, hidden: string[]) => void] {
  const [hidden, setHidden] = useState<Record<string, string[]>>(() => {
    try {
      return JSON.parse(localStorage.getItem(STORE) ?? "{}");
    } catch {
      return {};
    }
  });
  const set = (table: string, keys: string[]) => setHidden((h) => {
    const next = {...h, [table]: keys};
    localStorage.setItem(STORE, JSON.stringify(next));
    return next;
  });
  return [hidden, set];
}

/** What the menu needs of a column (results/columns.ts) */
interface PickerColumn { key: string; header: string; group: string }

interface Props {
  columns: PickerColumn[];
  hidden: string[];
  onChange: (hidden: string[]) => void;
}

export default function ColumnPicker({columns, hidden, onChange}: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  const groups: {name: string; cols: PickerColumn[]}[] = [];
  for (const c of columns) {
    const last = groups[groups.length - 1];
    if (last?.name === c.group) last.cols.push(c);
    else groups.push({name: c.group, cols: [c]});
  }
  const isHidden = new Set(hidden);
  const shown = columns.filter((c) => !isHidden.has(c.key)).length;
  const toggle = (keys: string[], show: boolean) => {
    const next = new Set(hidden);
    for (const k of keys) show ? next.delete(k) : next.add(k);
    onChange([...next]);
  };

  return (
    <div className="column-picker" ref={ref}>
      <button className="picker-button" aria-expanded={open} onClick={() => setOpen(!open)}>
        Columns{shown < columns.length ? ` ${shown}/${columns.length}` : ""} <span className="caret">▾</span>
      </button>
      {open && (
        <div className="picker-menu">
          {groups.map((g) => {
            const keys = g.cols.map((c) => c.key);
            const n = keys.filter((k) => !isHidden.has(k)).length;
            return (
              <div key={g.name} className="picker-group">
                <label className="picker-group-name">
                  <input type="checkbox" checked={n === keys.length} ref={(el) => { if (el) el.indeterminate = n > 0 && n < keys.length; }}
                         onChange={(e) => toggle(keys, e.target.checked)} />
                  {g.name}
                </label>
                {g.cols.map((c) => (
                  <label key={c.key}>
                    <input type="checkbox" checked={!isHidden.has(c.key)} onChange={(e) => toggle([c.key], e.target.checked)} />
                    {c.header}
                  </label>
                ))}
              </div>
            );
          })}
          <button className="picker-reset" disabled={shown === columns.length} onClick={() => onChange([])}>Show all</button>
        </div>
      )}
    </div>
  );
}
