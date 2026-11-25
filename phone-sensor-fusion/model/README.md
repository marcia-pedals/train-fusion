# Model

Two small HMMs, one on top of the other.

## Layer 1: mode and speed, once per second (`states.py`, `emissions.py`, `infer.py`)

Hidden state = (mode, speed), 157 states:

- **moving** at one of 152 speeds, 0.25 … 38 m/s;
- or standing, in one of five modes visited in order: **stopped, doors not yet open →
  door-open chime → doors open → door-close chime → stopped, doors closed** → moving.

Transitions (per second):

- moving: speed changes by a step drawn from P(step | speed band), learned from the speed
  labels; a step that reaches 0 means stopping;
- standing: leave each mode with probability 1 / (its mean duration, learned); from
  "stopped, doors closed" pull away with a positive step;
- rare escapes: pull away again without the doors having opened (1 %/s), doors re-open
  (0.2 %/s), and from *any* state straight into the door-open chime (10⁻⁶/s). The last one
  is what lets a chime override a wrong belief.

There is no "not on the train" state. Whether the rider is aboard is labelled
(`presence.csv`) but not modelled: the recordings hold under a minute of
off-train time each, too little to learn what a platform looks like to the sensors, and a
hand-built state (scored as "the best on-train explanation minus a margin") only worked by
calling every mid-ride start "off the train" until the next stop. So before a recording's
first door chime and after its rider steps off, the output means nothing.

Measurements, all conditionally independent given the state:

| measurement | depends on | form | used when |
|---|---|---|---|
| door-open / door-close chime presence | mode | 3 categories × (in that chime's mode / not) | always |
| audio line spectrum | speed | `features/comb.py` tables at the motor-tone partials, scaled per second so that it only says *which* speed, never *whether* moving | always |
| accelerometer line spectrum | speed | same, wheel-rotation partials; tables per phone state (borrowed from the other state if the training data had none) | phone resting or held |
| rumble level | speed (0 = standing) | Gaussian around a learned curve, plus a flat floor for levels that fit nothing | always |
| vibration levels (2 bands) | speed | same, separate curves for resting and held | phone resting or held |
| along-track acceleration | the speed *change* over the second | Gaussian around sign × Δv + offset, with an outlier share; multiplies the transition | phone resting |

The Gaussian level terms are raised to a power < 1 (1 ÷ the autocorrelation time of their
residuals, learned): an HMM takes successive seconds as independent evidence, and a loud
stretch of track is not thirty independent reports of speed.

Inference is exact: forward-backward for posteriors (`v`, `mode_p`), the forward pass alone
for what a live filter would say (`v_filt`), Viterbi for one consistent sequence (stops are
read off that). Distance is the running sum of posterior-mean speed.

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
| compass heading of travel | the track's heading along a..b, for that direction (sd 30°: it settles north- or southbound and barely votes on position); each leg's heading may be off altogether (20 %) | iOS's attitude in the recording, phone resting |
| pressure altitude through the leg | the track's elevation along a..b, up to a free offset per leg (sd 3.5 m) | a barometer in the recording |

The compass can drift: on one ride it turned 150° over the ride while iOS rated its
calibration high throughout, and heading, trusted, put the train going the wrong way.
Hence the chance that a leg's heading is off and says nothing.

Heading is also compared with the track's heading at each candidate station at the stop
itself. **A recording without a sensor simply contributes nothing for it**: the two
Rides A and B, which have neither attitude nor barometer, get exactly the beliefs they
got before these terms existed (checked: layer 1 identical, layer 2 within 3 × 10⁻⁶).

The travel heading comes from the phone's attitude and the along-track axis, and needs the
sign of the along-track acceleration (which end of the axis faces forward); that still
comes from layer 1's regression. Where the regression can't tell (a short or quiet
stretch) and the recording has an attitude, the sign is taken from that stretch's heading
agreeing with the others: a train doesn't turn round mid-ride.

Not every door opening layer 1 finds is a stop at a station: a misheard chime, or the
doors reopened during a hold. Each opening may be false (prior 5 % each, so a run of them
costs that per opening); the leg then runs on to the next one, and the false openings fall
anywhere along it. A
false opening splits a leg into two that fit no pair of stations, while the leg run on
through it fits one; that is how it shows. Only the distances decide this: the span's
total is what its distance gives it, and yaw, heading and barometer just share it out
among station pairs. (With them voting too, a ride whose heading fits the track badly
everywhere was best explained by calling most of its stops false.) The belief at each stop
is given that it was a stop; `p_real_filt` and `p_real` say how likely that was, from the
stops so far and from all of them. With this prior and the compass one above both at 0,
this is the layer as it was before.

A leg that fits no pair of stations for another reason (a wrong distance) doesn't wipe the
belief out either: with probability 10⁻⁶ a leg is "unexplained" and may be any hop ahead.

The split into two layers is exact as long as the map says nothing about speed (speed
limits are ignored) and layer 1's stops are right or false (a stop it missed reads as a
station passed). A joint (position, speed, mode) grid
would lift both conditions at ~10⁵ states; nothing in the data so far asks for it.

