# Deployment (GCP)

The archiver runs on a small VM and archives into a GCS bucket; `just sync` keeps a local
copy of the bucket in `data/rt` and `data/gtfs`, which the pipeline reads. All
of it is terraform in `infra/` (GCP project `train-fusion`).

```
VM fetcher-<hash> (e2-micro, us-west1, no external IP)
  python -m fetchers.gtfs.fetch --bucket train-fusion-archive --metrics   (systemd: restarts on any exit)
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
| `just sync` | download what's new (`just build` doesn't, so builds use a stable snapshot until you sync) |
| `just tf plan` / `just tf apply` | deploy: any change under `fetchers/` or `infra/fetcher/` replaces the VM |
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

## The recorder API (`backend/`)

The phone app's backend ([api.md](api.md); why it exists: [accounts-and-uploads.md](accounts-and-uploads.md)):
accounts (Identity Platform: Sign in with Apple, Google, emailed link), Train
Measurements (Firestore) and signed upload URLs for `gs://train-fusion-recordings/raw/`. Terraform in
`infra/api.tf`.

```
app ── ID token (Identity Platform) ─┐
    ── X-Device-Secret ──────────────┤
                                     ▼
Cloud Run service `api` (scale to zero, ≤ 2 instances, SA recorder-api)
  ├─ Firestore (default) database, us-west1: users, measurements, uploads, deletion requests, counters
  ├─ signs V4 PUT URLs with IAM signBlob (as itself) ──► app PUTs files to raw/<upload id>/
  ├─ lists raw/<upload id>/ (names and sizes) to check an upload is complete
  └─ deletes Identity Platform users (account deletion, refused signups)
```

