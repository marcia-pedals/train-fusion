# Exploration

Scripts for looking at the raw recordings, and what they showed. Nothing downstream imports
from here; figures go to `data/phone-sensor-fusion/exploration/`. Rides are called A–D
(which trains they were is not in the repo). Times are seconds since the start of a recording.

| script | what it does |
|---|---|
| `overview.py` | whole ride on one page: accelerometer, gyro, accelerometer spectrogram, audio spectrogram |
| `zoom.py <id> <t0> <t1> [fmax]` | a time window: audio spectrogram over accelerometer and gyro. The main tool for labelling |
| `tones.py` | every persistent narrow-band tone in the audio, by frequency |
| `tone_vs_speed.py <id>` | accelerometer-only odometry against the map; which spectral lines are proportional to speed |
| `track_features.py <id>` | do the map's curves, switches and level crossings show up in the IMU? |
| `heading.py`, `magnet.py`, `baro.py`, `fused.py` | the sensors added later (magnetometer, barometer, iOS device motion): see the last section |
| `transcribe.py` | whisper transcripts, for station announcements (kept out of the repo: other people's conversations) |

## The first two recordings

| | ride A | ride B |
|---|---|---|
| length | 10 legs | 10 legs |
| phone | in a hand the whole way; starts on the platform | lying flat on a surface for nearly all of it |
| sound | a conversation next to the phone | quiet car |

Sensor timing is clean in both: accelerometer and gyro at 100.07 Hz with no gap over 17 ms,
one audio segment, buffer timestamps consistent to well under a frame.

In the overview plot (`overview.py`) stops are dark bands in the two spectrograms; between
them the arches are spectral lines rising and falling with speed.

## Findings

**1. Door chimes are unmistakable.** (`tones.py`, `zoom.py`)
At every station stop, and nowhere else:

- doors opening: a steady tone at 2200 Hz with harmonics at 4400 and 6600 Hz, 6.0 s long;
- doors closing: 3520 Hz (with 1760, 5280, 7040 Hz), 5.2 s, a short gap, 2.5 s more,
  under the "doors are closing" announcement.

A two-of-three-partials detector finds 11 opening and 10 closing chimes in each ride and
nothing else on the train (two 1 s false closings on the platform before boarding).
The stop sequence is very regular on ride B: wheels stop, 1.9 s (± 0.15) later
the opening chime starts; the train pulls away 13.3 s (± 1.2) after the closing chime starts.

**2. A phone lying still is a good odometer on its own.** (`tone_vs_speed.py`)
Take the horizontal acceleration along its principal axis, integrate it from wheel-start to
wheel-stop, with the zero level interpolated between the two dwells:

Over the six legs of ride B through which the phone lay still, integral ÷ map distance is
0.987, 0.996, 0.998, 0.996, 0.999 and 1.045.

(The other four legs had the phone picked up part-way.) Peak speeds come out at 31–36 m/s;
the line limit is 79 mph = 35.3 m/s.

**3. The motor tone is a speedometer.** (`tone_vs_speed.py`, `labels/trace_tone.py`)
Resample the audio spectrogram onto frequency ÷ speed and average over time: anything
proportional to speed piles up at one ratio. There is a sharp peak at **26.4 Hz per m/s**
(930 Hz at line speed) and weaker ones at 2× that and at 58 Hz per m/s.

Traced through each of the 20 legs and integrated, the tone gives the map distance with a
constant of 26.0–26.8 Hz per m/s on every leg of both rides (sd 1.1 %; some of that is where
exactly the train stops along a 200 m platform). Where both exist, tone speed and
accelerometer speed agree to 0.2–0.6 m/s rms. The tone is audible in the hand-held ride
too, but only about a third of the time (speech), and in neither ride below ~250 Hz
(~10 m/s), where rumble and hums cover it.

**4. The accelerometer sees the wheels turn.** The same analysis on the accelerometer
spectrum (phone resting) gives lines at 0.35 Hz per m/s and its multiples (0.70, 1.05, 1.40):
once per wheel revolution for a 0.91 m wheel. So the motor tone is ~75 cycles per wheel
revolution. These are the arches in the accelerometer spectrogram of the overview.

**5. Loudness rises with speed.** Audio level in 20–70 Hz goes from −48 dB standing to
−39 dB at line speed in both rides (the hand-held one ~2 dB louder mid-range), with ±2 dB of
scatter. Accelerometer vibration level rises by 25 dB with speed when the phone is resting
and not at all when it is in a hand (the hand's own motion is 20–30 dB above the train's).

**6. Curves show in the gyro; switches and crossings don't show in vibration.** (`track_features.py`)
Rotation rate about the vertical against speed × map curvature at the labelled position,
ride B: slope 0.82, r = 0.69, residual 0.001 rad/s (robust) against curve signals of
0.01–0.02 rad/s. The sign and the timing of every curve match.

One-second vibration level within 30 m of a mapped switch: −0.1 dB against elsewhere
(sd 4 dB, 77 s of data); level crossings −1.3 dB. No effect at this resolution. A switch is
a transient of a few tens of ms; finding it needs a different feature (and probably the
lateral axis), which wasn't tried.

**7. Things that mislead.**
- On the platform (ride A, before boarding) there are steady tones at 950, 1425 and 1925 Hz. 950 Hz
  is where the motor tone is at 36 m/s.
- Inside the train there are steady lines near 200 Hz at stops (blower?) and a family at
  3.7–4.2 kHz and 7.8 kHz while moving; they are not proportional to speed.
- Two 7.25 kHz beeps ~5 s before most door openings (not used).
- Whisper catches most announcements (the next station, the train's number and direction,
  doors closing) when each 30 s window is decoded without the previous one's text
  (`transcribe.py`). With whisper.cpp's default context it fell into repeating one line and
  kept only 0–4 per ride.

## The sensors added later

Two rides (C and D, in opposite directions) also have a raw
magnetometer (100 Hz), a barometer (0.75 Hz) and iOS's fused device motion (100 Hz:
attitude, gravity, "user" acceleration, bias-corrected rotation rate, calibrated field).
The phone lay still for nearly all of both rides, so everything below is for a resting
phone; nothing here could be tested in a hand. `maps/data/elevation.csv` (ground elevation
every 50 m, `maps/fetch_elevation.py`) was added for the barometer.

