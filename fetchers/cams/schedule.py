"""When to fetch each camera again: lock onto its refresh cycle, back off when it fails.

Webcam images are re-published on a fixed cycle (Caltrans: every 300 s), so fetching
faster than that only repeats the same image, and fetching on a fixed timer at the same
rate gets images up to a whole cycle old. Instead, like the GTFS fetcher, each camera aims
a little after its next expected refresh, from the server's own capture time:

    next = last capture + period + margin

and if the image hasn't changed yet (304, or the same bytes), retries on a short ladder
(RETRY_LADDER_S) until it does, which also nudges `margin` up (AIMD: +MARGIN_UP_S per
early fetch, -MARGIN_DOWN_S per on-time one), so steady state is ~1 request per refresh.
The period itself is re-estimated from the capture times seen (median of recent gaps).

Failures (network errors, 5xx, 403/429) back off exponentially per camera, honouring
Retry-After; fetch.py also pauses the whole host on 403/429. A camera showing its "offline"
placeholder is polled only every DOWN_POLL_S until it comes back.
"""
from __future__ import annotations

import random
import statistics

MARGIN_S = 4.0                 # initial seconds after the expected refresh
MARGIN_MIN_S, MARGIN_MAX_S = 1.0, 90.0
MARGIN_UP_S, MARGIN_DOWN_S = 2.0, 0.25
RETRY_LADDER_S = (10, 15, 30, 60, 120)  # (sources publish up to ~60 s after their stamp)  # after an unchanged fetch; then period/2 until it changes
BACKOFF_BASE_S, BACKOFF_MAX_S = 30.0, 3600.0
DOWN_POLL_S = 900.0            # while the camera shows its placeholder
PERIOD_HISTORY = 12


def jitter(x: float, frac: float = 0.1) -> float:
    return x * random.uniform(1 - frac, 1 + frac)


class CamSchedule:
    """State for one camera. All times are unix seconds."""

    def __init__(self, period_s: float, margin_s: float = MARGIN_S):
        self.nominal = period_s
        self.period = period_s
        self.margin = margin_s
        self.capture = None     # server capture time of the newest image we have
        self.gaps: list[float] = []
        self.unchanged = 0      # consecutive fetches with no new image
        self.last_unchanged = None  # time of the latest of those
        self.failures = 0       # consecutive failed fetches
        self.down = 0           # consecutive placeholder images

    # -- outcomes. Each returns the time of the next fetch.
    def new_image(self, now: float, capture: float | None) -> float:
        """A new image. `capture` is its server-side time (Last-Modified etc.), if known."""
        if self.unchanged:
            # we had to retry, so we aimed too early: widen the margin, at least to just
            # past the last fetch that didn't have it yet (known to be too early)
            floor = (self.last_unchanged - capture + 2.0) if capture is not None and \
                self.last_unchanged is not None else 0.0
            self.margin = min(max(self.margin + MARGIN_UP_S, floor), MARGIN_MAX_S)
        else:
            self.margin = max(self.margin - MARGIN_DOWN_S, MARGIN_MIN_S)
        self.unchanged = self.failures = self.down = 0
        if capture is not None and self.capture is not None and capture > self.capture:
            gap = capture - self.capture
            # several periods may have passed (missed refreshes): count it as gap / k
            k = max(1, round(gap / self.period))
            self.gaps = (self.gaps + [gap / k])[-PERIOD_HISTORY:]
            if len(self.gaps) >= 3:
                est = statistics.median(self.gaps)
                self.period = min(max(est, self.nominal / 2), self.nominal * 2)
        if capture is not None:
            self.capture = capture
            nxt = capture + self.period + self.margin
            # if that's already past (we're late, or the capture time is old), go soon but
            # not at once; the unchanged ladder takes over if it hasn't refreshed
            return max(nxt, now + RETRY_LADDER_S[0])
        return now + self.period

    def same_image(self, now: float) -> float:
        """304, or the same bytes as last time: not refreshed yet."""
        self.unchanged += 1
        self.last_unchanged = now
        self.failures = 0
        if self.unchanged <= len(RETRY_LADDER_S):
            return now + RETRY_LADDER_S[self.unchanged - 1]
        return now + jitter(self.period / 2)

    def placeholder(self, now: float, poll_s: float | None = None) -> float:
        """The camera's "unavailable" image: the server works, the camera doesn't."""
        self.down += 1
        self.unchanged = self.failures = 0
        return now + jitter(max(poll_s or DOWN_POLL_S, self.period))

    def failed(self, now: float, retry_after: float | None = None) -> float:
        self.failures += 1
        delay = min(BACKOFF_BASE_S * 2 ** (self.failures - 1), BACKOFF_MAX_S)
        if retry_after is not None:
            delay = max(delay, min(retry_after, 6 * 3600.0))
        return now + jitter(delay, 0.2)

    @property
    def state(self) -> dict:
        return {"period_s": round(self.period, 1), "margin_s": round(self.margin, 2),
                "unchanged": self.unchanged, "failures": self.failures, "down": self.down}


# -- live cameras: a fresh frame on every request, so we choose the rate.
# Fast only around predicted train passes (passes.py); never faster than TrafficLand's own
# web viewer (2 s); slow otherwise, and slower still when no trains run.
LIVE_FAST_S = 3.0
LIVE_SLOW_S = 60.0
LIVE_NIGHT_S = 300.0
WINDOW_BEFORE_S, WINDOW_AFTER_S = 120.0, 240.0  # predictions tend to be early, trains late
SERVICE_HOURS = (4, 2)  # local; Caltrain runs ~04:30-01:30


def in_service(local_hour: int) -> bool:
    start, end = SERVICE_HOURS
    return local_hour >= start or local_hour < end


def live_next(now: float, windows, local_hour: int,
              fast_s: float = LIVE_FAST_S, slow_s: float = LIVE_SLOW_S) -> tuple[float, str]:
    """(next fetch time, why) for a live camera. `windows` is [(start, end, trip)] of
    predicted passes, or None when there's nothing to predict from."""
    if windows is None:
        if in_service(local_hour):
            return now + fast_s, "no predictions: fast in service hours"
        return now + LIVE_NIGHT_S, "night"
    for start, end, trip in windows:
        if start <= now < end:
            return now + fast_s, f"pass window {trip}"
        if start > now:
            return min(now + slow_s, start), "between passes"
    return now + (slow_s if in_service(local_hour) else LIVE_NIGHT_S), "no passes ahead"
