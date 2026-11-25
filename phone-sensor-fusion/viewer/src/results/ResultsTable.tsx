// A sortable table over rows already filtered by the page, with grouped headers, the
// schema's description as each header's tooltip, and mean / median of the shown rows.
import {useMemo, useState} from "react";
import {
  createColumnHelper, flexRender, getCoreRowModel, getSortedRowModel, useReactTable,
  type ColumnDef, type SortingState,
} from "@tanstack/react-table";
import type {Column} from "./columns";
import {isNum, log10p, logText} from "../lib/format";

interface Props<R> {
  rows: R[];
  columns: Column<R>[];
  describe: Record<string, string>;
  rowKey: (r: R) => string;
}

const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
function median(xs: number[]) {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

export default function ResultsTable<R extends Record<string, any>>({rows, columns, describe, rowKey}: Props<R>) {
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
      columns: g.cols.map((c) => helper.accessor((r) => (c.sortBy ? c.sortBy(r) : r[c.key]) ?? undefined, {
        id: c.key,
        header: c.header,
        sortUndefined: "last",
        enableSorting: !c.noSort,
        meta: c,
      }) as ColumnDef<R, unknown>),
    }));
  }, [columns]);

  const table = useReactTable({
    data: rows, columns: defs, state: {sorting}, onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
    getRowId: rowKey,
  });

  const stats = useMemo(() => Object.fromEntries(columns.filter((c) => c.summary).map((c) => {
    const xs = rows.map((r) => r[c.key]).filter(isNum)
      .map((x) => (c.summary === "abs" ? Math.abs(x) : c.summary === "log" ? log10p(x) : x));
    return [c.key, xs.length ? {mean: mean(xs), median: median(xs)} : null];
  })), [rows, columns]);

  // first column of each group after the first: drawn with a divider before it
  const groupStart = new Set(columns.filter((c, i) => i > 0 && columns[i - 1].group !== c.group).map((c) => c.key));
  const cell = (c: Column<R>, x: unknown) => (x == null ? (c.nullText ?? "–") : c.format ? c.format(x) : String(x));
  const cls = (...xs: (string | false | undefined)[]) => xs.filter(Boolean).join(" ");

  return (
    <table className="results">
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
                <th key={h.id} className={cls(!c.noSort && "sortable", c.align === "left" && "left", groupStart.has(c.key) && "gs")}
                    data-tip={c.link ? undefined : describe[c.key]}
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
          <tr key={row.id}>
            {columns.map((c) => {
              const x = row.original[c.key];
              const tone = c.tone?.(x ?? null);
              return (
                <td key={c.key} className={cls(c.align === "left" ? "left" : "num", tone, x == null && "null", groupStart.has(c.key) && "gs")}>
                  {c.link ? <a className="go" href={c.link(row.original)}>{c.linkText}</a>
                    : <span data-tip={c.tip?.(row.original)}>{cell(c, x)}</span>}
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
                const text = i === 0 ? k : !s ? "" : c.summary === "log" ? logText(s[k]) : cell(c, s[k]);
                return (
                  <td key={c.key} className={cls(i === 0 ? "left label" : "num", groupStart.has(c.key) && "gs")}>
                    <span data-tip={s && c.summary === "abs" ? `${k} of |${c.header.toLowerCase()}|`
                      : s && c.summary === "log" ? `${k} of the log10 probabilities` : undefined}>
                      {s && c.summary === "abs" ? `|${text.replace(/^[+−]/, "")}|` : text}
                    </span>
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
