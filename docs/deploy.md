# Deployment (GCP)

The archiver runs on a small VM and archives into a GCS bucket; `just sync` keeps a local
copy of the bucket in `data/rt` and `data/gtfs`, which the pipeline reads as before. All
of it is terraform in `infra/` (GCP project `train-fusion`).

```
VM fetcher-<hash> (e2-micro, us-west1, no external IP)
  fetch.py --bucket train-fusion-archive --metrics     (systemd: restarts on any exit)
    ├─ writes data/ on its disk, as when run locally
    ├─ uploads each finished file create-only to gs://train-fusion-archive ─┐
    └─ reports successful fetches per feed to Cloud Monitoring              │
laptop: just sync ── downloads names it doesn't have ◄──────────────────────┘
```

## Why it can't lose or clobber data

- **Every archive file is written once and never changed.** The fetch log, which used to be
  one growing `fetch_log.jsonl` per day, is now one file per minute per fetcher process
  (`fetch_log/<minute>_<instance>.jsonl`).
- **Uploads are create-only**: an object that exists is never replaced, and the VM's
  service account couldn't replace or delete one anyway (it has only create + read).
  So the bucket is the plain union of everything every fetcher wrote: a restart, a
  replaced VM, or two fetchers at once just add files. `decode` drops repeated content.
- **The VM's disk is a buffer.** Files are uploaded within ~30 s (log segments ~2 min,
  once their minute is over) and deleted locally after 2 h. On stop, including VM
  shutdown or deletion, the fetcher uploads everything it has before exiting. A VM
  stopped hard or that loses its disk loses at most the last couple of minutes.
- **Overlapping fetchers are tolerated, not prevented.** Two at once double the 511 quota
  use (60/hour/key); 511 has been permissive about that. A deploy overlaps for a minute
  or two; a lasting overlap triggers an alert. A fresh VM can't see the previous one's
  request history, so its first hour may also overlap the old one's quota window.
- **If a feed thread crashes, the whole process exits** (nonzero), and systemd restarts it,
  so one feed can't silently stop while the other keeps going.

## One-time setup

```sh
gcloud auth login
gcloud auth application-default login      # for `just sync` and terraform
gcloud config set project train-fusion

# terraform's state bucket (it can't create its own)
gcloud storage buckets create gs://train-fusion-tfstate --location=us-west1 \
    --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update gs://train-fusion-tfstate --versioning

echo 'alert_email = "you@example.com"' > infra/terraform.tfvars   # gitignored
just tf init

# secrets first, so the fetcher has its keys when it starts
just tf apply -target=google_secret_manager_secret.s
printf %s "$API_KEY_511_1" | gcloud secrets versions add API_KEY_511_1 --data-file=-
printf %s "$API_KEY_511_2" | gcloud secrets versions add API_KEY_511_2 --data-file=-

just tf apply       # everything else: bucket, network, VM, alerts
just fetcher-logs   # watch it start
```

Then move the existing local archive in. Stop the old local fetcher first: its day's
`fetch_log.jsonl` can't be updated once uploaded.

```sh
just migrate ../train-fusion/data     # uploads rt/ and gtfs/ create-only; deletes nothing
just sync                             # fills this checkout's data/ from the bucket
```

## Day to day

| | |
|---|---|
| `just sync` | download what's new (also run by `just build`) |
| `just tf plan` / `just tf apply` | deploy: any change to `fetch.py`, `fetch_cloud.py` or `infra/fetcher/` replaces the VM |
| `just fetcher-logs` | follow the VM's logs (IAP ssh) |
| `just pull-secrets` | write all secrets into `.env.local` for local runs |

`just sync` downloads only names it doesn't have. Service dates 2+ days old get a
`data/rt/<date>/.final` marker once downloaded and are never listed again, so it stays
fast as the archive grows. It warns if nothing new reached the bucket in 15 min.

## Secrets

`infra/secrets.tf` has the inventory: one Secret Manager secret per environment variable
of the same name. Terraform creates the secrets but never sees their values. To add one:
add its name to `secrets`, `just tf apply`, then
`printf %s "$VALUE" | gcloud secrets versions add NAME --data-file=-`, and give whatever
uses it access (for the fetcher, add it to `fetcher_secrets` in `fetcher.tf`). To rotate:
add a new version, then restart the fetcher (`sudo systemctl restart fetcher` on the VM, or
replace the VM with `just tf apply -replace=google_compute_instance.fetcher`).

## Alerts (email)

| Alert | Fires when |
|---|---|
| nothing written to the archive | no successful writes to the bucket for 15 min (fetcher, VM or upload dead) |
| a feed is failing | < 10 HTTP 200 fetches of a feed in 15 min (normal ~15), or no metric data at all: e.g. a revoked key, whose error bodies are still archived |
| more than one fetcher | > 22 per 15 min, sustained 30 min |

The first uses GCS's own request metric. If it misfires, check the `method` /
`response_code` label values in Metrics Explorer (`storage.googleapis.com/api/request_count`).

## Cost (approximate)

| | per month |
|---|---|
| e2-micro + 10 GB standard disk | free tier |
| Cloud NAT | ~$1 + $0.045/GB processed (~12 MB/day of feeds) |
| Storage: ~12 MB/day | ~$0.01 per month of archive (~$0.10/month after a year) |
| Uploads: ~3,700 objects/day | ~$0.55 |
| Alerts: 3 conditions | ~$0.30 ($0.10/condition since May 2026) |
| Custom metric | free (well under the free allotment) |
| Secret Manager | free (≤ 6 secret versions, 10k accesses) |
