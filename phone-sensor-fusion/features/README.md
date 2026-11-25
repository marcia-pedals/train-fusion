# Features

Turn a recording into one row of measurements per second. Three stages, each using only
what the previous ones produced:

| stage | module | learned? | output per second |
|---|---|---|---|
| 1 | `frames.py` | no | door-chime presence (open, close); audio line spectrum; the car's hum; accelerometer line spectrum; rumble level; vibration levels; walking (step-band level); gyro RMS; mean acceleration and rotation. If the recording has them: pressure altitude, iOS's attitude |
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

**The car's hum** (`frames.py`, `cabin_db`). Inside a Caltrain car there are steady lines at
multiples of 120 Hz (the car's 60 Hz power), clearest at 3720–4200 Hz, whether the train is
moving, standing or has its doors open; off the train they are heard only right next to a car
with its doors open. The measurement is the mean prominence of harmonics 31–35, which stay
clear of both chimes and of the platform tones heard so far. Per second, on against off the
train: AUC 0.95 (0.85 with the phone being carried). It is weaker on one trainset (ride E) and
gone while a train stands with its power off (ride J's long hold).

**Walking** (`frames.py`, `steps_db`). The |acceleration| level at walking pace, 1.2–3 Hz. At
every labelled boarding and alighting it reaches −13 dB or more; riding along, not one second
on any ride is above −15 dB (seated, standing, hand-held). The model lets the rider on or off
the train only within 10 s of it.

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
