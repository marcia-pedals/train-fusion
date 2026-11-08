# train-fusion commands. Run inside the nix dev shell (direnv does this for you).

default:
    @just --list

# rebuild everything in data/derived for exactly these service dates (or --last-week)
build +args:
    python -m train_fusion.build {{args}}

# generate web/packages/types/tables.ts (not checked in) from the stages' schemas.py
types:
    python -m train_fusion.gen_types

# explorer dev server on whatever is in data/derived
explore: types
    cd web/explorer && rm -rf src/.observablehq/cache && npm install --no-audit --no-fund && npm run dev

# run the archiver
fetch:
    python fetch.py

test:
    python -m pytest -q tests
