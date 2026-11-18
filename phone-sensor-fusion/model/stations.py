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

This two-layer split is exact inference for the joint model only in so far as the map says
nothing about speed (we ignore speed limits) and the first layer's stop detections are
right; in exchange each layer is small enough to read.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from features import motion, phone_state
from labels.frames import OPEN_CHIME
from maps import track as trackmod
from model.infer import Result

ALL = ("yaw", "heading", "baro")  # the optional measurements; distance is always used
P_SKIP = 0.3  # each station passed without stopping
DIST_REL, DIST_ABS = 0.04, 60.0  # sd of a leg's distance: 4 % and 60 m (where trains stop along a platform)
P_UNEXPLAINED = 1e-6  # a leg that fits nothing (a false stop, a bad distance): any hop ahead is then equally likely
YAW_SD = 0.004  # rad/s: gyro noise and the bias left after removing the stretch median
YAW_OUTLIER = 0.05  # share of yaw readings that aren't the track (a nudge of the phone)
YAW_WEIGHT = 0.2  # consecutive seconds of yaw residual are correlated: count 1 in 5
MIN_YAW_SPEED = 5.0  # m/s; below this v x curvature is too small to matter
# heading (recordings with iOS's attitude, phone resting): on two rides it was within 20
# degrees of the track always, but off by a steady 13 degrees for one whole stretch. A
# steady offset is poison for telling one place from another by its track direction (with
# sd 12 it made ride C worse), so the sd is set wide: heading then decides
# north- or southbound, which it never got wrong, and barely votes on position.
HEADING_SD = 30.0  # degrees
HEADING_OUTLIER = 0.02
HEADING_WEIGHT = 0.05  # its error is a slow offset, not noise: count 1 second in 20
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


def _robust(r, outlier, weight):
    """Summed log-likelihood of standardised residuals r: Gaussian with a flat floor."""
    return float(weight * np.sum(np.log((1 - outlier) * np.exp(-0.5 * r * r) + outlier * 0.05)))


def _leg_loglik(tr, res, meas, use, t0, t1, s_a, s_b, d_meas):
    """log p(what was measured along the leg | it ran from s_a to s_b), beyond its length."""
    tt = np.arange(t0, t1)
    direction = np.sign(s_b - s_a)
    # stretch the first layer's odometry so the leg ends exactly at b
    pos = s_a + direction * (res.distance[tt] - res.distance[t0]) * abs(s_b - s_a) / d_meas
    v = res.v[tt]
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
            ll += _robust(diff / HEADING_SD, HEADING_OUTLIER, HEADING_WEIGHT)
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
                out[:, d] = np.log((1 - HEADING_OUTLIER) * np.exp(-0.5 * z * z) + HEADING_OUTLIER * 0.05)
    return out


