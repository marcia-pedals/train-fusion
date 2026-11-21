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

# generate the phone recorder's Xcode project (ios/) and open it: docs/recorder.md
# (without ios/Config.xcconfig from `just ios-config`, a placeholder: the app then can't
# upload or sign in)
ios:
    [ -f ios/Config.xcconfig ] || printf 'API_HOST = example.invalid\nIDENTITY_API_KEY =\nGOOGLE_IOS_CLIENT_ID =\nGOOGLE_REVERSED_CLIENT_ID =\n' > ios/Config.xcconfig
    cd ios && xcodegen && open TrainRecorder.xcodeproj

# write the fitted model where the app bundles it (ios/TrainRecorder/model.json), and the
# reference features that bin/ios-sim-replay compares the app against
ios-model:
    cd phone-sensor-fusion && python -m app.export

# write ios/Config.xcconfig (gitignored): the API host and the sign-in settings the app
# builds with, from terraform's outputs. Then `just ios` and rebuild.
ios-config:
    #!/usr/bin/env bash
    set -euo pipefail
    url=$(terraform -chdir=infra output -raw api_url)
    key=$(terraform -chdir=infra output -raw identity_api_key)
    # from terraform.tfvars, not an output: a targeted apply (docs/deploy.md) doesn't record
    # outputs that only repeat a variable
    client=$(echo 'var.google_ios_client_id' | terraform -chdir=infra console | tr -d '"')
    [ -n "$client" ] || { echo "set google_ios_client_id in infra/terraform.tfvars and apply (docs/deploy.md)" >&2; exit 1; }
    host=${url#https://}; host=${host%/}
    # no "//" anywhere: xcconfig starts a comment there, hence a host and not a URL
    printf '%s\n' \
        "API_HOST = $host" \
        "IDENTITY_API_KEY = $key" \
        "GOOGLE_IOS_CLIENT_ID = $client" \
        "GOOGLE_REVERSED_CLIENT_ID = com.googleusercontent.apps.${client%.apps.googleusercontent.com}" \
        > ios/Config.xcconfig
    echo "wrote ios/Config.xcconfig (API at $host)"

# build backend/ with Cloud Build and deploy it to the Cloud Run service `api` (infra/api.tf)
api-deploy:
    #!/usr/bin/env bash
    set -euo pipefail
    tf() { terraform -chdir=infra output -raw "$1"; }
    image="$(tf api_image_repository)/api:$(date -u +%Y%m%dT%H%M%S)-$(git rev-parse --short HEAD)$(git diff --quiet HEAD -- backend || echo -dirty)"
    gcloud builds submit backend --project train-fusion --region us-west1 \
        --config backend/cloudbuild.yaml --substitutions "_IMAGE=$image" \
        --service-account "$(tf api_builder_service_account)" \
        --gcs-source-staging-dir "gs://$(tf api_builds_bucket)/source"
    gcloud run services update api --project train-fusion --region us-west1 --image "$image"
    curl -fsS "$(tf api_url)/health" && echo " <- $(tf api_url)"

# recordings people asked to have deleted, with the commands to delete each by hand;
# `just recording-deletions --done <request id>` once you have (backend/admin.py)
recording-deletions *args:
    python backend/admin.py deletions {{args}}

# refuse an account: disable it in Identity Platform and mark it blocked in the API
block-account uid:
    python backend/admin.py block {{uid}}

# phone recordings: download new ones from the bucket's raw/, sync labels/ both ways
# (phone-sensor-fusion/common/sync.py); e.g. `just sync-recordings --dry-run`
sync-recordings *args:
    cd phone-sensor-fusion && python -m common.sync {{args}}

# phone-sensor-fusion: features -> fit -> infer -> evaluate, leaving each recording out in turn
# (phone-sensor-fusion/README.md); e.g. `just psf --ablate`, `just psf --final`
psf *args:
    cd phone-sensor-fusion && python -m pipeline {{args}}

# phone-sensor-fusion viewer at http://127.0.0.1:3040: features, filtered and smoothed inference, audio
psf-view:
    cd phone-sensor-fusion && python -m viewer.export && python -m viewer.serve

test:
    python -m pytest -q tests
    cd phone-sensor-fusion && python -m pytest -q tests
    python -m pytest -q backend/tests
