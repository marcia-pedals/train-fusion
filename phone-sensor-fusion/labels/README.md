# Labels

Ground truth for the four recordings: for fitting the features and the model, and for
scoring them. The labels themselves are **not checked in**: they live in the repo's gitignored data
directory, `data/recordings/labels/<recording>/` (`common/paths.py`: `LABELS`),
because which train someone took when is private. They are made by hand and can't be
rebuilt, so `just sync-recordings` after labelling: it backs them up to the recordings bucket
(`labels/`), and brings them back on another checkout. Times are seconds since the start
of the recording.

| file | what | how it was made | good to |
|---|---|---|---|
| `journey.toml` | direction, the stations called at, and the evidence | announcements in the whisper transcript + the number of door chimes + leg durations against the map | certain |
| `stops.csv` | per station: `t_open`..`t_open_end` and `t_close`..`t_close_end` (the two door chimes), `t_arrive` (wheels stop), `t_depart` (wheels start) | chimes: `propose.py`, each checked on a spectrogram (`exploration/zoom.py`). Wheels: `stop_times_accel.py`, checked on `stop_sheets.py` | chimes ±0.1 s; wheels ±0.5 s |
| `presence.csv` | intervals of where the rider is: `train`, `platform`, `elsewhere` (both off the train), `unknown` (boards / alights somewhere inside the interval), each with its evidence | `presence_sheets.py`: steps in the accelerometer, the door chimes, and the change of ambience | boarding and alighting to within the `unknown` interval (4–15 s) |
| `phone.csv` | intervals of `resting` (lying on a surface), `held` (in a hand, fairly still), `handled` (being moved about) | read off the gyro energy and the low-passed accelerometer in the overview plots, then checked with `check_phone.py` | ±2 s; `held` / `handled` is a judgement |
| `tone_trace.csv` | the motor tone's frequency through every leg, 4 per second | `trace_tone.py`, overlays checked by eye | see below |
| `speed.csv` | speed, 1 per second, with its source and whether it is trustworthy | `build_speed.py` from the files above and the map | see below |
| `modes.csv` (optional) | the train's mode (`labels/frames.py`: `MODE_KEYS`, or `unknown`) over intervals that `stops.csv` can't describe, e.g. doors reopening during a long hold; overrides what `stops.csv` implies | by hand, from the chimes on a zoom | as the chimes |

`load.py` reads the files; `frames.py` turns them into per-second arrays (speed, mode,
on the train or not, phone state), which is what `features/` and `model/` train and score against.

## What is and isn't labelled

- **Wheel-stop and wheel-start times exist only where the phone was resting** (three of
  the four rides, and not at their first and last stops, when it was in a hand). They come
  from the accelerometer: braking holds until the wheels stop and then releases in a jerk.
  With the phone in a hand there is nothing comparable, and the tone can't say either (it is
  inaudible below 10 m/s). Ride A's stop times are therefore blank rather
  than guessed.
- **Which stations**: from announcements on every ride ("next stop" after each departure
  names every station on rides B, C and D; ride A, with a conversation next to the phone,
  has only a few). On C and D the leg durations matching the earlier rides and the
  accelerometer's own leg distances matching the map agree with them (`journey.toml`).
- **Speed on ride C**: the motor tone is barely audible there, so the tone
  trace fails (implied tone constants of 26–36 Hz per m/s). Seven legs have accelerometer
  speed; the other three (phone in a hand for part of the leg) have no speed label at all:
  `build_speed.py` rejects a traced leg whose implied constant is more than 4 % off 26.4 or
  whose ridge is visible less than 20 % of the time.
- **On / off the train** (`presence.csv`) is labelled for the rider, separately from the
  train's own mode: a door chime heard from the platform is still a door chime. Every
  recording starts before boarding and ends after stepping off; rides C and D include
  time before reaching the platform and on it (`elsewhere`, `platform`).
  Stepping through the door isn't directly visible; what brackets it is the door chime
  before, continuous steps, and the ambience changing (platform tones fading or appearing,
  the level dropping), so each
  boarding and alighting is an `unknown` interval rather than an instant. Nothing in the
  model uses this label yet (it has no off-train state); `labels/frames.py` exposes it as
  `on_train` per second, and drops speed and mode labels (other than door chimes) for
  off-train seconds. Platform sounds fool the chime *proposer* (sweeping harmonics of a
  train pulling in, steady tones near 3.5 and 5.3 kHz): check every proposed chime that
  falls outside the ride.