def localise(res: Result, f: dict, use=ALL, online_distance=False) -> Localisation:
    """`use`: which optional measurements to include; each also needs the recording to
    have the sensor (and, for yaw and heading, the phone to be resting)."""
    tr = trackmod.load()
    n = len(tr.stations)
    mode = res.path_mode
    t_stop = np.flatnonzero((mode == OPEN_CHIME) & (np.r_[-1, mode[:-1]] != OPEN_CHIME))
    dist = res.distance_filt if online_distance else res.distance
    leg_m = np.diff(dist[t_stop])
    meas = {"yaw": motion.extract(f, res.phone[:, phone_state.RESTING])["yaw_rate"],
            "heading": res.travel_heading if res.travel_heading is not None and np.isfinite(res.travel_heading).any() else None,
            "baro": f["baro_alt"].astype(float) if "baro_alt" in f else None}
    J = len(t_stop)
    # log transition weights per leg: [J-1, a, b] (direction is implied by the order of a, b)
    logw = np.full((max(J - 1, 0), n, n), -np.inf)
    for j in range(J - 1):
        d = max(leg_m[j], 1.0)
        sd = np.hypot(DIST_REL * d, DIST_ABS)
        for a in range(n):
            for b in range(n):
                if a == b:
                    continue
                true_d = abs(tr.station_s[b] - tr.station_s[a])
                if abs(true_d - d) > 6 * sd:
                    continue
                lw = -0.5 * ((d - true_d) / sd) ** 2 - np.log(sd) + (abs(b - a) - 1) * np.log(P_SKIP)
                lw += _leg_loglik(tr, res, meas, use, t_stop[j], t_stop[j + 1], tr.station_s[a], tr.station_s[b], d)
                logw[j, a, b] = lw
    # state (station, dir): dir 0 = south (index increases), 1 = north
    south = np.triu(np.ones((n, n), bool), 1)
    filt = np.zeros((J, n, 2))
    alpha = np.full((n, 2), 1.0 / (2 * n))
    Ws, Es = [], []
    for j in range(J):
        if j:
            lw = logw[j - 1]
            w = np.exp(lw - lw.max()) if np.isfinite(lw).any() else np.zeros((n, n))
            w = (1 - P_UNEXPLAINED) * w / max(w.sum(), 1e-300) + P_UNEXPLAINED / (n * (n - 1))
            Ws.append(w)
            alpha = np.stack([alpha[:, 0] @ (w * south), alpha[:, 1] @ (w * south.T)], axis=1)
        e = _stop_loglik(tr, meas, use, int(t_stop[j]))
        Es.append(np.exp(e - e.max()))
        alpha = alpha * Es[-1]
        alpha /= alpha.sum()
        filt[j] = alpha
    smooth = np.zeros_like(filt)
    beta = np.ones((n, 2))
    for j in range(J - 1, -1, -1):
        g = filt[j] * beta
        smooth[j] = g / g.sum()
        if j:
            w = Ws[j - 1]
            be = beta * Es[j]
            beta = np.stack([(w * south) @ be[:, 0], (w * south.T) @ be[:, 1]], axis=1)
            beta /= beta.sum()
    return Localisation(t_stop=t_stop, leg_m=leg_m, filt=filt, smooth=smooth)


def score(loc: Localisation, truth_by_stop: dict[int, str]) -> dict:
    """How the forward pass (the belief at each stop given the past) did, on the detected
    stops that are labelled ones. truth_by_stop: index into loc.t_stop -> station."""
    tr = trackmod.load()
    js = sorted(truth_by_stop)
    right = []
    for j in js:
        a, _ = np.unravel_index(np.argmax(loc.filt[j]), loc.filt[j].shape)
        right.append(tr.stations[a] == truth_by_stop[j])
    locked = next((k for k in range(len(js)) if all(right[k:])), None)
    p_truth = [float(loc.filt[j][tr.stations.index(truth_by_stop[j])].sum()) for j in js]
    return {"stops": len(js), "right": int(sum(right)), "right_from_stop": None if locked is None else locked + 1,
            "p_truth": p_truth}


def describe(loc: Localisation, truth: list[str] | None = None) -> list[str]:
    """One line per door opening: what the forward pass believes at that moment."""
    tr = trackmod.load()
    lines = []
    for j in range(len(loc.t_stop)):
        p = loc.filt[j]
        a, d = np.unravel_index(np.argmax(p), p.shape)
        line = (f"stop {j + 1:2d} at {loc.t_stop[j]:5d} s: {tr.stations[a]:18s} {'south' if d == 0 else 'north'}bound"
                f"  p={p[a, d]:.2f}")
        if truth and j < len(truth):
            pt = p[tr.stations.index(truth[j])].sum()
            line += f"   truth {truth[j]:18s} p={pt:.2f} {'ok' if tr.stations[a] == truth[j] else 'WRONG'}"
        if j:
            line += f"   (leg {loc.leg_m[j - 1]:5.0f} m)"
        lines.append(line)
    return lines
