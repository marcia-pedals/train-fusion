// The address after "#": a page path and its parameters, e.g. "#/ride/<id>?t0=10&t1=90".
import {useEffect, useState} from "react";

export interface Hash { path: string[]; params: URLSearchParams }

export function readHash(): Hash {
  const [p, q = ""] = window.location.hash.replace(/^#\/?/, "").split("?");
  return {path: p.split("/").filter(Boolean).map(decodeURIComponent), params: new URLSearchParams(q)};
}

export function useHash(): Hash {
  const [hash, setHash] = useState(readHash);
  useEffect(() => {
    const on = () => setHash(readHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}

/** Change the address without adding to the history or re-routing. */
export function replaceHash(path: string[], params: Record<string, string>) {
  const q = new URLSearchParams(params).toString();
  history.replaceState(null, "", `#/${path.map(encodeURIComponent).join("/")}${q ? `?${q}` : ""}`);
}

/** The ride page for a run's inference (by default the newest run's), whole or zoomed to [t0, t1]. */
export function rideHref(id: string, run?: string, t0?: number, t1?: number) {
  const q = new URLSearchParams({...(run ? {run} : {}),
    ...(t0 != null && t1 != null ? {t0: t0.toFixed(1), t1: t1.toFixed(1)} : {})}).toString();
  return `#/ride/${encodeURIComponent(id)}${q ? `?${q}` : ""}`;
}
