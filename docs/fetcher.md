# Fetcher (archiver)

`fetch.py` archives Caltrain's two 511.org GTFS-RT feeds and the daily static GTFS,
byte-for-byte, so everything downstream can be rebuilt from the archive. The deployed one
runs on a GCP VM and uploads to a bucket ([deploy.md](deploy.md)); `just sync` brings the
archive into the local `data/`.

## Usage

```sh
python fetch.py            # runs forever; -v prints scheduler state
                           # (--bucket B --metrics: the deployed mode, see deploy.md)
pbcat data/rt/<day>/vehiclepositions/<stamp>.pb      # read a snapshot (--json for jq)
python inspect_rt.py data/rt/<day>                   # freshness/cadence summary
```

Everything lands under `data/` (gitignored):

```
data/rt/<service_date>/<feed>/<utc_stamp>.pb    raw response body, unmodified
data/rt/<service_date>/fetch_log/<utc_minute>_<instance>.jsonl
                                                per request: timing, status, sha256,
                                                entity_ts, age_at_request_s, rate-limit headers
data/gtfs/<local_date>/GTFSTransitData_CT_<utc_stamp>.zip   static GTFS, daily
data/gtfs/fetch_log/<utc_minute>_<instance>.jsonl
```

Files are never changed once finished: the log starts a new file every minute, named by
the process (`<host>-<random>`), so archives from any number of fetchers merge by union.
Archives from before 2026-11-11 have one `fetch_log.jsonl` per directory instead.
`fetch.read_log(dir)` reads both. If either feed's thread crashes, the process exits
nonzero (for systemd to restart it) rather than carrying on with one feed.

`service_date` = local time minus 3 h, so after-midnight trains file under the previous day.
Non-200 response bodies are kept too, as `<stamp>.pb.http<status>`.

## What we learned about the 511 feeds (2026-11-07)

**Quota.** 60 requests/hour per key, by 511's statement. There's no combined endpoint, so
each RT feed gets its own key at one fetch per 60 s. The static GTFS zip (`datafeeds`)
borrows one vehicle-position slot per day (first fetch on or after 03:00 local, when no trains
run). The `RateLimit-Remaining` header is noisy: it goes up and down between consecutive
calls, as if several backend servers each keep their own count, so it can't be used to
measure real usage. We log it anyway. In practice we went well over 60/hour/key during
testing without ever getting a 429. `fetch.py` still enforces 60 per rolling hour per key
itself (`RateLimiter`, seeded from the fetch log so restarts don't overspend).

**Update cadence.**
- `header.timestamp` is just the request time. Ignore it.
- Every entity in a snapshot shares one `timestamp`: 511 re-stamps the whole feed every
  **~15.1 s**, and serves it within ~0.5-1.5 s of that timestamp.
- But the **content only changes on every other stamp (~30.3 s)**. The stamps in between
  repeat the previous content exactly (no train moves, no prediction changes). Presumably
  511 polls every 15 s and its source updates every 30 s. Measured with
  `experiments/content_cadence.py`: strictly alternating over 20 stamps on both feeds.
- Trip updates and vehicle positions are stamped on **separate cycles**. Sometimes they
  match to within a second; sometimes they're ~5-13 s apart.

**Fetch scheduling** (`Scheduler` in `fetch.py`). Fetching on a fixed :00 schedule gives
content 0-30 s old (~15 s on average), depending on luck. Instead, each feed:
- aims `margin` after a content update: `last content stamp + 60 s` (2 content cycles),
  relative to the data's own timestamp, so phase drift is corrected every fetch;
- finds which stamps carry content (the parity) with a **parity check** at startup and
  every 15 min: one extra fetch a stamp after a normal one; if content changed, that one is a
  content stamp, else the first was. A slot is skipped beforehand to pay for it;
- snaps every returned stamp onto the content lattice, so arriving one stamp early or late
  doesn't lose the parity;
- adapts the margin AIMD-style: +1 s when a fetch arrives before the stamp it aimed at
  (serve lag or the upstream period grew), -0.05 s otherwise.

In simulation (`content_age` = time since the content we got actually changed), this gives
~2-5 s content age at fetch for periods 15.0-16 s and serve lags up to 3 s, never over
60 requests in any rolling hour, at the cost of ~5% early fetches while the margin probes.
The log records `margin_s`, `aimed_ets`, `role` (normal / parity_probe) and `content_sha256`
(content with timestamps stripped) per fetch, plus a `parity_check` event line.

Normal fetches also log `content_ets` (added 2026-11-07; present from the first fetcher restart after that). That's the
stamp at which the fetched content first appeared: the entity stamp itself if it's a
content stamp, 15 s earlier if it's an in-between stamp. It's null while the parity is
unknown, and parity probes don't log it (their `parity_check` event settles both fetches).

**Parity isn't permanent.** On 2026-11-07, VP's parity held all day. TU's flipped once
between 10:40 and 11:40 local (`experiments/content_parity.py`), which is why the parity
check runs every 15 min, not hourly (each check costs one skipped 60 s slot, ~4 of 60
fetches per hour). Between checks, fetches after a flip land on in-between stamps, with
content 15 s staler than intended.
A logged `content_ets` is only as right as the fetcher's current parity.

**Latency is bounded by the quota.** With one fetch per 60 s, the newest content we hold
is on average ~30 s (half the gap) + content age at fetch: ~33 s, vs ~45 s for a random
phase. **Asking 511 for 120/hour/key (one fetch per 30 s content update) would bring that to
~18 s.** 240/hour would be half wasted on repeated content.

**Content quirks.**
- Trip updates: only `arrival.time`/`departure.time` + `stop_id`. No `delay`, no
  `stop_sequence`. Departure is *always* arrival + 60 s (synthetic dwell). The first
  remaining stop sometimes has a departure only. Trips that haven't started yet are included.
- Vehicle positions: lat/lon + timestamp only (no bearing, speed, stop, status).
- `vehicle.id` == `trip_id` in both feeds: there's no physical train identity.
- `route_id` is the service pattern (`Local Weekday`, `Limited`, `Express`, ...).
