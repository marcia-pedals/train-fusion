# phone-sensor-fusion

Where is the train, how fast is it going, and is it at a platform, from nothing but a phone
riding on it: the microphone, accelerometer and gyroscope recorded by the
[recorder app](../docs/recorder.md). No GPS, no network.

Built from four rides on Caltrain's electric trains (ten legs each; called A–D here,
and which trains they were is deliberately not in the repo): three with the phone lying on a surface, one with it in
a hand next to a conversation. The two newer recordings also carry magnetometer,
barometer and iOS's fused device-motion data; the station layer uses heading (from the
fused attitude) and the barometer when a recording has them.

## Results

Four rides, A–D, ten legs each. Each is scored
with a model fitted on the other three. The test needs no labels of its own: **the distance
the model says the train ran between two door openings, against the map distance between
those two stations.**

| test ride | phone | stops found | leg distance error, mean (max) | bias | same, summed filtered speeds | speed RMSE |
|---|---|---|---|---|---|---|
| ride A | in a hand, people talking | 11 of 11, 0 false | 8.1 % (51.7 %) | −5.8 % | 12.3 % | 3.55 m/s |
| ride B | resting | 11 of 11, 0 false | 1.5 % (8.7 %) | +0.7 % | 0.7 % | 0.49 m/s |
| ride C | resting; motor tone barely audible | 11 of 11, 1 false | 3.8 % (21.0 %) | +1.1 % | 5.8 % | 0.69 m/s |
| ride D | resting | 11 of 11, 0 false | 0.8 % (2.7 %) | +0.5 % | 2.0 % | 0.46 m/s |

Over all 40 legs: mean error 3.5 %, median 1.0 %, 30 legs within 2 %, 35 within 5 %, 3 worse
than 10 %.

The bad legs, and why:

- **ride A, two legs (−52 %, −17 %): the wheel-rotation lines with the phone in a
  hand.** This is the only hand-held ride, so the model applies tables learned on resting
  phones, and here they pull the speed off. With that measurement removed this ride scores
  3.8 % (max 15 %). It helped when there was one resting ride to borrow from (2.1 %) and
  hurts with three; neither says what properly fitted hand-held tables would do.
- **ride C, last leg (+21 %):** the phone is picked up 27 s before arrival, and on
  this ride there is no audible motor tone to carry the speed once the accelerometer is gone.
- **ride B, first leg (+8.7 %)** and the false stop on ride C (on
  the platform): the stretch before and just after boarding, see below.

On ride C the motor tone is almost absent from the audio (different
trainset, or where the phone lay; not looked into yet). The model still gets 3.8 % because
the accelerometer carries it: without the wheel-rotation lines that ride scores 35 %,
without the along-track acceleration 22 %, without the tone 3.8 %.

Given the map but **no idea where on the line the ride is or which way it is going**
(26 stations × 2 directions, equally likely), the station layer's belief after each stop,
using only the stops so far. The first stop can never be named (nothing to go on yet), so
"right from stop 2 on" is the best possible:

| what the station layer may use | ride A | ride B | ride C | ride D |
|---|---|---|---|---|
| leg distances only | from stop 6 | from stop 3 | from stop 5 | from stop 3 |
| + yaw (curves) | from stop 6 | from stop 2 | from stop 3 | from stop 2 |
| + heading (no yaw) | – | – | from stop 3 | from stop 2 |
| + barometer (no yaw) | – | – | from stop 4 | from stop 3 |
| + yaw + heading + barometer | from stop 6 | from stop 2 | **from stop 2** | from stop 2 |

Heading and barometer exist only in rides C and D; rides A and B are treated
exactly as before (–). On ride A the layer is misled by that ride's −52 % leg.
Heading's contribution is the direction of travel; the barometer's is small on its own
(the car's own pressure wanders by metres), and it slightly lowers confidence at stop 2 on
ride D (0.99 against 1.00).

`python -m model.plot` draws each ride's result (speed, mode and phone state against the
labels) into `data/phone-sensor-fusion/model/`.

What each measurement contributes (mean leg distance error with it removed; `just psf --ablate`):