What terraform makes: the APIs; the Firestore database; the service (with a placeholder
image: `just api-deploy` builds and deploys the real one, and terraform ignores the image
from then on) and its service account, which may create objects under `raw/` (never
replace or delete), list the bucket's names and sizes, sign as itself, use Firestore and
delete Identity Platform users, and nothing else; Identity Platform with email link sign-in
and the API's host as an authorized domain; the app's API key (restricted to the Identity
Toolkit and Secure Token APIs, and to the app's bundle id); and what `just api-deploy`
builds with (an Artifact Registry repo `api`, a staging bucket, and a build service account
that can only read that bucket, push to that repo and write logs).

Settings are environment variables on the service, set in `api.tf` (limits, the bucket) and `terraform.tfvars` (`contact_email`, shown on `/privacy`); change them
with `just tf apply`.

### One-time setup

In this order. Older builds of the app keep uploading as before, with the `recorder`
service account's key (`infra/recordings.tf`): that account stays until you remove it,
which cuts those builds off.

1. **Google OAuth clients** (Google Cloud console, project train-fusion):
   - *Google Auth Platform → Branding*: app name "Train Fusion", support email (the
     privacy policy URL, `https://train-fusion.web.app/privacy`, once step 3 has made the API). *Audience*:
     External, published.
   - *Clients → Create*: a **Web application** client, with authorized redirect URI
     `https://train-fusion.firebaseapp.com/__/auth/handler` (for Identity Platform's Google
     provider, and a future web app), and an **iOS** client with bundle id
     `io.github.marcia-pedals.TrainFusion` (for the app).
2. `infra/terraform.tfvars` (gitignored): add `contact_email = "..."` (the privacy policy's
   contact address) and `google_ios_client_id = "<the iOS client's id>"`.
3. Make the API and everything around it:

   ```sh
   just tf apply
   just api-deploy                # build backend/ and deploy it; ends by checking /health
   ```

   (Check from outside with `/health`: Cloud Run's front end answers `/healthz` itself.)
4. **Providers** (Identity Platform → Providers; configured by hand, not in terraform, so
   their secrets stay out of the state):
   - **Google**: the web client's id and secret (step 1).
   - **Apple**: first, in the Apple Developer account (team 4Q3J7GB2S8):
     - the App ID `io.github.marcia-pedals.TrainFusion` has the Sign in with Apple
       capability;
     - a **Services ID** (e.g. `io.github.marcia-pedals.TrainFusion.web`) with Sign in
       with Apple, domain `train-fusion.firebaseapp.com`, return URL
       `https://train-fusion.firebaseapp.com/__/auth/handler`;
     - a **Key** with Sign in with Apple: note its Key ID, download the `.p8` (once);
     - so that emailed sign-in links reach people who hid their address from Apple (an
       `@privaterelay.appleid.com` address): *Services → Sign in with Apple for Email
       Communication*: add the sender, `noreply@train-fusion.firebaseapp.com`.

     Then the provider: client id = the Services ID; for iOS, bundle id
     `io.github.marcia-pedals.TrainFusion`; OAuth code flow: Team ID `4Q3J7GB2S8`, the
     Key ID and the `.p8`'s contents. The code flow settings let Identity Platform revoke
     a user's Apple tokens when they delete their account (Apple requires that), and are
     what web and Android sign-in will use later.
   - **Email link** is on already (terraform: email, no password). The emailed link goes
     through `train-fusion.firebaseapp.com` (Identity Platform won't let this project change
     that) and lands on `<api_url>/auth/email-link`, whose "Open in Train Fusion" button
     opens the app (`trainfusion://`).
5. `just ios-config` (writes `ios/Config.xcconfig`, gitignored: the API host, the API key,
   the Google client ids, the site host for the privacy policy), `just ios`, build, and install on every phone that records.
   The bundle id changed (`…TrainRecorder` → `…TrainFusion`), so this installs as a second
   app next to the old one, with its own, empty storage. Before deleting the old app, move
   its recordings over: Finder → the phone → Files → copy `recordings/` out of the old app
   and into the new one (or upload them from the old app first).

### Day to day

| | |
|---|---|
| `just api-deploy` | build `backend/` (Cloud Build) and deploy it |
| `just tf apply` | change the service's settings (env vars in `api.tf`), scaling, IAM |
| `gcloud run services logs tail api --region us-west1` | its logs (never tokens or device secrets) |
| `just deletions` | pending deletion requests (recordings and Train Measurements), and what to run for each |
| `just block-account <uid>` | refuse an account |
| `just test` | includes the backend's tests (against in-memory fakes, no GCP) |

**Deletion requests** (someone asked for their recordings or Train Measurements to be
deleted, or deleted their account): `just deletions` lists each pending request with what it
covers and what to run. Nothing is deleted automatically: for Train Measurements run
`just delete-measurements <request id>` (it deletes exactly the ones that request lists);
for uploads, the `gcloud storage rm -r gs://train-fusion-recordings/raw/<upload id>/`
commands, and also labels made from those uploads (in the bucket's `labels/` and
`RIDES.md`) and any laptop copies (`data/recordings/`). Then
`just deletions --done <request id>` (it refuses while that request's measurements are
still there). Requests promise deletion within 30
days. Deleted objects stay recoverable for the bucket's 7-day soft-delete window. Upload
URLs handed out shortly before a request still work for up to 12 hours; the listing says
when a request is that recent.

**Blocking an account** (someone abusing the shared upload limits): find its uid (the
`uid` of its uploads in Firestore, or Identity Platform's user list) and
`just block-account <uid>`: this disables the user in Identity Platform (no new tokens)
and sets `users/<uid>.blocked`, so the API refuses it at once with `403 blocked`. Uploads
without an account can't be blocked individually: the shared daily limit is the
protection (accounts-and-uploads.md says why).

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
| Recorder API: Cloud Run, Firestore, Identity Platform, Cloud Build, Artifact Registry | free at this scale (all within free tiers: scale to zero, < 50k monthly users, a few builds) |
| Recordings bucket | ~$0.02/GB-month stored; uploads are free (ingress) |
