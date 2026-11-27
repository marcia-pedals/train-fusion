"""Second layer: where on the line are we? An HMM over station stops.

The first layer (model/infer.py) knows nothing about the map; it reports when the doors
opened and how far the train ran in between. This layer explains those with the map:

  hidden state at the j-th door opening:  (station, direction of travel)
  transition j -> j+1:                    to any station further along in that direction
                                          (a skipped station costs P_SKIP)
  measurements of a transition a -> b:
    distance   the first layer's distance for the leg ~ N(map distance a..b, sd), with
               sd = DIST_REL x distance (+) DIST_ABS: the odometry's cross-validated error
    yaw        (phone resting only) the rotation rate about the vertical during the leg
               ~ N(direction x speed x track curvature at the position the leg hypothesis
               puts us, YAW_SD), a robust version, discounted for correlation
    heading    (recordings with iOS's attitude; phone resting) the compass heading of
               travel against the track's heading at that position and direction
    baro       (recordings with a barometer; any phone state) pressure altitude against
               the track's elevation along the leg, up to a free offset per leg
  measurement at a stop: heading again, against the track's heading at each station.
  Whatever a recording lacks simply contributes nothing, so older recordings are
  treated exactly as before.

Start: any station, either direction, equally likely. The forward pass gives, after every
door opening, the probability of each station using only the past, which is what a phone
could show live; between stops, position = that station + direction x distance since.
With `causal=True` its inputs are causal too: the door openings are read off the first
layer's filtered modes, and at each opening the whole layer is run again on the first
layer's speeds smoothed over everything up to that opening (Result.v_upto), so every leg so
far is measured with all that is known by then. `filt`, `now_filt` and `p_real_filt` are
then what a phone running both layers live would show at each opening.

Not every door opening the first layer finds is a stop at a station (a chime misheard, doors
reopened during a hold): each may be false, with prior P_FALSE, and then the leg runs on to
the next opening. Whether openings were stops is decided by the distances, which a false
one splits into two legs that fit no pair of stations; the other measurements only say
which stations. filt and smooth are given that the opening was a stop; p_real_filt and
p_real say how likely that was. `path` is instead the single most likely explanation of all
the openings together (which were stops, and at which stations): one consistent story,
where p_real >= 0.5 taken opening by opening need not be.

This two-layer split is exact inference for the joint model only in so far as the map says
nothing about speed (we ignore speed limits) and the first layer's stop detections are
right or false (not missed: a stop it missed reads as a station passed); in exchange each
layer is small enough to read.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from features import motion, phone_state
from labels.frames import MOVING, OPEN, OPEN_CHIME
from maps import track as trackmod
from model.infer import Result
from model.states import DT

ALL = ("yaw", "heading", "baro")  # the optional measurements; distance is always used
P_SKIP = 0.3  # each station passed without stopping
DIST_REL, DIST_ABS = 0.04, 60.0  # sd of a leg's distance: 4 % and 60 m (where trains stop along a platform)
P_UNEXPLAINED = 1e-6  # a leg that fits nothing (a bad distance): any hop ahead is then equally likely
P_FALSE = 0.05  # a door opening the first layer found that wasn't a stop at a station
YAW_SD = 0.004  # rad/s: gyro noise and the bias left after removing the stretch median
YAW_OUTLIER = 0.05  # share of yaw readings that aren't the track (a nudge of the phone)
YAW_WEIGHT = 0.2  # consecutive seconds of yaw residual are correlated: count 1 in 5
MIN_YAW_SPEED = 5.0  # m/s; below this v x curvature is too small to matter
# heading (recordings with iOS's attitude, phone resting): on two rides it was within 20
# degrees of the track always, but off by a steady 13 degrees for one whole stretch. A
# steady offset is poison for telling one place from another by its track direction (with
# sd 12 it made ride C worse), so the sd is set wide: heading then decides
# north- or southbound and barely votes on position.
HEADING_SD = 30.0  # degrees
HEADING_OUTLIER = 0.02
HEADING_WEIGHT = 0.05  # its error is a slow offset, not noise: count 1 second in 20
# ... and sometimes not a steady one: on one ride the compass drifted 150 degrees over the
# ride (iOS still rating its magnetometer calibration high), which made heading decide the
# wrong direction. So each leg's heading (and each stop's) may be off altogether, and then
# says nothing; with this anywhere from 0.05 to 0.5 that ride came out right.
HEADING_P_OFF = 0.2
HEADING_VS_ANY = np.log(360 / (np.sqrt(2 * np.pi) * HEADING_SD))  # Gaussian peak against any heading


def _heading_off(ratio):
    """Heading log-likelihood against any heading -> the same, allowing that it's off."""
    return np.logaddexp(np.log1p(-HEADING_P_OFF) + ratio, np.log(HEADING_P_OFF))