**8. Heading: iOS's attitude knows which way the train points, to a few degrees.** (`heading.py`)
The train's forward direction in the phone's axes comes from the accelerometer; iOS's
attitude turns it into a compass heading; the map says what it should be.

| source of attitude | error against the track heading | within 20° | pointing the wrong way |
|---|---|---|---|
| iOS fused attitude | median +2°, spread ±7° (ride C); −1°, ±1° (ride D) | 100 % | 0 % |
| magnetic field + gravity, no gyro | median +45°, spread ±47° | 23–37 % | 0–24 % |
| gyro only, heading *change* since the stretch began | +1°, ±1° | 100 % | – |

Inside the steel car the magnetic field itself is useless as a compass (18–39 µT in one
ride, 34–73 µT in the other; the Earth's is 48). iOS's heading is right anyway, which
means it is the gyro carrying a heading that was set *before boarding*, outdoors. A
recording started inside the train would not have that; this was not tested.
So: north- or southbound is decided, every second, as soon as the forward axis is known
(one acceleration or braking with the phone still). As a position fix it is weak: the
track's heading only spans 104°–160° over the stretch these rides cover and changes by a
median of 0.2° per km, so a heading good to ±3° still leaves 27 % of that stretch
possible, ±10° leaves 49 %. Curves are where it bites, and the gyro already gives those.

**9. Magnetometer: no traction signature, no speed lines, no place fingerprint.** (`magnet.py`)
- The 60 Hz supply shows as a line (folded to 39.5 Hz) in one ride and not at all in the
  other; its level does not follow motoring or braking (correlation with |a × v| 0.28 and
  0.03; medians for motoring / coasting / braking within 1 dB of each other, scatter 3–4 dB).
- The field's size and direction don't follow acceleration or power either (|r| ≤ 0.2).
- Low-frequency flutter (0.5–15 Hz) grows with speed (r = 0.5–0.7): the phone vibrating in
  the car's uneven field. There are no lines proportional to speed (1 dB at best; the
  accelerometer's wheel line stands 3–4 dB up).
- The field at a given place does not repeat between the two rides (r ≈ 0 after removing
  slow trends), though they ran on opposite tracks in different cars.

Follow-ups (`magnet_gaps.py`), closing what the first look left open:
- *Is the sensor simply too slow?* It delivers 99.6 samples/s. Its noise floor falls
  smoothly with frequency and is not cut off below 50 Hz (0.9 → 0.7 ×10⁻⁴ µT²/Hz from
  30–38 Hz to 46–49.5 Hz), so nothing in band says "filtered". What happens to a true
  60 Hz field before sampling cannot be told from these recordings.
- *Is the 60 Hz line the train's supply?* No. A line that a 60 Hz source would fold to is
  there **off** the train in both rides at ~4 nT, and on the train it is no stronger
  (3.5 nT ride D, 1.2 nT ride C). Whatever it is (ambient mains, or the phone), the
  overhead wire adds nothing measurable to it: at most a few nT, where a naive estimate
  for a wire carrying 200 A four metres away is several µT.
- *Lines proportional to speed at any ratio?* Searched 0.1–40 Hz per m/s on each axis,
  folding frequencies above 50 Hz back, at all speeds and at 2–10 m/s only: the best ratio
  stands 0.6–1.4 dB above the rest, which is what the maximum of 2000 noisy ratios looks
  like. None.
- *Traction current as an event?* Stacking the field around the 8–9 wheel-starts and
  wheel-stops per ride where the phone lay still: one second after pulling away, with full
  tractive effort on and the car barely moved, the field vector has changed by 0.26–0.28 µT
  against 0.21–0.24 µT between two moments of standing still. No step: traction and
  braking currents change the field at the seat by less than about 0.1 µT. The 60 Hz line
  doesn't change either (±2–3 nT).
- What the stacks do show: the field changes by ~0.7 µT after 1 m of movement, ~2 µT after
  6 m, ~3 µT after 25 m, the same on arriving as on leaving. The static field inside the
  car depends on where the car is, metre by metre (rails, platform steel). That is a
  place signature at a much finer scale than the 50 m cells tested above. Whether it
  repeats on another pass cannot be tested: the two rides used opposite tracks.

**Promising, to revisit: the field's rate of change as a low-speed sensor.** (`magnet_speed.py`)
Because the field changes from metre to metre, how fast the reading changes follows speed:
0.46 µT/s standing, 0.5 at 0.2–1 m/s, 0.6–1.1 at 1–2, 1.0–1.5 at 2–4, 2.2–2.4 at 4–7,
3.8–4.1 at 7–10, 6.5 at 10–20 m/s (medians, both rides). Below 10 m/s, exactly where the
motor tone is inaudible, log rate and log speed correlate at 0.63–0.68. One second at a
time it separates 3–7 m/s from standing almost perfectly (AUC 0.96), 1–3 m/s well
(0.83–0.89), 0.2–1 m/s weakly (0.70). The field's *strength* alone, which doesn't change
when the phone is turned, does nearly as well (0.93–0.94, 0.74–0.84, 0.64–0.68), so this
might work in a hand, where the accelerometer doesn't.
Limits: the field's slope along the track is unknown and only stays the same for ~2 m
(correlation 0.7 at 2 m, ~0 at 5 m), so one second gives speed to within a factor of ~2,
and there is no smooth curve to fit and extrapolate. The change at each stop has its own
size and direction (so it is the surroundings, not the train's current).
Not yet known, and needing more recordings: whether it works with the phone in a hand
(no hand-held ride has a magnetometer); whether the field repeats at the same place on
another pass (needs two rides on the same track, ideally the same seat), which would make
it a position fingerprint as well.

**10. Barometer: follows the track's elevation; doors show too.** (`baro.py`)
Pressure altitude against the map's ground elevation at the labelled position, allowing
an offset and a linear drift per ride: r = 0.92 and 0.88, slope 0.96, residual 2.4 m and
3.7 m on a profile that spans 26 m.

Sliding the map under the ride, the misfit is smallest within ±0.2–0.5 km of the true
position and half as large again 1 km away (2.4 → 3.3 m; 3.7 → 4.3–4.6 m): a whole ride's
pressure trace places it to roughly a kilometre, with no stops needed. What spoils it:
the car's own pressure. It dips by the equivalent of several metres while moving, and it
steps when the doors open: +5.8 ± 0.8 m within 8 s at every stop of ride D
(a pressurised car venting), but only a −1.5 m blip that recovers in ride C.
So doors are visible in pressure, but not the same way in every car. The DEM has its own
flaw: it drops into every road underpass (spikes in the profile) and needs a median
filter.

**11. iOS's fused acceleration is worse than the raw one for this.** (`fused.py`)
- iOS splits the accelerometer into gravity and "user" acceleration by assuming that
  whatever persists is gravity. A train accelerates for a minute at a time, so most of it
  is filed under gravity: user acceleration turned into the world frame and projected on
  the track direction recovers only 20–40 % of the labelled speed change (residual
  0.17–0.21 m/s² against 0.04 for the raw recipe), and integrating it gives 10–45 % of the
  map distance on every leg. It cannot be used for odometry, resting or not.
- Their sum is just the raw accelerometer, so applying our recipe to it changes nothing.
- iOS's rotation rate about iOS's gravity matches speed × curvature exactly as well as the
  raw gyro with its median removed (residual 0.0025–0.0029 rad/s either way); the only gain
  is not needing the median.
- The useful part of the fusion is the attitude's *heading* (8). With it, the along-track
  axis and its sign need no principal-axis fit and no per-stretch regression, and survive
  the phone being moved; tilt would still have to come from our own long-term gravity
  estimate, not iOS's.

**12. Smaller things tried.**
- Track grade in the accelerometer (gravity along a slope adds to the reading): in two of
  four resting stretches the accelerometer's excess over the labelled speed change
  correlates with the map's grade (|r| 0.4–0.55), in the other two not. Inconclusive, and
  the speed labels on those legs come from the same accelerometer.
