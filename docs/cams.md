# Webcams (fetchers/cams)

`fetchers/cams/fetch.py` archives still images from public traffic cameras that can see the
Caltrain tracks, byte-for-byte, with a log line per request that records everything known
about the capture from metadata alone (no pixels are decoded). Seeing trains in the images
is a later pipeline stage.

## Usage

```sh
just fetch-cams                          # = python -m fetchers.cams.fetch; enabled cameras, forever
python -m fetchers.cams.fetch --list     # every camera we know, * = enabled, and what it sees
python -m fetchers.cams.fetch --cams scc-401837,caltrans-tv309 --max-minutes 30 -v
python -m fetchers.cams.fetch --all      # include cameras that aren't enabled (surveys)
```

Live cameras (TrafficLand) are fetched fast only around predicted train passes, which come
from the GTFS archiver's trip updates in `<rt-dir>/rt/` (default: the data dir, as on the
VM). Run locally without the archiver, pass `--rt-dir data` and keep `just sync` running,
or it falls back to fetching them fast all day.

```
data/cams/<service_date>/<cam_id>/<utc_stamp>.jpg     a new image (raw bytes)
data/cams/<service_date>/<cam_id>/<utc_stamp>.png.http404   a "camera unavailable" image
data/cams/<service_date>/<cam_id>/<utc_stamp>.http<status>  other error bodies
data/cams/<service_date>/fetch_log/<utc_minute>_<instance>.jsonl
data/cams/catalog/<local_date>/<source>_<utc_stamp>.json      camera catalogs, daily
```

As in the GTFS archive, files are never changed once written and the log is per minute per
process (`fetchers/archive.py`), so archives merge by union. `<utc_stamp>` is the request
time. An image identical to the camera's previous one (or a 304) is logged as `unchanged`
but not stored again. With `--bucket`, finished files are uploaded create-only (only `cams/`).

### The log

One line per request: `cam`, `source`, `scheduled_utc`, `request_utc`, `elapsed_s`,
`status`, `bytes`, `sha256`, `file`, plus

| field | |
|---|---|
| `quality` | `ok` new image · `unchanged` 304 or same bytes · `placeholder` the source's "camera unavailable" image · `stale` new file but an old capture · `not_image` / `truncated` · `error` |
| `capture_utc`, `capture_src` | when the frame was taken, and from what: `last-modified` (Caltrans), `exif`, or `request` (TrafficLand has no capture time; mid-request is used) |
| `age_s` | request time − capture time |
| `exif_lag_s` | Last-Modified − EXIF time (Caltrans): minutes means a re-stamped old frame |
| `image_status` | TrafficLand's `Image-Status` header |
| `jpeg` | width, height, `complete` (has its end marker), `qt` (quantization-table fingerprint: identifies the encoder), `exif_time`, `comment` |
| `headers` | Last-Modified, ETag, Cache-Control, … |
| `sched`, `cadence`, `next_in_s` | scheduler state; for live cameras why it picked the next time (`pass window <trip>`, `between passes`, …) |

## Sources and what we learned (2026-11-11)

The survey's HTML report (every camera we looked at, with frames) and its frames are kept
out of git, in `data/cams-survey/2026-11-11/report/` and `data/cams/2026-11-11/`.
In short: 47 Caltrans cameras and 32 Santa Clara County cameras within 600 m of
the tracks; the tracks are visible from 3 of those that were working (`--list`).

### Caltrans CCTV (cwwp2.dot.ca.gov)

- Catalog: `https://cwwp2.dot.ca.gov/data/d4/cctv/cctvStatusD04.json` (archived daily),
  fields documented at cwwp2.dot.ca.gov/documentation/cctv/cctv-field-description.htm.
- **Stills refresh every 5 minutes**, documented (`currentImageUpdateFrequency` = 5 for all
  773 District 4 cameras) and measured (300 ± 1 s). Each camera has its own phase.
  511.org's image proxy serves the same bytes, cached an hour (sometimes staler); QuickMap
  uses the same images. There is no fresher still.
- Last-Modified is the frame's grab time, but the file appears on the server **~25-35 s
  later** (up to ~60 s). (Other districts refresh every 1-3 min; District 4 is slow.) So we fetch ~30 s after the expected refresh, and the margin
  adapts (schedule.py). Steady state: ~1 request per new image.
- ETag + If-None-Match gives 304s. If-Modified-Since doesn't work (exact match only).
- A dead camera's image is replaced by a 320×260 "Temporarily Unavailable" JPEG with an
  error digit, re-made every 5 min. Detected by its quantization tables (`931afd90`) **and**
  size (13.4-13.8 kB): one live camera (tv458) shares those tables but its frames are
  ≥ 20 kB. About half the cameras near the tracks showed it all day.
- The EXIF DateTimeOriginal (Pacific time) is usually within 10 s of Last-Modified;
  minutes apart means an old frame re-stamped (logged as `exif_lag_s`; `stale` beyond 15 min).