# barometer (recordings with one; any phone state): pressure altitude against track
# elevation, free offset per leg (weather, the car's own pressure); residual 2.4-3.7 m
BARO_SD = 3.5  # m
BARO_OUTLIER = 0.05
BARO_WEIGHT = 0.05  # the car's pressure wanders over tens of seconds: count 1 second in 20
MIN_BARO_SPEED = 2.0  # m/s: leave out the dwell, where opening doors step the pressure


@dataclass
class Localisation:
    t_stop: np.ndarray  # [J] frame of each door opening
    leg_m: np.ndarray  # [J-1] first-layer distance of each leg
    filt: np.ndarray  # [J, n_stations, 2] P(station, direction | stops so far); direction 0 = south
    smooth: np.ndarray  # [J, n_stations, 2] given all stops
    # filt and smooth are given that the opening was a stop at a station; how likely that was:
    p_real_filt: np.ndarray  # [J] given the stops so far
    p_real: np.ndarray  # [J] given all
    # where the train last stopped, as known right after each opening: P(the last real stop
    # among openings 0..j was at station, direction | openings 0..j), not conditioned on j
    # being real; the rest of the mass is "no real stop yet"
    now_filt: np.ndarray  # [J, n_stations, 2]
    # the most likely explanation of all the openings at once (Viterbi): the station of
    # each opening it calls a stop, -1 for those it calls false; and the direction travelled
    path: np.ndarray = None  # [J] int
    path_dir: int = -1  # 0 south, 1 north, -1 none


def _robust(r, outlier, weight):
    """Summed log-likelihood of standardised residuals r: Gaussian with a flat floor."""
    return float(weight * np.sum(np.log((1 - outlier) * np.exp(-0.5 * r * r) + outlier * 0.05)))


def _leg_loglik(tr, dist, v, meas, use, t0, t1, s_a, s_b, d_meas):
    """log p(what was measured along the leg | it ran from s_a to s_b), beyond its length.
    dist, v: the first layer's odometry and speed."""
    tt = np.arange(t0, t1)
    direction = np.sign(s_b - s_a)
    # stretch the first layer's odometry so the leg ends exactly at b
    pos = s_a + direction * (dist[tt] - dist[t0]) * abs(s_b - s_a) / d_meas
    v = v[tt]
    ll = 0.0
    if "yaw" in use:
        ok = np.isfinite(meas["yaw"][tt]) & (v > MIN_YAW_SPEED)
        if ok.sum() >= 5:
            pred = direction * v[ok] * tr.curvature_at(pos[ok])
            ll += _robust((meas["yaw"][tt][ok] - pred) / YAW_SD, YAW_OUTLIER, YAW_WEIGHT)
    if "heading" in use and meas["heading"] is not None:
        ok = np.isfinite(meas["heading"][tt])
        if ok.sum() >= 5:
            diff = (meas["heading"][tt][ok] - tr.heading_at(pos[ok], direction) + 180) % 360 - 180
            ll += _heading_off(_robust(diff / HEADING_SD, HEADING_OUTLIER, HEADING_WEIGHT)
                               + HEADING_WEIGHT * ok.sum() * HEADING_VS_ANY)
    if "baro" in use and meas["baro"] is not None:
        ok = v > MIN_BARO_SPEED
        if ok.sum() >= 20:
            r = meas["baro"][tt][ok] - tr.elevation_at(pos[ok])
            ll += _robust((r - np.median(r)) / BARO_SD, BARO_OUTLIER, BARO_WEIGHT)
    return ll


def _stop_loglik(tr, meas, use, t):
    """[n_stations, 2] log p(heading measured around a door opening | station, direction)."""
    n = len(tr.stations)
    out = np.zeros((n, 2))
    if "heading" in use and meas["heading"] is not None:
        h = meas["heading"][max(t - 15, 0):t + 15]
        h = h[np.isfinite(h)]
        if len(h) >= 5:
            r = np.radians(h)
            here = np.degrees(np.arctan2(np.sin(r).mean(), np.cos(r).mean()))
            for d, direction in enumerate((1, -1)):
                diff = (here - tr.heading_at(tr.station_s, direction) + 180) % 360 - 180
                z = diff / HEADING_SD
                out[:, d] = _heading_off(np.log((1 - HEADING_OUTLIER) * np.exp(-0.5 * z * z) + HEADING_OUTLIER * 0.05)
                                         + HEADING_VS_ANY)
    return out


