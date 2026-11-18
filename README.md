# train-fusion

An HMM that fuses Caltrain's two 511.org GTFS-RT feeds (trip updates and vehicle
positions) into a single estimate of where each train is. Built up one small piece at a
time. So far: the archiver, and the reference trajectory that delays are measured against.

## Setup

Nix flake + direnv: `direnv allow` gives you Python 3.12 (polars, shapely, pyproj,
scipy, …), just, node, gcloud and terraform, puts `bin/` on PATH, and loads `.env.local` (which must define
`API_KEY_511_1` and `API_KEY_511_2`; `just pull-secrets` writes them from Secret Manager).

```sh
just sync                          # download what's new from the GCS archive into data/
just build 2026-11-07 2026-11-08   # derive everything (incl. the reference) for these dates
just build --last-week             # ... for every archived date among the last 7 days
just explore                       # explorer at http://127.0.0.1:3030
just test
just fetch                         # run the archiver locally (the real one runs on GCP)
```

`just sync` needs `gcloud auth application-default login` once.

## Docs

- [docs/fetcher.md](docs/fetcher.md): the archiver, data layout, and what we learned about the 511 feeds
- [docs/deploy.md](docs/deploy.md): the GCP deployment (terraform), the bucket, secrets, alerts
- [docs/pipeline.md](docs/pipeline.md): stages, tables, commands
- [docs/reference.md](docs/reference.md): the reference, how it's fitted and evaluated
- [docs/recorder.md](docs/recorder.md): the iPhone app (`ios/`) for recording audio + motion on a train, and its file format
- [phone-sensor-fusion/README.md](phone-sensor-fusion/README.md): stops, speed and position of a train from those recordings alone (separate from the GTFS-RT pipeline)