- `previous/<name>-N.jpg` history images (15 or 60 min apart) are unreliable: for some
  cameras they're years old, stamped 1970 or 2041. Not used.
- Some cameras' in-image clock overlay is an hour off. Use the headers.
- 196 of 773 cameras also have an HLS video stream (wzmedia.dot.ca.gov, Wowza, 320×240-ish,
  30 fps, 10 s segments); streams 404 intermittently, apparently started on demand. None
  of the cameras that see the tracks has one, so they aren't fetched.
- **Why stills are weak for trains:** a train crosses tv309's ~100 m of visible track in
  ~5 s, so a frame every 5 min catches ~1-2 % of passes.

### Santa Clara County expressway cameras (TrafficLand)

- The county's inventory is open: `maps.santaclaracounty.gov/server/rest/services/agency/
  SCCTrafficCamera/MapServer/1` (archived daily). `Web_ID` is the TrafficLand camera id.
  The county's own roads site is behind a Cloudflare challenge.
- Images: `https://ie.trafficland.com/v2.0/<id>/huge?system=weatherbug-web&pubtoken=<token>&refreshRate=2000`
  with the per-camera token embedded in `https://www.weatherbug.com/traffic-cam/<any>/<id>`
  (one page carries ~30 nearby cameras' tokens). The county's agreement lets TrafficLand
  show the cameras free on public websites; this reuses WeatherBug's public embed.
- **A fresh 640×480 frame on every request** (tested at 2 s), with no capture time at all
  (no Last-Modified/ETag/EXIF; `Cache-Control: max-age=2`). Requests take 0.6-2.3 s.
- `Image-Status` header mirrors the outcome: 404 + an 18,283-byte "temporarily
  unavailable" PNG for a dead camera (and those requests take ~5.5 s, hence 2 requests in
  flight to this host); 401 + PNG for a bad token, which makes us re-read the page (at
  most every 5 min per camera).
- 29 of the 32 Central Expwy cameras in Mountain View were down all of 2026-11-11, and the
  other three from 13:12 (all of Central Expwy; other expressways were up). The county's
  page has said "down due to maintenance" since 9/28/26. Down cameras that matter are
  re-checked every 5 min (`recheck_s`).
- The WeatherBug tokens expire (all had changed within ~1.5 h); a 401 makes us re-read
  the page, taking every token on it.
- **Castro St @ Central Expy (NB view), `scc-401837`**, shows the Castro St level crossing
  next to Mountain View station: trains are in view ~15 s per pass. Car numbers aren't
  readable at this resolution; train type, direction and wrap colours are.
- Cadence: never faster than WeatherBug's own viewer (2 s). 3 s from 2 min before to 4 min
  after each predicted pass, 60 s between passes, 5 min at night. Predictions come from
  the trip updates (`passes.py`): within ~30 s twenty minutes ahead on the passes checked,
  ~2 min early on a late train. ~5-10× fewer requests than fetching fast all day.

### ALERTCalifornia wildfire cameras (alertca.py)

- UC San Diego's pan-tilt-zoom cameras; frames are archived on their side every ~11 s and
  kept 2 days, so we read their frame lists instead of polling an image:
  `public-camera-data/<cam>/10sec/{15,30}-min.json` (every frame) and
  `1min/12-hour.json` (~1/min). Only these lists name the frames (paths end in a content
  hash; folders aren't listable), so after an outage only 30 min can be recovered at full
  rate and 12 h at 1/min. Keep it running.
- Each frame's headers carry pan/tilt/zoom and the capture time (x-amz-meta-context), but
  the pose tag is wrong about half the time around moves, so **every frame is downloaded**
  (`mode: "all"`, ~2.2 GB/day/camera) and the tag is only logged.
- **redwoodcity-cam02** sees trains in its home view: 8 sightings on 2026-11-11 in a thin
  band above the privacy mask. Fog until ~09:15; unusable after dark (glare).
  **sanbrunomtn-cam01** glances wide over Bayshore every few minutes; no train confirmed.
- Licence CC BY-NC-ND 4.0: non-commercial, credit "ALERTCalifornia | UC San Diego".

## What the cameras told us about GTFS-RT

At Castro St and tv309, positions interpolated from GTFS-RT vehicle positions put each train
at the camera 41-65 s *after* the camera saw it; both camera clocks are within ~1 s of ours.

## Respectfulness

- One request per new image on Caltrans (~0.17 req/s for all 47 cameras), conditional.
- Per host: requests at least 1 s apart, one (Caltrans) or two (TrafficLand) in flight,
  and the whole host pauses on 403/429 (5 min, doubling to 6 h). Per camera: exponential
  backoff on errors (30 s → 1 h, jittered, honouring Retry-After), 15 min polling while
  a camera shows its placeholder.
- An honest User-Agent on Caltrans; the browser one TrafficLand's viewer would send there.
- First round spread over a minute instead of a burst at startup.
