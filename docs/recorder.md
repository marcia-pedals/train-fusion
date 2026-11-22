# The phone recorder (`ios/`)

A one-screen iPhone app for recording a train ride from the inside: audio (to hear the
door chimes and the traction motors) plus raw accelerometer, gyroscope and magnetometer
samples and the barometer, all
with timestamps that convert to exact clock time. One button starts and stops a recording;
after each one the app asks whether to upload it (through the backend, to
`gs://train-fusion-recordings/raw/`), and it can share each ride's Train Measurements
(asked when the first recording starts). Signing in is optional; it links what's shared to the
account. What the app does about accounts, uploads, Train Measurements and keeping recordings is
in [accounts-and-uploads.md](accounts-and-uploads.md); the backend's API in [api.md](api.md).
Recording carries on with the app in the background and the phone locked.

The recordings are used by [phone-sensor-fusion/](../phone-sensor-fusion/README.md), which infers the
train's stops, speed and position from them. `just sync-recordings` downloads them to
`data/recordings/raw/<bucket dir>/` (and syncs their labels, `data/recordings/labels/`, both ways).
The bucket dir is the recording id plus the backend's suffix, `<id>-<8 hex>`, for uploads
made since uploads went through the backend; plain `<id>` for older ones.

## Screens

Three, behind a tab bar:

