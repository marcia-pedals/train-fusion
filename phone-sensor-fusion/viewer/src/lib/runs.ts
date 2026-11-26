// The evaluation runs the pipeline kept (pipeline.py), listed by the dev server (vite.config.ts).
import {loadJson} from "./data";

export interface RunMeta {
  name: string;
  named: boolean;  // given with --name (else the name is the start time)
  written: string;  // ISO time
  ablate: boolean;
}

export const loadRuns = () => loadJson<RunMeta[]>("/runs.json");

/** A run's folder as served. */
export const runBase = (name: string) => `/data/eval/runs/${encodeURIComponent(name)}`;

/** "my-change · <day month time>", or just the time for an unnamed run, for a run picker. */
export function runLabel(r: RunMeta) {
  const when = new Date(r.written).toLocaleString(undefined, {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"});
  return (r.named ? `${r.name} · ${when}` : when) + (!r.named && r.ablate ? " · ablate" : "");
}
