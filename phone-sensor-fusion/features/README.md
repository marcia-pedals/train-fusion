# Features

Turn a recording into one row of measurements per second. Three stages, each using only
what the previous ones produced:

| stage | module | learned? | output per second |
|---|---|---|---|
| 1 | `frames.py` | no | door-chime presence (open, close); audio line spectrum; accelerometer line spectrum; rumble level; vibration levels; gyro RMS; mean acceleration and rotation. If the recording has them: pressure altitude, iOS's attitude |
| 2 | `phone_state.py` | yes: 3-state HMM on gyro RMS | P(phone resting / held / handled) |
| 3 | `motion.py` | no | along-track acceleration and yaw rate, where the phone is resting; with an attitude, the compass heading of the along-track axis |
| – | `comb.py` | yes: which lines are ∝ speed, and likelihood-ratio tables | log-likelihood of each candidate speed, from a line spectrum |

Stage 1 is cached in `data/phone-sensor-fusion/features/<id>.frames.npz`; the rest is cheap
and computed when needed. Each module's docstring has the details; the essentials:

**Door chimes** (`frames.py`). A partial is present if its band is ≥ 12 dB above the
spectrum 100–400 Hz to either side; a chime is present if two of its three partials are.
The measurement is the fraction of the second for which that holds.

**Line spectra** (`frames.py`). Power spectrum in dB minus its running median over frequency
(±80 Hz for audio, ±1.5 Hz for the accelerometer): the smooth spectrum shape goes, narrow
lines stay. Max-pooled over a few bins so that a line that moves within the second isn't lost.

**Speed from lines** (`comb.py`). Fitted from speed labels: (1) the ratios frequency ÷ speed
at which lines appear (the peaks of the ratio spectrum; 26.4, 52.7 and 58.1 Hz per m/s for
audio), and (2) per ratio and speed band, a table of log p(prominence | partial here) −
log p(prominence | no partial), made non-decreasing in prominence by isotonic regression.
Applied, it sums the tables at ratio × v for every candidate speed v. Where a partial is
never visible (the motor tone below 10 m/s) the table comes out flat by itself.

**Phone state** (`phone_state.py`). Resting / held / handled from the log gyro RMS: Gaussian
per state, sticky transitions, forward filter. It decides what else is believed: the IMU
describes the train only when the phone is resting; vibration levels have separate models
for resting and held and none for handled.

**Motion** (`motion.py`). Per stretch of resting phone: vertical = mean accelerometer
reading; along-track axis = principal axis of the horizontal acceleration; yaw rate =
rotation about the vertical minus its median. The along-track acceleration has an unknown
sign and a small offset per stretch, which the model estimates.

## Causality

Everything in stage 1 and 2 needs at most the current second (the accelerometer spectrum: a
5 s window centred on it), so it can run live. Stage 3 uses whole stretches for the vertical
and the along-track axis; live, those become running estimates that settle after the first
station-to-station cycle, or come from the magnetometer once there is one.

## What isn't pulling its weight

The vibration levels change no result. The accelerometer line spectrum (wheel rotation,
`wheel` in the model) is used whenever the phone is resting or held, with tables fitted per
phone state; with one ride of each kind, cross-validation can only ever apply tables
borrowed from the other state, so its measured benefit (hand-held ride: leg error 3.2 % →
2.1 %) is a lower bound on a properly fitted one, and its small cost on the resting ride
may be the borrowing.