def _span_logw(tr, dist, v, meas, use, t0, t1, d, n_false, p_false):
    """[n, n] log p(a span of the first layer's output | it ran from station a to station b),
    including that n_false door openings inside it were false and the one at its end real.

    Whether openings were stops is decided by the distance alone: the span's total is what
    the distance (and the skip and false priors) give it, and the other measurements only
    move weight between the station pairs within it, like the heading at a stop. (Let them
    vote on it too and a ride whose yaw or heading fits the track badly everywhere is
    explained best by calling most of its stops false.)"""
    n = len(tr.stations)
    d = max(d, 1.0)
    sd = np.hypot(DIST_REL * d, DIST_ABS)
    ld = np.full((n, n), -np.inf)  # distance and priors
    ls = np.zeros((n, n))  # the other measurements
    for a in range(n):
        for b in range(n):
            if a == b:
                continue
            true_d = abs(tr.station_s[b] - tr.station_s[a])
            if abs(true_d - d) > 6 * sd:
                continue
            ld[a, b] = -0.5 * ((d - true_d) / sd) ** 2 - np.log(sd) + (abs(b - a) - 1) * np.log(P_SKIP)
            if use:
                ls[a, b] = _leg_loglik(tr, dist, v, meas, use, t0, t1, tr.station_s[a], tr.station_s[b], d)
    if n_false:
        # where the false openings fell: anywhere along the leg (ordered uniforms)
        D = np.abs(tr.station_s[None, :] - tr.station_s[:, None])
        np.fill_diagonal(D, 1.0)
        ld += math.lgamma(n_false + 1) - n_false * np.log(D)
    total = _lse(ld, axis=None)
    if np.isfinite(total):
        lw = ld + ls
        lw += total - _lse(lw, axis=None)
    else:  # nothing explains it: its distance anywhere on the line
        lw, total = ld, -np.log(tr.station_s.max() - tr.station_s.min())
    # an unexplained span: any hop ahead, P_UNEXPLAINED as likely as all the explanations
    lw = np.logaddexp(lw, total + np.log(P_UNEXPLAINED / (n * (n - 1))))
    np.fill_diagonal(lw, -np.inf)
    return lw + _times_log(n_false, p_false) + np.log1p(-p_false)


