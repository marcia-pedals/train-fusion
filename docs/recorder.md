# The phone recorder (`ios/`)

A one-screen iPhone app for recording a train ride from the inside: audio (to hear the
door chimes and the traction motors) plus raw accelerometer, gyroscope and magnetometer
samples and the barometer, all
with timestamps that convert to exact clock time. One button starts and stops a recording;
each recording in the list can be uploaded to `gs://train-fusion-recordings` or deleted.
Recording carries on with the app in the background and the phone locked.

Nothing in the pipeline reads the recordings yet.

## Setup

```sh
just tf apply     # once: the recordings bucket and the upload-only `recorder` service account
just ios-key      # once: its key, into ios/TrainRecorder/uploader-key.json (gitignored)
just ios          # generate the Xcode project (xcodegen) and open it; then run on the phone
```

`ios/project.yml` is the source of truth for the Xcode project; the `.xcodeproj` is
generated and not checked in. Re-run `just ios` after adding files, or after `just ios-key`
(the key is bundled only if it existed when the project was generated).

The key is inside the app, so anyone holding a build of the app has it. That's why it is
its own service account with nothing but `objectCreator` on its own bucket: the worst it
can do is add objects there. Don't distribute the app beyond your own devices.

## A recording

`Documents/recordings/<id>/` on the phone (also visible in the Files app, and in Finder
with the phone plugged in), uploaded as `gs://train-fusion-recordings/<id>/`. `<id>` is
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
| `meta.json` | device, app version, start/end, audio format and input, sample counts. Uploaded last: if it's in the bucket, everything is |

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
- **Uploads** run while the app is open (plus a short grace period in the background). A
  failed upload can be retried: objects already in the bucket are skipped, never replaced.
- Deleting in the app deletes from the phone only.
