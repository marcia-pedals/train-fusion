// Loading what the pipeline wrote (served by the dev server under /data, vite.config.ts).
import {useEffect, useState} from "react";
import {parquetReadObjects} from "hyparquet";
import {compressors} from "hyparquet-compressors";

/** A Parquet file as plain objects; Int64 columns arrive as BigInt and become numbers. */
export async function loadParquet<T>(url: string): Promise<T[]> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status} ${await res.text()}`);
  const rows = await parquetReadObjects({file: await res.arrayBuffer(), compressors});
  for (const r of rows) for (const k in r) if (typeof r[k] === "bigint") r[k] = Number(r[k]);
  return rows as T[];
}

export async function loadJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status} ${await res.text()}`);
  return res.json();
}

export type Loaded<T> = {status: "loading"} | {status: "error"; error: string} | {status: "ok"; value: T};

export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({status: "loading"});
  useEffect(() => {
    let live = true;
    setState({status: "loading"});
    load().then((value) => live && setState({status: "ok", value}),
      (e) => live && setState({status: "error", error: String(e?.message ?? e)}));
    return () => { live = false; };
  }, deps);  // eslint-disable-line react-hooks/exhaustive-deps
  return state;
}
