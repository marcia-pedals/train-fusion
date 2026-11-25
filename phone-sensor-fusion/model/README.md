# Model

Two small HMMs, one on top of the other.

## Layer 1: mode and speed, once per second (`states.py`, `emissions.py`, `infer.py`)

Hidden state = (mode, speed) with the rider on the train, or off it before or after the ride;
159 states:

- on the train, **moving** at one of 152 speeds, 0.25 … 38 m/s;
- on the train, standing, in one of five modes visited in order: **stopped, doors not yet
  open → door-open chime → doors open → door-close chime → stopped, doors closed** → moving;
- off the train: **before** the ride, **after** it.

Transitions (per second):

- moving: speed changes by a step drawn from P(step | speed band), learned from the speed
  labels; a step that reaches 0 means stopping;
- standing: leave each mode with probability 1 / (its mean duration, learned); from
  "stopped, doors closed" pull away with a positive step;
- rare escapes: pull away again without the doors having opened (1 %/s), doors re-open
  (0.2 %/s). (There used to be a third, from any state straight into the door-open chime,
  10⁻⁶/s, so that a chime could override a wrong belief, mostly the "fast train" on a platform
  before boarding. The *before* state does that job now; with it removed, leg distances are
  better on six of ten rides and worse on one.)

Getting on and off: a recording holds at most one ride, **before → on the train → after**,
each step once. On at a standing train (into any standing mode), off through open doors, and
both only while walking: the step-band level (`features/frames.py` `steps_db`) above −15 dB within the last
10 s. Otherwise not at all. Walking is never seen mid-ride and always at boarding and
alighting, so this is what keeps the rider on the train through anything that looks like a
platform (a ten-minute hold with the car's power off), and off it through anything that looks
like a train. The exact second of boarding and alighting is left loose: 0.05/s while allowed.

The prior puts half on the train (spread as before) and half before the ride, so a recording
may start anywhere.

Measurements, all conditionally independent given the state:

| measurement | depends on | form | used when |
|---|---|---|---|
| door-open / door-close chime presence | mode | 3 categories × (in that chime's mode / not) | always |
| audio line spectrum | speed | `features/comb.py` tables at the motor-tone partials, scaled per second so that it only says *which* speed, never *whether* moving | always |
| accelerometer line spectrum | speed | same, wheel-rotation partials; tables per phone state (borrowed from the other state if the training data had none) | phone resting or held |
| rumble level | speed (0 = standing) | Gaussian around a learned curve, plus a flat floor for levels that fit nothing | always |
| vibration levels (2 bands) | speed | same, separate curves for resting and held | phone resting or held |
| along-track acceleration | the speed *change* over the second | Gaussian around sign × Δv + offset, with an outlier share; multiplies the transition | phone resting |
| the car's hum (`cabin_db`) | on / off the train | 12 categories, a table for each; weight 0.2 | always |

Off the train the train's speed is hidden: the levels are those of standing, the car's hum has
an off-train table, and the motor tone and the chimes are neutral (below). None of the off-train terms touches
the on-train states, so given that the rider is on the train the model is the on-train model;
the speeds it reports (`v`, `v_filt`) are the train's speed given that.

**What you hear of trains is neutral.** Off the train you hear trains too. The motor-tone term
only says *which* speed, but a line held steady for minutes still piles up evidence for "a
train at that speed": 0.4–2.5 nats/s on platforms (an arriving train's motor tone, steady
platform tones, walking noise); without a counterweight the rider is "on a fast train" through
every platform wait. And the door chimes are heard from the platform: the boarding train's
opening on nearly every ride, the closing behind the rider who just stepped off on six of the
eight rides that go on long enough (an off-train chime table, averaged over every off-train
second, calls that unlikely and keeps the rider aboard). So the off-train states get exactly
what the on-train model makes of the tone and the chimes alone (a forward pass over the
on-train states with those as the only measurements; causal), including the freedom to start
a ride at any moment the walking gate is open, as boarding does (else the model picks the
boarding second that makes the tone after it fit a train best, and walking to the station
reads as riding). They then say nothing about on or off: the hum and walking decide. Viterbi gets the best-path score instead (it
compares single paths).

**The hum's weight** is set by hand. 1 / its residuals' autocorrelation time would be 0.04
(the level drifts over half a minute), and at that the model takes minutes to tell a platform
from a train. Anywhere in 0.1–0.4 the smoothed result is the same; 0.2 has the fewest live errors.

The Gaussian level terms are raised to a power < 1 (1 ÷ the autocorrelation time of their
residuals, learned): an HMM takes successive seconds as independent evidence, and a loud
stretch of track is not thirty independent reports of speed.

Inference is exact: forward-backward for posteriors (`v`, `mode_p`, `p_on`: on the
train), the forward pass alone for what a live filter would say (`v_filt`, `p_on_filt`), a
fixed-lag smoother for what a display a little late would say (`p_on_lag`: P(on at t |
everything up to t + lag)), Viterbi for one consistent sequence
(stops are read off that). Distance is the running sum of posterior-mean speed.

The along-track acceleration has two nuisance parameters per stretch of resting phone (which
way the phone points, and a zero offset). `infer.py` runs once without it, regresses the
measured acceleration on the inferred change of speed per stretch (the slope comes out at
±0.9–1.0 on ride B), and runs again with it.

## Layer 2: which station (`stations.py`)

Hidden state at each door opening = (station, direction). Transition to any station further
along. Start: uniform over 26 stations × 2 directions. Forward pass = the belief after each
stop given the past; between stops, position = that station ± distance since.

What a transition a → b is weighed by:

| measurement | compared with | needs |
|---|---|---|
| the leg's distance from layer 1 | map distance a..b (sd 4 % ⊕ 60 m) | nothing: always used |
| yaw rate through the leg | speed × map curvature along a..b | phone resting |
| compass heading of travel | the track's heading along a..b, for that direction (sd 30°: it settles north- or southbound and barely votes on position) | iOS's attitude in the recording, phone resting |
| pressure altitude through the leg | the track's elevation along a..b, up to a free offset per leg (sd 3.5 m) | a barometer in the recording |

Heading is also compared with the track's heading at each candidate station at the stop
itself. **A recording without a sensor simply contributes nothing for it**: the two
Rides A and B, which have neither attitude nor barometer, get exactly the beliefs they
got before these terms existed (checked: layer 1 identical, layer 2 within 3 × 10⁻⁶).

The travel heading comes from the phone's attitude and the along-track axis, and needs the
sign of the along-track acceleration (which end of the axis faces forward); that still
comes from layer 1's regression. Where the regression can't tell (a short or quiet
stretch) and the recording has an attitude, the sign is taken from that stretch's heading
agreeing with the others: a train doesn't turn round mid-ride.

A leg that fits no pair of stations (a false stop, a wrong distance) no longer wipes the
belief out: with probability 10⁻⁶ a leg is "unexplained" and may be any hop ahead.

The split into two layers is exact as long as the map says nothing about speed (speed
limits are ignored) and layer 1's stops are right. A joint (position, speed, mode) grid
would lift both conditions at ~10⁵ states; nothing in the data so far asks for it.

## Fitting (`fit.py`)

Everything is counted or averaged from labelled seconds: no gradient descent, no
hyper-parameter search. Every block has a prior worth a few pseudo-observations, so a
training set with no resting phone, or no hand-held one, still gives a complete model.
`params/all.json` (fitted on every labelled ride) is the whole fitted model, readable, and
checked in. The leave-one-out fits are named after recordings, so they go to
`data/phone-sensor-fusion/params/without-<id>.json` instead.

Set by hand rather than learned (each is a named constant with its reason next to it): the
escape probabilities, getting on and off and the walking threshold, the hum's weight, the outlier shares and floors, the station layer's distance, yaw,
heading and barometer sds and weights, the chime pseudo-count.

Two things in the fitting are less clean than they look:

- The motor-tone tables are partly fitted to speed labels that were themselves traced from
  the motor tone (the 14 `tone` legs). Those tables will look better on such labels than
  they are. The cross-validated distances don't have this problem (the test ride's audio
  and labels are never seen), but it does mean the tables may be over-confident.
- The acceleration noise sd can only be fitted against speed labels that didn't come from
  the accelerometer: 400 s of data.

## Evaluation (`evaluate.py`, `../pipeline.py`)

Leave one recording out, fit on the rest, infer on it. Reported per test recording:

- **leg distance error**: for each pair of consecutive door openings the model found, its
  distance ÷ the map distance between the stations those were (matched to the labelled
  chimes within 5 s), smoothed and filter-only;
- stops found / false;
- speed RMSE on confidently labelled seconds; mode accuracy on labelled seconds;
- with `--ablate`, all of it again without each measurement, and with each alone;
- the station layer's belief after every stop, with and without yaw.
- on or off the train, away from boarding and alighting (60 s either side, and the boarding
  train's doors-open time): seconds where P(on) ≥ 0.5 is wrong, for the filter, 30 s late and
  smoothed; and stops the Viterbi path finds while the rider is off the train.

`model/plot.py` draws speed, mode and phone state against the labels.
