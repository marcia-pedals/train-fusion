# train-fusion

An HMM that fuses Caltrain's two 511.org GTFS-RT feeds (trip updates and vehicle
positions) into a single estimate of where each train is. Built up one small piece at a
time. So far: the archiver, and the reference trajectory that delays are measured against.

## Setup

Nix flake + direnv: `direnv allow` gives you Python 3.12 (polars, shapely, pyproj,
scipy, …), just and node, puts `bin/` on PATH, and loads `.env.local` (which must define
`API_KEY_511_1` and `API_KEY_511_2`).

```sh
just fetch                 # archive the feeds into data/ (runs forever)
just build 2026-11-07 2026-11-08   # derive everything (incl. the reference) for exactly these dates
just build --last-week             # ... for every archived date among the last 7 days
just test
```

## Docs

- [docs/fetcher.md](docs/fetcher.md): the archiver, data layout, and what we learned about the 511 feeds
- [docs/pipeline.md](docs/pipeline.md): stages, tables, commands
- [docs/reference.md](docs/reference.md): the reference, how it's fitted and evaluated
