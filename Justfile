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
ios:
    cd ios && xcodegen && open TrainRecorder.xcodeproj

# write the fitted model where the app bundles it (ios/TrainRecorder/model.json), and the
# reference features that bin/ios-sim-replay compares the app against
ios-model:
    cd phone-sensor-fusion && python -m app.export

# one-off: create the upload-only key the app bundles (gitignored). Then `just ios` and rebuild.
ios-key:
    gcloud iam service-accounts keys create ios/TrainRecorder/uploader-key.json \
        --iam-account "$(terraform -chdir=infra output -raw recorder_service_account)" --project train-fusion

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