| | ride A (held) | ride B | ride C (no tone) | ride D |
|---|---|---|---|---|
| everything | 8.1 % | 1.5 % | 3.8 % | 0.8 % |
| without the motor tone | 31 % | 2.4 % | 3.8 % | 1.9 % |
| without the wheel-rotation lines | 3.8 % | 0.7 % | 35 % | 0.9 % |
| without the along-track acceleration | (unused) | 1.6 % | 22 % | 1.5 % |
| without the rumble level | 8.6 % | 1.6 % | 5.5 % | 0.6 % |
| without vibration levels | 8.1 % | 1.5 % | 55 % | 0.8 % |

What doesn't work yet, plainly:

- **The model doesn't know about not being on the train.** Every recording starts before
  boarding and ends after getting off. Steady platform tones sit exactly where the motor tone is at
  36 m/s, so until the first door chime the model believes it is on a fast train, and it
  found one false stop on a platform. The first door-open chime re-anchors it
  (`P_REANCHOR`). When the rider is on the train is *labelled*
  (`presence.csv` in the labels, now about 25 minutes of off-train time), so an off-train
  state can be learned; a hand-built one was tried and removed (it could only be made to
  work by reporting every mid-ride start as "off the train").
- **Online (filter-only) estimates with the phone in a hand** lose the train for tens of
  seconds at a time when speech covers the tone at low speed (8 % mean leg error, single legs
  off by a third). Smoothing repairs it; a fixed-lag smoother would be the online answer.
- **The hand-held ride reads long by ~3 % without the wheel lines.** About 0.7 % is the tone constant differing between
  the two trainsets (wheel wear?); the rest is unexplained.
- **Wheel-rotation lines with the phone in a hand** are applied with tables borrowed from
  resting phones (there is one hand-held ride, so none can be fitted without testing on
  it), and in the four-ride cross-validation that hurts (above).
- **Switches and level crossings** show nothing in one-second vibration levels.
- Four rides, one line, one phone, one hand-held ride. Every number above is from 40 legs.

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
- **Wheel-rotation lines with the phone in a hand**: currently applied with tables borrowed
  from resting phones, which hurt the one hand-held ride in cross-validation. Needs a
  second hand-held ride to fit and test properly.
- **An off-train state**, learnable now that `presence.csv` holds about 25 minutes of
  off-train time.

## Layout

The pipeline is five folders, each with its own README, used in this order:

| | | |
|---|---|---|
| [`exploration/`](exploration/README.md) | scripts that look at the raw recordings, and what they found | nothing downstream imports it |
| [`maps/`](maps/README.md) | the Caltrain line as a 1-D track model from OpenStreetMap: stations, curvature, switches, crossings | `maps/data/` checked in |
| [`labels/`](labels/README.md) | manual and semi-automatic ground truth: stations, door chimes, wheel stop/start, on/off the train, phone handling, speed | the labels themselves are in `../data/phone-sensor-fusion-labels/<recording>/`, not checked in |
| [`features/`](features/README.md) | measurements per second, from deterministic signal processing to small learned classifiers | cached in `data/phone-sensor-fusion/features/` |
| [`model/`](model/README.md) | the HMM over (mode, speed), the station layer on top, fitting, evaluation | `params/all.json` (fitted on every ride) checked in; the cross-validation fits go to `data/` |

`app/` exports the fitted model for the phone app's online inference and checks the app's
port against this pipeline ([docs/recorder.md](../docs/recorder.md)). [`viewer/`](viewer/README.md) is a web page for exploring all of it on one time axis while
the audio plays. `common/` has the recording loader (the only code that reads the raw files) and paths;
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
this directory. Recordings go in `../data/app-recordings/<id>/`.

```sh
python -m pipeline              # features if missing, then leave-one-recording-out: fit, infer, evaluate
python -m pipeline --ablate     # ... also with each measurement removed / alone
python -m pipeline --final      # fit on every labelled recording -> params/all.json
python -m model.plot            # result plots -> data/phone-sensor-fusion/model/<id>.cv.png
python -m viewer.export && python -m viewer.serve   # the viewer, http://127.0.0.1:3040
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