## Fitting (`fit.py`)

Everything is counted or averaged from labelled seconds: no gradient descent, no
hyper-parameter search. Every block has a prior worth a few pseudo-observations, so a
training set with no resting phone, or no hand-held one, still gives a complete model.
`params/all.json` (fitted on every labelled ride) is the whole fitted model, readable, and
checked in. The leave-one-out fits are named after recordings, so they go to
`data/phone-sensor-fusion/params/without-<id>.json` instead.

Set by hand rather than learned (each is a named constant with its reason next to it): the
escape probabilities, the outlier shares and floors, the station layer's distance, yaw,
heading and barometer sds and weights, the chime pseudo-count.

Two things in the fitting are less clean than they look:

- The motor-tone tables are partly fitted to speed labels that were themselves traced from
  the motor tone (the 14 `tone` legs). Those tables will look better on such labels than
  they are. The cross-validated distances don't have this problem (the test ride's audio
  and labels are never seen), but it does mean the tables may be over-confident.
- The acceleration noise sd can only be fitted against speed labels that didn't come from
  the accelerometer: 400 s of data.

## Evaluation (`evaluate.py`, `schemas.py`, `../pipeline.py`)

Leave one ride out, fit on the rest, infer on it, and score it against its labels. The
labels only cut the ride up: each labelled stop gets a window (its labelled times ± 5 s),
and a leg runs from one labelled stop to the next, its moving window between the two
stops' windows (less any door opening labelled there, such as doors reopened in a hold).

Per leg, once with the filter (measurements up to the end of the window in question) and
once smoothed (the whole ride):

- **distance error**: speed summed from one labelled door opening to the next, against the
  map distance between the two stations;
- **p(missed stop)**: the probability that the doors did not start opening anywhere in the
  end stop's window;
- **p(false stop)**: the probability that they started opening at least once within the
  moving window.

Both are the probability of being wrong, and both are over paths, not frames
(`hmm.p_visit`: the forward pass over the window on two copies of the chain, paths that
have entered the door-open chime and paths that haven't). Each is computed directly rather
than as one minus the other, so tiny values keep their precision; the viewer shows them as
log10.

Per ride:

- the station layer run **causally** (`stations.localise(causal=True)`): door openings read
  off the filtered modes, and at each one the layer is run again on speeds smoothed over
  everything heard up to then (`Result.v_upto`), so each leg is measured knowing how it
  ended. Per leg, `live_dist_err` is that distance against the map. After each opening it says where
  the train last stopped (`now_filt`); against the last labelled stop and the ride's
  direction (within 5 s either side of a labelled door opening, the stop before it and the
  one it opens both count), **time to fix** is from leaving the first labelled stop until that belief
  first reaches 0.9, and **lost** is the time after that it spends below 0.5. The belief
  only changes at door openings so far.
- the station layer **smoothed** (the Viterbi openings and `p_real`): labelled stops with no
  opening it believes was a stop (**missing**), and openings it believes were stops beyond
  one per labelled stop (**spurious**).

`pipeline.py` writes them to `data/phone-sensor-fusion/eval/` as `legs.parquet` and
`rides.parquet` (columns in `schemas.py`, declared with the repo's schema language) and the
thresholds to `meta.json`; with `--ablate` every row again for each variant. The viewer
(`../viewer/`) shows them.

The layer-1 acceleration's per-stretch sign and offset come from the whole ride even for
the filter (see Layer 1 above), so "filtered" is causal given those.

`ride_view.py` writes, from the same inference, what the viewer's ride page shows
(`data/phone-sensor-fusion/rides/<id>/`). `model/plot.py` draws speed, mode and phone state
against the labels.
