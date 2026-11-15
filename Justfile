# train-fusion commands. Run inside the nix dev shell (direnv does this for you).

default:
    @just --list

# download what's new in the GCS archive into data/rt and data/gtfs
sync:
    python -m train_fusion.sync

# rebuild everything in data/derived for exactly these service dates (or --last-week), from
# the local archive as it is (no sync: run `just sync` when you want new data)
build +args:
    python -m train_fusion.build {{args}}

# generate web/packages/types/tables.ts (not checked in) from the stages' schemas.py
types:
    python -m train_fusion.gen_types

# explorer dev server on whatever is in data/derived
explore: types
    cd web/explorer && rm -rf src/.observablehq/cache && npm install --no-audit --no-fund && npm run dev

# run the archiver locally, into data/ (the deployed one runs on a VM: docs/deploy.md)
fetch:
    python -m fetchers.gtfs.fetch

# run the webcam archiver locally, into data/cams (docs/cams.md; pass windows need fresh trip updates in data/rt)
fetch-cams *args:
    python -m fetchers.cams.fetch {{args}}

# terraform in infra/, e.g. `just tf plan`, `just tf apply` (deploys the fetcher)
tf *args:
    terraform -chdir=infra {{args}}

# follow the fetcher VM's logs
fetcher-logs:
    gcloud compute ssh "$(terraform -chdir=infra output -raw fetcher_name)" --tunnel-through-iap \
        --zone "$(terraform -chdir=infra output -raw zone)" --project train-fusion \
        -- sudo journalctl -u google-startup-scripts -u fetcher -f

# write every Secret Manager secret into .env.local (other lines kept)
pull-secrets:
    pull-secrets

# one-off: upload an existing local archive (e.g. ../train-fusion/data) to the bucket
migrate dir:
    python -m fetchers.cloud migrate --data-dir {{dir}} --bucket train-fusion-archive

test:
    python -m pytest -q tests
