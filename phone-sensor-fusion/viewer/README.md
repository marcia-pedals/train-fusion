# Viewer

A web page that puts a recording's audio, features, labels and inference on one time axis,
and plays the audio while the model's beliefs move.

```sh
python -m viewer.export     # once, and again after anything upstream changes (features, labels, params)
python -m viewer.serve      # http://127.0.0.1:3040
```

(`just psf-view` from the repo root does both.)

By default each recording is shown with the model fitted *without* it
(`data/phone-sensor-fusion/params/without-<id>.json`), i.e. what the pipeline evaluates; `python -m viewer.export
--params all` shows the model fitted on everything instead.

## What is on the page

Left, top to bottom, all sharing the time axis:

| track | |
|---|---|
| ride | the whole recording (smoothed speed; green = not moving) with the visible window; click or drag to move it |
| Audio | spectrogram 0–5 kHz at 10 frames/s. Optional lines: where the motor tone and its partial would be at the smoothed and at the filtered speed, so you can see whether the model is on the ridge |
| Accelerometer | line spectrum of the acceleration magnitude, 0–50 Hz, 1 frame/s, with the wheel-rotation partials at the model's speed. The track's caption says whether the model shown actually uses them |
| Speed | label (thick where trustworthy, thin where a guess), smoothed and filtered posterior means, labelled door openings. Behind them, selectable: the smoothed posterior over speed, the filtered posterior, the motor tone's likelihood alone, or the wheel-rotation lines' likelihood alone (what one measurement says before the HMM) |
| Mode | stacked probabilities per second: smoothed, filtered, and the label; under them, whether the rider was on the train (a label only: the model has no such state) |
| Phone | resting / held / handled: the filter's belief and the label |
| Door chimes, along-track acceleration, rumble, vibration, yaw rate, gyroscope energy | the per-second measurements. The acceleration is drawn with the sign and offset the model estimated, over the change of the smoothed speed it should match |

Right, for the second under the cursor (or under the playhead when the cursor is elsewhere):
speed tiles; the full belief over speed, filtered against smoothed; mode and phone
probabilities (the labelled one in bold); and the line, with the station layer's belief
after the latest door opening (given all stops / given the stops so far) and the best
position estimate as a dot.

**Filtered** = using only the past, what a phone could show live. **Smoothed** = using the
whole recording. Blue is smoothed and orange is filtered everywhere.

Controls: click to move the playhead; drag to pan; pinch or ⌘/ctrl + scroll to zoom;
two-finger sideways scroll to pan; space to play or pause; ← → for 10 s (shift: 1 s);
playback up to 16×. The address bar keeps `#<recording>&t=<s>&span=<s>`, so a moment can be
bookmarked or pasted into a note.

## How it is made

`export.py` runs the inference and writes, per recording, `meta.json` (every per-second
series) and four `uint8` rasters (spectrogram; the two posteriors and the tone likelihood
over speed, each second scaled to its own peak) to `data/phone-sensor-fusion/viewer/`.
`serve.py` serves those, the static page, and the recording's own `.m4a` with HTTP range
requests (which seeking needs and python's stock server lacks). `static/` is plain
HTML/CSS/JS drawing on canvases: no build step, no dependencies. Follows the system's
light/dark setting.

Audio time is mapped to recording time by the offset of the first audio frame
(`audio_t0`, ~0.19 s); the browser's AAC decoder may add up to ~45 ms of its own, well
under the one-second frames.