- **Measure**: what the model believes right now (next section), under a thin header.
  Normally the header has the elapsed time, in the middle the train ("Enter trip and car
  number"; tap for a sheet with trip number, car number and upper / lower / vestibule,
  whose Save refuses numbers that can't be right; tap again to edit or clear; it goes into
  `meta.json` and the model doesn't use it) and, on the right, the record button. When a recording is being
  replayed it has two lines: a progress bar with elapsed / total time and which recording
  it is; and previous stop, play / pause, next stop and the replay speed (1× to 64×, or as
  fast as it goes). "Stop" means a door opening the model heard; with none left in that
  direction the buttons go to the beginning or the end. The model only runs forwards, so
  going back replays from the start, flat out, to the place (a second or two). Tapping the
  Measure tab again leaves replay.

  The beliefs are shown for a rider, not a statistician, in four compact cards. Each
  keeps its size whether or not it has anything to show yet (a greyed-out skeleton stands
  in), so the screen doesn't jump as fixes arrive:
  1. one line for what the train is doing: "Listening for door to start tracking…" until
     the first door chime (before that the model can't tell a platform from a train), then
     the speed with its 90 % interval and the acceleration over the last 3 s (always
     m/s²), or the doors' state, or "Train stopped";
  2. where it is: north- or southbound, the last station and the next with the train
     between them and the distance from each, and how sure it is in a word, with the
     runner-up segment beside it while it isn't confident. Once the doors have opened at a
     station the line slides to show the train at that station, between the one before and
     the one after, without distances; when the train is clearly moving again it slides
     back to the two-station view;
  3. the last four stops, newest first: station, and when the doors opened and closed (the
     start of each chime). Stations are named by working back from the current fix along
     the measured leg lengths, so the table stays a skeleton until the fix is confident;
  4. icons for how good the information is: the phone (lying still / held / being moved),
     the motor tone, the along-track acceleration, and the wheel vibration. Green good,
     yellow fair, red missing, grey not in play. Tap for each one's state and a number.

  Every probability is still there under "Details".
- **Settings**: miles and mph, or kilometres and km/h; Train Measurements (share
  automatically; delete what's shared); full recordings (how many to keep on the phone;
  whether to ask about uploading after each ride; deleting shared recordings, done by
  hand within 30 days); the account (sign in with Apple, Google or an email link; sign
  out; delete account); the privacy policy.
- **History**: the rides on the phone (`Application Support/rides.json`; a ride outlives its
  recording), then, under "Shared, not on this phone", rides deleted from the phone that
  still have something shared. A row is titled by where the model heard the ride start and
  end ("Millbrae → San Jose Diridon"; "Unknown trip" if it named no stop), then the date,
  length and size (or "recording not kept"), then a share icon each for its Train
  Measurements and its full recording: green shared, grey not, faint grey can't be (too
  large, or not on the phone), blue sharing (with the upload's progress), orange waiting,
  red failed. Tapping a row opens the ride: the same, the trip and car if they were typed
  in, Play back (or why not), each kind of sharing with its state in words and Share
  (asking first, as in [accounts-and-uploads.md](accounts-and-uploads.md)) or Unshare
  (confirmed first; asks for that ride's to be deleted), and Delete from this phone. A ride
  that's only shared offers just Unshare. A long press offers the same actions; swiping
  left deletes. Replay (not while recording) plays a finished recording back through the
  model, to watch the beliefs without being on a train.

  What's shared is kept on the phone (rides.json; the rides not on the phone in
  `shared-elsewhere.json`, made from the server's copy of their measurements) and brought
  up to date from the server (`GET /v1/measurements`, `GET /v1/uploads`, with the device
  secret and the account if signed in) when History appears, when it's pulled down and
  when the app comes to the front. Anything marked for deletion shows as not shared;
  sharing it again undoes that.

## Online inference (experimental)

While recording, the app runs `phone-sensor-fusion`'s model on the live sensors and shows
its belief about the last second, using only the past: speed (a number and the whole
distribution), what the train is doing (moving, or where in the stop cycle), what the phone
is doing (resting / held / handled), and where on the line it thinks it is (station and
direction after each door opening, and distance since).

The belief is about the second that has just ended. One measurement arrives late: the
accelerometer's line spectrum (wheel-rotation lines) uses a 5 s window centred on its
second, so it is ~3 s behind. The engine therefore keeps a *settled* belief, 3 s back, that
has every measurement, and each second steps a copy of it forward through the seconds
since with everything else that is already in; that copy is what is shown. Stops, leg
distances and the station layer advance with the settled belief, so a door opening shows
at once as "door-open chime" but is counted, and the station updated, about 3 s later.

`ios/TrainRecorder/Inference/` is a Swift re-implementation: `Features.swift` (per-second
measurements, with Accelerate for the FFTs), `Engine.swift` (phone state, along-track
acceleration, the HMM's forward step, distance, and the station layer), `Drivers.swift`
(live and replay). The fitted model and the track come from `model.json` in the bundle,
written by `just ios-model`. Nothing else is shared with the Python code, on purpose (see
AGENTS.md).

Cost: one second of data takes a few milliseconds to process (a replay runs at several
hundred times real time on a Mac), done once a second on a utility-priority queue; the
sensor callbacks only append to buffers.

What the online version can't do like the offline pipeline, and does instead:

- the along-track axis of a resting phone, and gravity, are re-estimated every second from
  the stretch so far;
- which way to read the acceleration, and its zero, come from a station stop inside the
  stretch when there has been one (zero = the level while the doors are open; the train
  braked into the stop and pulls away from it), otherwise from how the acceleration goes
  with the filter's own speed changes; until one of those is clear the accelerometer isn't
  used;
- a stop is the settled probability of the door-open chime crossing ½;
- distance is the expected distance given everything so far, carried forward with the
  filter (not the sum of filtered speeds).

### Trying it in the simulator

```sh
just ios-model            # in the nix shell: model.json + reference features
just ios                  # generate the Xcode project
bin/ios-sim-replay        # outside the nix shell: build, load data/recordings/raw, replay, compare
```

`bin/ios-sim-replay` copies the recordings into the simulator's copy of the app, replays
each flat out (launch arguments `-autoreplay <id> -replayspeed max -dump`), and runs
`python -m app.compare` on the dumps: feature-by-feature agreement with the Python
pipeline, then the app's online speed, leg distances and stations against the labels and
the map. To watch instead, open the app in the simulator and press a recording's replay button in History.

The model isn't on the phone-loading path yet: `model.json` is simply built into the app,
and replay only sees recordings that are in the app's own Documents folder.

## Setup

```sh
just ios-config   # once the backend is deployed (docs/deploy.md): ios/Config.xcconfig (gitignored)
just ios          # generate the Xcode project (xcodegen) and open it; then run on the phone
```

`ios/project.yml` is the source of truth for the Xcode project; the `.xcodeproj` is
generated and not checked in. Re-run `just ios` after adding files, or after `just ios-config`.

The app holds no credential: uploads go through the backend, which hands out signed URLs
(docs/api.md). `Config.xcconfig` only says where the backend is, and Identity Platform's
API key (restricted to this app's bundle id) and Google client id for sign-in, and the public
site's host (the privacy policy link, `infra/hosting.tf`). Without it
the app records and replays as usual but can't upload or sign in.

Build from a shell outside the nix dev shell: its linker breaks Xcode's link step.

## A recording

`Documents/recordings/<id>/` on the phone (also visible in the Files app, and in Finder
with the phone plugged in), uploaded as `gs://train-fusion-recordings/raw/<id>-<8 hex>/`. `<id>` is
the start time in UTC, e.g. `20261115T181502Z`.

| File | |
|---|---|
| `audio_000.m4a` | AAC-LC, mono, 96 kbit/s, 48 kHz (the phone's own mic, no gain control or voice processing). ~45 MB/hour |
| `audio_timing.csv.gz` | `segment,frame,frames,uptime_s`: one row per captured buffer (~0.1 s): frame `frame` of `audio_<segment>.m4a` was captured at `uptime_s` |
| `accel.csv.gz` | `uptime_s,x_g,y_g,z_g`: raw accelerometer, 100 Hz, in g (gravity included) |
| `gyro.csv.gz` | `uptime_s,x_rad_s,y_rad_s,z_rad_s`: raw gyroscope, 100 Hz |
| `mag.csv.gz` | `uptime_s,x_ut,y_ut,z_ut`: raw magnetometer, 100 Hz, in µT. Uncalibrated: the earth's field plus the phone's own bias and anything magnetic nearby |
| `baro.csv.gz` | `uptime_s,pressure_kpa,relative_altitude_m`: barometer, about 1 Hz. The altitude is iOS's, relative to the start of the recording. Empty if "Motion & Fitness" access was refused |
| `device_motion.csv.gz` | iOS's own fusion of the motion sensors, 100 Hz (below) |
| `clock.csv` | `uptime_s,unix_s,ntp_offset_s,ntp_round_trip_s`: how uptime maps to clock time (below) |
| `meta.json` | device, app version, start/end, audio format and input, sample counts, and `ride`: what the rider typed in about the train (`trip_number`, `car_number`, `car_position`: `unspecified` / `upper` / `lower` / `vestibule`), or null; and `inferred` (below). Uploaded last: if it's in the bucket, everything is |

`meta.json`'s `inferred` is what the app's model believed when the recording was stopped
(null if it wasn't stopped properly, or the model never ran): `stops`, one per door opening
it heard, each with `doors_opened_uptime_s`, `doors_closed_uptime_s` (the start of each
chime; closed may be null) and `station`, its best guess; `direction`; and
`station_confidence`, the probability it gave the last stop's station and the direction.
It's a convenience (History's titles come from it), not ground truth: the model in the app
changes, and the recording can always be replayed through a newer one.

`device_motion.csv.gz` is what iOS itself makes of the motion sensors (`CMDeviceMotion`).
It isn't just a function of the raw files: iOS has been estimating the gyroscope's and the
magnetometer's biases since long before the recording started, which a recording of a phone
lying still can't reproduce. Columns after `uptime_s`:

- `qx,qy,qz,qw`: the attitude quaternion as `CMAttitude` reports it, relative to the frame
  named in `meta.json` (`motion.device_motion_reference_frame`; normally
  `xMagneticNorthZVertical`: x to magnetic north, z up). Check the rotation's direction
  against the gravity columns before relying on it;
- `rot_*_rad_s`: rotation rate with the gyro bias removed;
- `gravity_*_g`, `user_accel_*_g`: the accelerometer reading split into gravity and the rest;
- `mag_*_ut`, `mag_accuracy`: the magnetic field with the phone's own bias removed, and how
  good iOS thinks that calibration is (-1 uncalibrated, 0 low, 1 medium, 2 high);
- `heading_deg`: compass heading of the phone's y axis, 0–360 from magnetic north.

On a train the steel and the traction currents disturb the magnetic field, so heading and
the attitude's yaw can be off; `mag_accuracy` is the hint.

Axes are the phone's (x right, y up, z out of the screen), so they depend on how the
phone is lying.

Usually there is one audio segment. If capture is cut off and resumes (a phone call, a
change of audio route), a new segment file starts; `audio_timing.csv.gz` says when each
one's frames were captured, so a gap is visible rather than silently closed up.

## Timestamps

Every sample is stamped with **uptime**: seconds on the phone's monotonic clock, the one
the sensors and the audio hardware stamp their own data with. It has nothing to do with
the time of day, and never jumps. `clock.csv` ties it to clock time, in two steps:

- every row: at `uptime_s`, the phone's system clock read `unix_s` (every 10 s);
- rows with `ntp_offset_s`: an NTP exchange (time.apple.com) at that moment found true
  time to be `unix_s + ntp_offset_s`, to within about `ntp_round_trip_s / 2`. Measured
  three times at the start, every 2 minutes, and at the end, whenever there's a network.

So for a sample at uptime `u`: **true unix time = u + (unix_s + ntp_offset_s − uptime_s)**,
using an NTP row (the one with the smallest round trip is the most accurate; they should
all agree to within their round trips, and comparing them over a ride shows any drift of
the monotonic clock, typically a few ms per hour). With no NTP rows (no network for the
whole recording), use `unix_s − uptime_s` from any row: the phone's own clock, usually
within a few tens of ms but unverified.

For audio: frame `n` of a segment was captured at the `uptime_s` of its buffer's row plus
`(n − frame) / sample_rate`. Decode the `.m4a` with something that honours the MP4 edit
list (ffmpeg, and so librosa / torchaudio / PyAV, do), so that decoded sample 0 is frame 0;
a decoder that ignores it yields 2112 extra samples (44 ms) at the front. `meta.json`'s
`audio.input_latency_s` is the hardware's reported capture latency, if you want to
subtract it (a few ms).

`meta.json` also has `*_first_sample_latency_s`: how long after its own timestamp the
first sample of each stream reached the app. A few ms to ~0.1 s means the stream's
timestamps are on the uptime clock as assumed.

## What can go wrong

- **Killed mid-recording** (crash, or out of battery; swiping the app away is handled):
  the sensor and clock files are fine up to the last few seconds (`gzip -dc` complains
  about the truncated end but outputs the data), but the last audio segment is unplayable
  (an `.m4a` needs its closing index). The list shows such a recording as "not stopped properly".
- **Uploads** run in a background URL session, so they finish with the app closed; what's in
  flight is in `Application Support/uploads.json`. A failed upload can be retried: objects
  already in the bucket are skipped, never replaced. In the bucket a recording is
  `raw/<id>-<8 hex>/` (the backend adds the suffix so two phones can't collide).
- Deleting in the app deletes from the phone only.
