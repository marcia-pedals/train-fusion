// A sortable table over rows already filtered by the page, with grouped headers, the
// schema's description as each header's tooltip, and mean / median of the shown rows.
// With a baseline run, each metric also shows what it was there, under it, and its colour
// says how it changed (green: better, red: worse; none: no change worth counting) instead
// of how worrying it is; and the columns can sort by the change instead.
// Each row is a link (`rowHref`): click to go, ⌘/ctrl-click or middle-click for a new tab.
import {useMemo, useState, type MouseEvent} from "react";
import {
  createColumnHelper, flexRender, getCoreRowModel, getSortedRowModel, useReactTable,
  type ColumnDef, type SortingState,
} from "@tanstack/react-table";
import type {Column} from "./columns";
import {isNum, log10p, logText} from "../lib/format";
import {compare} from "./diff";

interface Props<R> {
  rows: R[];
  columns: Column<R>[];
  describe: Record<string, string>;
  rowKey: (r: R) => string;
  rowHref: (r: R) => string;
  /** the baseline run's rows, by rowKey; and its name */
  base?: Map<string, R>;
  baseName?: string;
  /** sort metric columns by their change from the baseline instead of by value */
  byChange?: boolean;
}

const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
function median(xs: number[]) {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** Follow a row's link, unless the click was the end of selecting text in it. */
function follow(e: MouseEvent, href: string) {
  if (e.button === 2 || window.getSelection()?.toString()) return;
  if (e.metaKey || e.ctrlKey || e.button === 1) window.open(href, "_blank");
  else window.location.hash = href;
}

export default function ResultsTable<R extends Record<string, any>>({rows, columns, describe, rowKey, rowHref, base, baseName, byChange}: Props<R>) {
  const [sorting, setSorting] = useState<SortingState>([]);

  const defs = useMemo(() => {
    const helper = createColumnHelper<R>();
    const groups: {name: string; cols: Column<R>[]}[] = [];
    for (const c of columns) {
      const last = groups[groups.length - 1];
      if (last?.name === c.group) last.cols.push(c);
      else groups.push({name: c.group, cols: [c]});
    }
    return groups.map((g) => helper.group({
      id: g.name,
      header: g.name,
      columns: g.cols.map((c) => helper.accessor((r) => {
        if (byChange && base && c.diff) {
          const b = base.get(rowKey(r));
          return b ? compare(c, r[c.key], b[c.key]).delta : undefined;
        }
        return (c.sortBy ? c.sortBy(r) : r[c.key]) ?? undefined;
      }, {
        id: c.key,
        header: c.header,
        sortUndefined: "last",
        meta: c,
      }) as ColumnDef<R, unknown>),
    }));
  }, [columns, base, byChange, rowKey]);

  const table = useReactTable({
    data: rows, columns: defs, state: {sorting}, onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
    getRowId: rowKey,
  });

  const summarise = (rs: R[]) => Object.fromEntries(columns.filter((c) => c.summary).map((c) => {
    const xs = rs.map((r) => r[c.key]).filter(isNum)
      .map((x) => (c.summary === "abs" ? Math.abs(x) : c.summary === "log" ? log10p(x) : x));
    return [c.key, xs.length ? {mean: mean(xs), median: median(xs)} : null];
  }));
  const stats = useMemo(() => summarise(rows), [rows, columns]);
  // the baseline's summary over the same rows (those it has)
  const baseRows = useMemo(() => (base ? rows.map((r) => base.get(rowKey(r))).filter((r): r is R => !!r) : []), [rows, base, rowKey]);
  const baseStats = useMemo(() => (base ? summarise(baseRows) : null), [baseRows, columns]);
  const missing = base ? rows.length - baseRows.length : 0;

  // first column of each group after the first: drawn with a divider before it
  const groupStart = new Set(columns.filter((c, i) => i > 0 && columns[i - 1].group !== c.group).map((c) => c.key));
  const cell = (c: Column<R>, x: unknown) => (x == null ? (c.nullText ?? "–") : c.format ? c.format(x) : String(x));
  const cls = (...xs: (string | false | undefined)[]) => xs.filter(Boolean).join(" ");

  return (
    <table className={base ? "results vs" : "results"}>
      <thead>
        {table.getHeaderGroups().map((hg, depth) => (
          <tr key={hg.id} className={depth === 0 ? "groups" : "heads"}>
            {hg.headers.map((h) => {
              if (depth === 0) {
                return <th key={h.id} colSpan={h.colSpan} className={cls("group", h.index > 0 && "gs")}>
                  {h.isPlaceholder ? null : String(h.column.columnDef.header)}</th>;
              }
              const c = h.column.columnDef.meta as Column<R>;
              const dir = h.column.getIsSorted();
              return (
                <th key={h.id} className={cls("sortable", c.align === "left" && "left", groupStart.has(c.key) && "gs")}
                    data-tip={describe[c.key]}
                    onClick={h.column.getToggleSortingHandler()}
                    aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}>
                  {flexRender(h.column.columnDef.header, h.getContext())}
                  <span className="sort">{dir === "asc" ? "▲" : dir === "desc" ? "▼" : ""}</span>
                </th>
              );
            })}
          </tr>
        ))}
      </thead>
      <tbody>
        {table.getRowModel().rows.map((row) => (
          <tr key={row.id} className="link" onClick={(e) => follow(e, rowHref(row.original))}
              onAuxClick={(e) => e.button === 1 && follow(e, rowHref(row.original))}>
            {columns.map((c, i) => {
              const x = row.original[c.key];
              const b = base?.get(row.id);
              const change = b && c.diff ? compare(c, x, b[c.key]).change : undefined;
              // with a baseline, a metric is coloured by its change, not by its value
              const color = base && c.diff ? (change === "better" || change === "worse" ? change : undefined) : c.tone?.(x ?? null);
              return (
                <td key={c.key} className={cls(c.align === "left" ? "left" : "num", color, x == null && "null", groupStart.has(c.key) && "gs")}>
                  <span className="now" data-tip={c.tip?.(row.original)}>{cell(c, x)}</span>
                  {base && i === 0 && !b && <span className="prev new" data-tip={`Not in ${baseName}`}>new</span>}
                  {change && <Prev change={change} text={cell(c, b![c.key])} now={cell(c, x)} baseName={baseName} />}
                </td>
              );
            })}
          </tr>
        ))}
        {rows.length === 0 && (
          <tr><td className="empty" colSpan={columns.length}>No rows match.</td></tr>
        )}
      </tbody>
      {rows.length > 1 && (
        <tfoot>
          {(["mean", "median"] as const).map((k) => (
            <tr key={k}>
              {columns.map((c, i) => {
                const s = stats[c.key];
                const text = (s: {mean: number; median: number} | null) => {
                  const t = !s ? "" : c.summary === "log" ? logText(s[k]) : cell(c, s[k]);
                  return s && c.summary === "abs" ? `|${t.replace(/^[+−]/, "")}|` : t;
                };
                const bs = baseStats?.[c.key];
                // summaries of |x| are compared as they are; of log10 p, a factor of 10 is a change
                const sc = c.summary === "abs" ? {...c, diff: c.diff && {...c.diff, badness: undefined}}
                  : c.summary === "log" ? {...c, diff: {eps: 1}} : c;
                const change = s && bs && c.diff ? compare(sc, s[k], bs[k]).change : undefined;
                return (
                  <td key={c.key} className={cls(i === 0 ? "left label" : "num", (change === "better" || change === "worse") && change,
                                                 groupStart.has(c.key) && "gs")}>
                    <span className="now" data-tip={s && c.summary === "abs" ? `${k} of |${c.header.toLowerCase()}|`
                      : s && c.summary === "log" ? `${k} of the log10 probabilities` : undefined}>
                      {i === 0 ? k : text(s)}
                    </span>
                    {change && <Prev baseName={baseName} text={text(bs!)} now={text(s!)} change={change} />}
                    {i === 0 && missing > 0 && <span className="prev" data-tip={`${missing} of these rows are not in ${baseName}: its summary leaves them out`}>
                      {missing} new</span>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tfoot>
      )}
    </table>
  );
}

/** Under a value: what it was in the baseline ("=" if the same). */
function Prev({change, text, now, baseName}: {change: ReturnType<typeof compare>["change"]; text: string; now: string; baseName?: string}) {
  // a change too small to count that doesn't show in the digits either: as good as the same
  if (change === "same" || (change === "small" && text === now)) return <span className="prev same" data-tip={`The same in ${baseName}`}>=</span>;
  return <span className="prev" data-tip={`In ${baseName}`}>{text}</span>;
}