## Speed labels

Each second gets the best source available (`speed.csv`, column `source`):

| source | where | how |
|---|---|---|
| `dwell` | at a platform | 0, from wheel-stop (else the opening chime) to wheel-start (else the end of the closing chime) |
| `accel` | 6 legs of ride B through which the phone lay still | integral of the along-track acceleration between wheel-start and wheel-stop |
| `tone` | the other 14 legs | the traced motor tone ÷ a constant |

On `accel` and `tone` legs **one constant per leg is chosen so that the leg's integral
equals the map distance between its stations.** So the labels' scale is the map's; a model
is never tested on a recording whose labels it was fitted to.

`trace_tone.py` follows the tone by dynamic programming: the path through the spectrogram
that collects the most line prominence (at the tone and at its 2.2× partial), is smooth in
its second difference like a train's speed, and is at 0 Hz when the closing chime ends and
again when the next opening chime starts. Below 250 Hz there is no evidence, and the path
crosses that zone as a straight ramp.


How good:

- Where both sources exist (the six `accel` legs), tone and accelerometer speeds differ by
  0.2–0.6 m/s rms wherever the tone is visible.
- `confident = 0` marks seconds where the label is a guess: on `tone` legs wherever the
  ridge isn't actually seen (below 250 Hz, or under speech), about half of each leg on
  ride B and two thirds on ride A. Mid-leg the trace is interpolating
  between visible stretches and is probably right; at the two ends of a leg (below 10 m/s)
  it is a straight line and can be several seconds off. Scoring and fitting use confident
  seconds only.
- Ride A's first leg is the weakest: the rider is walking
  to a seat for its first 40 s and the tone is visible 24 % of the time.

## Adding a recording

**Resting or held**: a still hand and a seat look alike if you only look for the moments
the phone is moved (steps in the low-passed accelerometer). They differ in level: resting
is flat (a few degrees of tilt) and quiet (gyro jitter around 10^-2.1 rad/s), held is
tilted and wanders, with more jitter. One ride was first labelled resting throughout while
it was in a hand. `check_phone` flags labelled intervals whose jitter disagrees (tilt alone
doesn't count: a phone can rest propped up), so look and decide.

```sh
python -m exploration.overview                 # look at it
python -m exploration.transcribe               # announcements -> journey.toml
python -m labels.propose <id>                  # chime times -> stops.csv, after checking each with exploration.zoom
python -m labels.stop_times_accel <id> <t0> <t1> "<opens>" "<closes>"   # resting stretches only: t_arrive, t_depart
python -m labels.stop_sheets <id> "<opens>" "<closes>"                  # ... to check them
python -m labels.presence_sheets               # first / last 150 s -> presence.csv
#   write phone.csv from the overview
python -m labels.check_phone <id>              # resting / held that the sensors disagree with
python -m labels.trace_tone <id>               # tone_trace.csv; check data/phone-sensor-fusion/labels/*.trace_*.png
python -m labels.build_speed <id>              # speed.csv; prints tone-vs-accelerometer agreement
```

**Stations (`journey.toml`)**: the announcements are the evidence to use first. Grep the
transcript (`data/phone-sensor-fusion/whisper/<id>.csv`) for "next stop", "approaching"
and "making all stops" (train number and direction); whisper misspells some names, but
their order settles them. Write down in `evidence` which announcements, at what times, fix
which stations. Only where none is heard, fall back on the leg durations against the map
and earlier rides, and on the accelerometer's leg distances; say so in `evidence`.

Before relying on a transcript, check that it isn't one line on repeat
(`cut -d, -f3- <id>.csv | sort | uniq -c | sort -rn | head`): a few dozen "Thank you."
for windows without speech is normal, hundreds of one sentence means whisper went wrong.
`transcribe.py` skips rides that already have a transcript: delete the csv to redo one.