def _times_log(m, p):
    """m x log p, with 0 x log 0 = 0."""
    m = np.asarray(m, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(m == 0, 0.0, m * np.log(p))


def _lse(x, axis):
    m = np.max(x, axis=axis, keepdims=True)
    m = np.where(np.isfinite(m), m, 0.0)
    with np.errstate(divide="ignore"):
        out = np.log(np.sum(np.exp(x - m), axis=axis, keepdims=True)) + m
    return out.reshape(()) if axis is None else np.squeeze(out, axis=axis)


def _forward(la, lw):
    """la [n, 2] log belief at the earlier stop, lw [n, n] span -> [n, 2] at the later one.
    Direction 0 = south (station index increases), 1 = north."""
    south = np.triu(np.ones(lw.shape, bool), 1)
    return np.stack([_lse(la[:, 0, None] + np.where(south, lw, -np.inf), axis=0),
                     _lse(la[:, 1, None] + np.where(south.T, lw, -np.inf), axis=0)], axis=1)


def _backward(lb, lw):
    south = np.triu(np.ones(lw.shape, bool), 1)
    return np.stack([_lse(np.where(south, lw, -np.inf) + lb[None, :, 0], axis=1),
                     _lse(np.where(south.T, lw, -np.inf) + lb[None, :, 1], axis=1)], axis=1)


def openings(res: Result, causal: bool) -> np.ndarray:
    """Frames where the first layer's doors start opening, in the Viterbi path or (causal) the
    filtered most probable mode: where it enters the door-open chime, or enters doors open
    from standing. The second is for stops where no chime is heard (this car's doors
    shut): the filtered belief passes through the chime state without it ever being the
    most probable. From moving it doesn't count: doors don't open at speed (it is what a
    platform before boarding, read as a fast train, looks like)."""
    mode = np.argmax(res.mode_p_filt, axis=1) if causal else res.path_mode
    prev = np.r_[MOVING, mode[:-1]]
    chime = (mode == OPEN_CHIME) & (prev != OPEN_CHIME)
    doors = (mode == OPEN) & ~np.isin(prev, [OPEN_CHIME, OPEN, MOVING])
    return np.flatnonzero(chime | doors)


def localise(res: Result, f: dict, use=ALL, causal=False, p_false=P_FALSE) -> Localisation:
    """`use`: which optional measurements to include; each also needs the recording to
    have the sensor (and, for yaw and heading, the phone to be resting). `causal`: what a
    phone could show live at each opening (see above). `p_false`: prior that a door opening
    the first layer found was not a stop at a station (0: trust them all).

    Causal: `smooth` and `p_real` are from the last opening's run (given everything up to it)."""
    tr = trackmod.load()
    meas = {"yaw": motion.extract(f, res.phone[:, phone_state.RESTING])["yaw_rate"] if "yaw" in use else None,
            "heading": res.travel_heading if res.travel_heading is not None and np.isfinite(res.travel_heading).any() else None,
            "baro": f["baro_alt"].astype(float) if "baro_alt" in f else None}
    t_stop = openings(res, causal)
    if not causal:
        return _localise(tr, meas, use, t_stop, res.distance, res.v, p_false)
    runs = []
    for j, tj in enumerate(t_stop):
        v = res.v_filt.copy()  # (frames after tj aren't looked at)
        v[:tj + 1] = res.v_upto(int(tj))
        runs.append(_localise(tr, meas, use, t_stop[:j + 1], np.r_[0, np.cumsum(v * DT)], v, p_false))
    if not runs:
        return _localise(tr, meas, use, t_stop, res.distance_filt, res.v_filt, p_false)
    last = runs[-1]
    return Localisation(t_stop=t_stop, leg_m=np.array([r.leg_m[-1] for r in runs[1:]]),
                        filt=np.array([r.filt[-1] for r in runs]), smooth=last.smooth,
                        p_real_filt=np.array([r.p_real_filt[-1] for r in runs]), p_real=last.p_real,
                        now_filt=np.array([r.now_filt[-1] for r in runs]))


def _localise(tr, meas, use, t_stop, dist, v, p_false) -> Localisation:
    """The layer over the openings t_stop, given the first layer's odometry and speed."""
    n = len(tr.stations)
    leg_m = np.diff(dist[t_stop])
    J = len(t_stop)
    if J == 0:
        z = np.zeros((0, n, 2))
        return Localisation(t_stop=t_stop, leg_m=leg_m, filt=z, smooth=z, p_real_filt=np.zeros(0), p_real=np.zeros(0),
                            now_filt=z, path=np.zeros(0, int))
    # spans between two real stops k < j, with the j - k - 1 openings between them false
    # (any number of them: each costs a factor p_false; none at all when that is 0)
    spans = [(k, j) for j in range(J) for k in range(j) if p_false > 0 or k == j - 1]
    logw = {(k, j): _span_logw(tr, dist, v, meas, use, t_stop[k], t_stop[j], dist[t_stop[j]] - dist[t_stop[k]], j - k - 1, p_false)
            for k, j in spans}
    # heading at the stop says which station, not whether it was one: mean 1 over the states
    loge = []
    for j in range(J):
        e = _stop_loglik(tr, meas, use, int(t_stop[j]))
        loge.append(e - _lse(e, axis=None) + np.log(e.size))
    line_m = tr.station_s.max() - tr.station_s.min()
    # a false opening with no real stop after it (before the first, after the last):
    # anywhere on an average leg; times how many
    l_open = lambda m: _times_log(m, p_false * (n - 1) / line_m)
    l_real = np.log1p(-p_false)
    # forward: la[j] = log p(openings up to j, j real, state at j)
    la = np.zeros((J, n, 2))
    for j in range(J):
        terms = [np.full((n, 2), l_open(j) + l_real - np.log(2 * n))]  # the first real stop
        terms += [_forward(la[k], logw[k, j]) for k in range(j) if (k, j) in logw]
        la[j] = _lse(np.stack(terms), axis=0) + loge[j]
    # backward: lb[k] = log p(openings after k | k real, state at k)
    lb = np.zeros((J, n, 2))
    for k in range(J - 1, -1, -1):
        terms = [np.full((n, 2), l_open(J - 1 - k))]  # the last real stop
        terms += [_backward(loge[j] + lb[j], logw[k, j]) for j in range(k + 1, J) if (k, j) in logw]
        lb[k] = _lse(np.stack(terms), axis=0)
    lreal = np.array([_lse(la[j], axis=None) for j in range(J)])
    lz = _lse(lreal + l_open(J - 1 - np.arange(J)), axis=0)
    # p(j real | openings up to j): against "j false", every real stop before it (or none)
    p_real_filt = np.ones(J)
    for j in range(J):
        lfalse = _lse(np.r_[l_open(j + 1), lreal[:j] + l_open(j - np.arange(j))], axis=0)
        p_real_filt[j] = 1 / (1 + np.exp(lfalse - lreal[j])) if np.isfinite(lreal[j]) else 0.0
    # the last real stop among 0..j is k: k real, the j - k openings after it false
    now_filt = np.zeros((J, n, 2))
    for j in range(J):
        terms = np.stack([la[k] + l_open(j - k) for k in range(j + 1)])
        lnone = l_open(j + 1)
        lall = np.logaddexp(_lse(terms, axis=None), lnone)
        now_filt[j] = np.exp(_lse(terms, axis=0) - lall)
    post = la + lb
    p_real = np.array([np.exp(_lse(post[j], axis=None) - lz) for j in range(J)])

    def norm(x):
        x = np.exp(x - _lse(x, axis=None))
        return x / x.sum()

    filt = np.array([norm(la[j]) for j in range(J)]).reshape(J, n, 2)
    smooth = np.array([norm(post[j]) for j in range(J)]).reshape(J, n, 2)
    path, path_dir = _viterbi(J, n, logw, loge, l_open, l_real)
    return Localisation(t_stop=t_stop, leg_m=leg_m, filt=filt, smooth=smooth,
                        p_real_filt=p_real_filt, p_real=np.minimum(p_real, 1.0), now_filt=now_filt,
                        path=path, path_dir=path_dir)


def _viterbi(J, n, logw, loge, l_open, l_real):
    """The forward pass of _localise with max for sum, and back again: ([J] station of each
    opening in the most likely explanation or -1 where it calls it false, its direction)."""
    path = np.full(J, -1)
    if J == 0:
        return path, -1
    allowed = (np.triu(np.ones((n, n), bool), 1),)
    allowed += (allowed[0].T,)
    m = np.zeros((J, n, 2))
    back = np.full((J, n, 2, 2), -1)  # (k, a) of the real stop before (j, b, d); -1: none
    for j in range(J):
        best = np.full((n, 2), l_open(j) + l_real - np.log(2 * n))  # the first real stop
        for k in range(j):
            if (k, j) not in logw:
                continue
            for d in range(2):
                x = m[k][:, d, None] + np.where(allowed[d], logw[k, j], -np.inf)  # [a, b]
                a = np.argmax(x, axis=0)
                v = x[a, np.arange(n)]
                better = v > best[:, d]
                best[better, d] = v[better]
                back[j, better, d, 0] = k
                back[j, better, d, 1] = a[better]
        m[j] = best + loge[j]
    # the last real stop is j, the openings after it false; or none real at all
    ends = np.array([np.max(m[j]) + l_open(J - 1 - j) for j in range(J)])
    j = int(np.argmax(ends))
    if not ends[j] > l_open(J):
        return path, -1
    b, d = (int(x) for x in np.unravel_index(np.argmax(m[j]), m[j].shape))
    while j >= 0:
        path[j] = b
        j, b = (int(x) for x in back[j, b, d])
    return path, d


def describe(loc: Localisation, truth: list[str] | None = None) -> list[str]:
    """One line per door opening: what the forward pass believes at that moment."""
    tr = trackmod.load()
    lines = []
    for j in range(len(loc.t_stop)):
        p = loc.filt[j]
        a, d = np.unravel_index(np.argmax(p), p.shape)
        line = (f"stop {j + 1:2d} at {loc.t_stop[j]:5d} s: {tr.stations[a]:18s} {'south' if d == 0 else 'north'}bound"
                f"  p={p[a, d]:.2f}  a stop: p={loc.p_real_filt[j]:.2f}")
        if truth and j < len(truth):
            pt = p[tr.stations.index(truth[j])].sum()
            line += f"   truth {truth[j]:18s} p={pt:.2f} {'ok' if tr.stations[a] == truth[j] else 'WRONG'}"
        if j:
            line += f"   (leg {loc.leg_m[j - 1]:5.0f} m)"
        lines.append(line)
    return lines
