# train-fusion commands. Run inside the nix dev shell (direnv does this for you).

default:
    @just --list

# rebuild everything in data/derived for exactly these service dates (or --last-week)
build +args:
    python -m train_fusion.build {{args}}

# run the archiver
fetch:
    python fetch.py

test:
    python -m pytest -q tests
