# phone-sensor-fusion

Where is the train, how fast is it going, and is it at a platform, from nothing but a phone
riding on it: the microphone, accelerometer and gyroscope recorded by the
[recorder app](../docs/recorder.md). No GPS, no network.

Built from labelled rides on Caltrain's electric trains, with the phone either lying on a
surface or held in a hand. Newer recordings also carry magnetometer, barometer and iOS's
fused device-motion data; the station layer uses heading (from the fused attitude) and the
barometer when a recording has them.

## What doesn't work yet

- **On and off the train is two hand-set states, before and after** (model/README.md). The
  first door chime or the car's hum ends "before", so the model leaves it as the train
  pulls in, before the doors open; and with "stopped, doors not yet open" normally lasting
  seconds, it can invent a door cycle to fill the wait, a false opening next to the first
  stop. A long hold with the power off (no hum) makes the filter believe the rider got off.
- **Online (filter-only) estimates with the phone in a hand** lose the train for tens of
  seconds at a time when speech covers the tone at low speed. Smoothing repairs it; a fixed-lag smoother would be the online answer.
- **The hand-held ride reads long without the wheel lines.** Part of it is the tone constant differing between
  the two trainsets (wheel wear?); the rest is unexplained.
- **Wheel-rotation lines with the phone in a hand** use held tables fitted on the hand-held
  rides (two so far). Left out of training, one of them leaves too little held data, and its
  fold borrows tables from resting phones, which hurts it in cross-validation.
- **Switches and level crossings** show nothing in one-second vibration levels.

## To come back to when there is more data

- **Magnetometer as a low-speed sensor.** How fast the magnetic field changes follows
  speed below 10 m/s, where the motor tone is silent, and the rotation-proof version
  (field strength only) might work in a hand. Found on two resting-phone rides; not in the
  model. Needs: a hand-held ride with the magnetometer, and two rides over the same track
  to see whether the field also repeats by place.
  [exploration/README.md](exploration/README.md), finding 9; `python -m exploration.magnet_speed`.
- **Heading and barometer are in the station layer but only tested with the phone lying
  still.** The barometer term would work in a hand, where yaw and heading don't; no
  hand-held ride has one. Not built: the door pressure step as a door detector, heading
  for a recording started inside the train, the barometer as a position clue before the
  first stop.
- **Wheel-rotation lines with the phone in a hand**: held tables come from two hand-held
  rides, and a hand adds lines of its own (a steady 4-5 Hz one on one ride). Needs a third
  to fit and test them properly.

## Layout

The pipeline is five folders, each with its own README, used in this order:

| | | |
|---|---|---|
| [`exploration/`](exploration/README.md) | scripts that look at the raw recordings, and what they found | nothing downstream imports it |
| [`maps/`](maps/README.md) | the Caltrain line as a 1-D track model from OpenStreetMap: stations, curvature, switches, crossings | `maps/data/` checked in |
| [`labels/`](labels/README.md) | manual and semi-automatic ground truth: stations, door chimes, wheel stop/start, on/off the train, phone handling, speed | the labels themselves are in `../data/recordings/labels/<recording>/`, not checked in |
| [`features/`](features/README.md) | measurements per second, from deterministic signal processing to small learned classifiers | cached in `data/phone-sensor-fusion/features/` |
| [`model/`](model/README.md) | the HMM over (mode, speed), the station layer on top, fitting, evaluation | `params/all.json` (fitted on every ride) checked in; the cross-validation fits go to `data/` |

`app/` exports the fitted model for the phone app's online inference and checks the app's
port against this pipeline ([docs/recorder.md](../docs/recorder.md)). [`viewer/`](viewer/README.md) is a web app with the evaluation's per-ride and per-leg
tables, and each ride in detail: spectrograms, the model's beliefs and the audio on one time axis. `common/` has the recording loader (the only code that reads the raw files) and paths;
`pipeline.py` runs features → fit → infer → evaluate; `tests/` checks the inference code
against brute force.

```
recording ──► features/frames ──► features/phone_state ──► features/motion ─┐
 (audio,        raw per-second      resting / held /         along-track     │
  accel,        measurements        handled (learned)        accel, yaw      ▼
  gyro)              │                                                   model/infer ──► model/stations
                     └── features/comb (learned: speed from spectral lines) ─►  mode, speed,     station,
labels ──► model/fit ──► params/*.json ─────────────────────────────────────►  distance         position
maps ────────────────────────────────────────────────────────────────────────────────────────────►
```

## Running it

Inside the repo's nix shell (`direnv allow`; or prefix commands with `bin/psf-dev`), from
this directory. Recordings are in `../data/recordings/raw/<id>/` and labels in `../data/recordings/labels/<id>/`:
`just sync-recordings` fetches both from the bucket (and uploads new labels).

```sh
python -m pipeline              # features if missing, then leave-one-recording-out: fit, infer, evaluate; kept as a run (--name)
python -m pipeline --ablate     # ... also with each measurement removed / alone
python -m pipeline --final      # fit on every labelled recording -> params/all.json
python -m model.plot            # result plots -> data/phone-sensor-fusion/model/<id>.cv.png
python -m model.schemas && (cd viewer && npm install && npm run dev)   # the results viewer, http://127.0.0.1:3040
python -m pytest -q tests
```

(`just psf [--ablate|--final]` and `just psf-view` from the repo root do the same.)

To add a recording: label it ([labels/README.md](labels/README.md): about ten minutes with
the proposal scripts when the chimes are clean), then rerun the pipeline.

Rebuilding the map (only if OpenStreetMap changed): `python -m maps.fetch_osm && python -m maps.build_track`.

## Design choices that keep later steps open

- **Magnetometer, barometer.** A new sensor is a new array in `features/frames.py` and a
  new emission term in `model/emissions.py` (heading against the track's heading at the
  believed position, pressure against elevation). The station layer already carries the
  position needed to look those up; `maps/data/profile.csv` already has heading (elevation
  is not downloaded yet). Nothing else changes. Two things were deliberately *not* built
  because a magnetometer makes them easy: a causal estimate of the phone's orientation
  (`features/motion.py` uses whole resting stretches), and anything for curves with the
  phone in a hand.
- **Online, on the phone.** All per-second features need at most a few seconds of signal;
  the phone-state classifier is a forward filter; the HMM has 157 states and its forward
  step is one 157 × 157 matrix product per second; the station layer updates once per stop.
  `v_filt` in the results is exactly what a live filter would have reported. Not causal
  yet: the orientation in `features/motion.py`, and the two-pass estimate of the
  accelerometer's sign and offset in `model/infer.py` (both become running estimates).
- **Labels can't leak into the test.** Speed labels use the map (each leg is scaled to its
  map distance), so the map distance of a *test* leg is never seen by the model that is
  tested on it: models are always fitted on other recordings.
