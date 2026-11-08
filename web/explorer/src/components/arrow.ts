// Helpers for the Parquet files the data loaders serve.

/** Arrow table -> plain objects. Int64 columns arrive as BigInt; make them numbers. */
export function rows(table: Iterable<any>): any[] {
  return Array.from(table, (r) => {
    const o = r.toJSON();
    for (const k in o) if (typeof o[k] === "bigint") o[k] = Number(o[k]);
    return o;
  });
}
