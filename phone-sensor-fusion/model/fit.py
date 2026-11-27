"""Learn the model's parameters from labelled recordings.

Input: the per-frame measurements (features/) and per-frame labels (labels/frames.py) of
the training recordings. Output: one JSON-able dict (written to params/all.json, or to data/ for the leave-one-out fits):

  phone        features/phone_state.py's classifier
  tone, wheel  features/comb.py's tables, for the audio and the accelerometer line spectra
               (wheel: separately for a resting and for a held phone)
  chime        P(chime measurement category | that chime's mode / any other mode)
  level        rumble_db and vib_db as Gaussians whose mean depends on speed (per phone
               state for vib), with a weight < 1 that discounts the frame-to-frame
               correlation of their residuals (an HMM assumes there is none)
  accel        sd of along-track acceleration around the labelled dv/dt
  dynamics     per regime (braking, coasting, accelerating at part or at full traction)
               and per speed: P(speed change over the next second), P(next second's
               regime); P(regime | pulling away)
  dwell        mean time spent in each stopped mode

Every block has a prior (a few pseudo-observations of a sensible default), so a training
set lacking some situation, e.g. no resting phone, still yields a complete model.
"""

from __future__ import annotations

import numpy as np

from features import comb, motion, phone_state
from labels.frames import CLOSE_CHIME, MOVING, OPEN, OPEN_CHIME, POST, PRE, UNKNOWN, FrameLabels
from model import states

CHIME_EDGES = [0.15, 0.7]  # measurement (fraction of the second) -> 3 categories
# pseudo-count per category. Small: in ~4000 labelled seconds the detector never fired
# outside a chime, and we'd rather believe that than the 1/N a count of one would claim
CHIME_PSEUDO = 0.05
LEVEL_V_KNOTS = np.array([0.0, 1.5, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0])  # m/s
DYN_MAX_STEP = 6  # speed change per frame in units of DV: +-1.5 m/s^2
# regimes: the labelled speed change over 2 * REGIME_HALF + 1 s against +-A_COAST. Traction
# never gives less than ~0.35 m/s^2 and braking rarely less than ~0.2; coasting (line speed,
# drag) stays within ~0.1
REGIME_HALF = 2
A_COAST = 0.15  # m/s^2
# dynamics tables per speed, smoothed over speed (kernel_weights()). A table over 13 steps
# whose common entries (p ~ 0.1-0.3) should be known to ~+-0.03 needs ~200-300 independent
# seconds; neighbouring seconds of one ride aren't independent, so this errs high
DYN_N_EFF = 300.0  # seconds of data each speed's table should rest on
DYN_H_MIN, DYN_H_MAX = 0.25, 10.0  # m/s: kernel sd limits (one grid step; most of a speed range)
DYN_H_GRID = np.geomspace(DYN_H_MIN, DYN_H_MAX, 60)
DYN_PRIOR = 10.0  # pseudo-seconds of the regime's distribution over all speeds
CLAMP_N = 20  # seconds: a step table's centre isn't extended past the speeds with fewer than this beyond them
# the part / full traction mixture (fit_split())
SPLIT_GRID = np.arange(0.0, 38.01, 0.5)  # m/s: where its parameters are kept
SPLIT_H = 1.5  # m/s: kernel sd of the M step
SPLIT_SD_MIN = 0.03  # m/s^2
SPLIT_ITER = 200
DWELL_PRIOR_S = {PRE: 2.0, OPEN_CHIME: 6.0, OPEN: 20.0, CLOSE_CHIME: 8.0, POST: 5.0}
DWELL_PRIOR_WEIGHT = 2.0
ACCEL_PRIOR = (0.15, 50.0)  # sd m/s^2, weight in frames
MIN_LEVEL_FRAMES = 15


def _autocorr_time(res_runs: list[np.ndarray], max_lag=30) -> float:
    """Integrated autocorrelation time (frames) of residuals given as contiguous runs."""
    num, den = np.zeros(max_lag + 1), 0.0
    for r in res_runs:
        if len(r) <= max_lag + 1:
            continue
        r = r - r.mean()
        den += np.sum(r * r)
        for k in range(1, max_lag + 1):
            num[k] += np.sum(r[k:] * r[:-k])
    if den == 0:
        return 1.0
    rho = num / den
    tau = 1.0
    for k in range(1, max_lag + 1):
        if rho[k] < 0.05:
            break
        tau += 2 * rho[k]
    return float(tau)


def _runs(mask):
    edges = np.flatnonzero(np.diff(np.r_[False, mask, False].astype(int)))
    return list(zip(edges[::2], edges[1::2]))


def fit_level(xs: list[np.ndarray], labels: list[FrameLabels], masks: list[np.ndarray]) -> dict | None:
    """x ~ N(mean(v), sd): mean piecewise linear over LEVEL_V_KNOTS (knot 0 = standing)."""
    x = np.concatenate(xs)
    v = np.concatenate([l.v for l in labels])
    ok = np.concatenate([m & l.confident & np.isfinite(l.v) for m, l in zip(masks, labels)]) & np.isfinite(x)
    if ok.sum() < 10 * MIN_LEVEL_FRAMES or (ok & (v == 0)).sum() < MIN_LEVEL_FRAMES:
        return None
    knots = LEVEL_V_KNOTS
    k = np.where(v == 0, 0, 1 + np.digitize(v, (knots[1:-1] + knots[2:]) / 2))  # nearest moving knot
    means = np.full(len(knots), np.nan)
    for j in range(len(knots)):
        m = ok & (k == j)
        if m.sum() >= MIN_LEVEL_FRAMES:
            means[j] = np.median(x[m])
    good = np.isfinite(means)
    means = np.interp(knots, knots[good], means[good])
    res = x - means[k]
    sd = float(1.4826 * np.median(np.abs(res[ok] - np.median(res[ok]))))
    res_runs, at = [], 0
    for xx, m, l in zip(xs, masks, labels):
        full_ok = ok[at:at + len(xx)]
        for a, b in _runs(full_ok):
            res_runs.append(res[at + a:at + b])
        at += len(xx)
    tau = _autocorr_time(res_runs)
    return {"knots": knots.tolist(), "mean": means.round(3).tolist(), "sd": round(max(sd, 0.5), 3),
            "weight": round(1 / tau, 4), "n_frames": int(ok.sum())}


def fit_chime(xs, labels, mode) -> dict:
    cat = np.digitize(np.concatenate(xs), CHIME_EDGES)
    is_mode = np.concatenate([l.mode == mode for l in labels])
    out = {}
    for name, m in (("in", is_mode), ("out", ~is_mode)):
        n = np.bincount(cat[m], minlength=3) + CHIME_PSEUDO
        # significant figures, not decimals: the pseudo-count must not round to 0 (log 0)
        out[name] = [float(f"{x:.4g}") for x in n / n.sum()]
    return out


def regime_accel(v: np.ndarray) -> np.ndarray:
    """Per frame t, the speed change over the 2 * REGIME_HALF + 1 s centred on the step
    t -> t + 1, m/s^2 (nan where it can't be had). One-second differences of the labels are
    too jittery to classify on their own; this window still resolves a coast of a few seconds."""
    h = REGIME_HALF
    a = np.full(len(v), np.nan)
    i = np.arange(h, len(v) - h - 1)
    a[i] = (v[i + h + 1] - v[i - h]) / ((2 * h + 1) * states.DT)
    return a


def _coarse_regimes(v: np.ndarray, confident: np.ndarray) -> np.ndarray:
    """Per frame t, braking (states.BRAKING), coasting (COASTING) or accelerating (ACCEL_PART,
    before regime_labels() splits it) for the step from t to t + 1: regime_accel() against
    +-A_COAST. -1 where the labels can't tell."""
    a = regime_accel(np.where(confident, v, np.nan))
    out = np.full(len(v), -1)
    out[a < -A_COAST] = states.BRAKING
    out[np.abs(a) <= A_COAST] = states.COASTING
    out[a > A_COAST] = states.ACCEL_PART
    return out


def fit_split(labels: list[FrameLabels]) -> dict:
    """Accelerating comes in two kinds: full traction, a narrow curve (~1 m/s^2 up to 12 m/s,
    falling to ~0.36 at line speed), and part traction below it (~0.65 m/s^2 at low speeds,
    ~0.15 below the curve higher up). A two-component Gaussian mixture over the accelerating
    seconds' regime_accel(), whose means, sds and shares vary with speed: EM, each M step
    kernel-smoothed over speed (sd SPLIT_H), started from the 35th and 85th percentiles.
    Returns its parameters on SPLIT_GRID, per component (part, full)."""
    vs, as_ = [], []
    for l in labels:
        c = l.confident & np.isfinite(l.v)
        g, a = _coarse_regimes(l.v, l.confident), regime_accel(l.v)
        m = (g == states.ACCEL_PART) & np.isfinite(a) & c
        vs.append(l.v[m]); as_.append(a[m])
    V, A = np.concatenate(vs), np.concatenate(as_)
    W = np.exp(-0.5 * ((SPLIT_GRID[:, None] - V[None, :]) / SPLIT_H) ** 2)
    mu = np.zeros((2, len(SPLIT_GRID)))
    for k, q in ((0, 35), (1, 85)):
        for j, u in enumerate(SPLIT_GRID):
            near = np.abs(V - u) < 2
            mu[k, j] = np.percentile(A[near], q) if near.sum() > 5 else np.nan
    mu = np.array([np.interp(SPLIT_GRID, SPLIT_GRID[np.isfinite(m)], m[np.isfinite(m)]) for m in mu])
    sd, share = np.full_like(mu, 0.1), np.full_like(mu, 0.5)
    for _ in range(SPLIT_ITER):
        R = _split_resp(V, A, mu, sd, share)
        for k in range(2):
            w = W * R[k][None, :]
            n = w.sum(axis=1)
            mu[k] = (w @ A) / n
            sd[k] = np.sqrt(np.maximum((w @ A**2) / n - mu[k] ** 2, SPLIT_SD_MIN**2))
            share[k] = n / W.sum(axis=1)
    return {"speeds": SPLIT_GRID.tolist(), "mean": mu.round(4).tolist(), "sd": sd.round(4).tolist(),
            "share": share.round(4).tolist()}


def _split_resp(V, A, mu, sd, share) -> np.ndarray:
    """[2, n] P(part, full traction | speed, acceleration)."""
    at = lambda x: np.array([np.interp(V, SPLIT_GRID, row) for row in x])
    m, s, p = at(mu), at(sd), at(share)
    lik = p * np.exp(-0.5 * ((A - m) / s) ** 2) / s
    return lik / np.maximum(lik.sum(axis=0, keepdims=True), 1e-300)


def regime_labels(split: dict):
    """A function (v, confident) -> per frame t, the regime (states.REGIMES) of the step from
    t to t + 1, -1 where the labels can't tell: _coarse_regimes(), with accelerating split
    into part and full traction by the mixture fit_split() returned."""
    mu, sd, share = (np.asarray(split[k]) for k in ("mean", "sd", "share"))

    def label(v: np.ndarray, confident: np.ndarray) -> np.ndarray:
        g = _coarse_regimes(v, confident)
        acc = np.flatnonzero(g == states.ACCEL_PART)
        if len(acc):
            R = _split_resp(v[acc], regime_accel(v)[acc], mu, sd, share)
            g[acc] = np.where(R[1] > 0.5, states.ACCEL_FULL, states.ACCEL_PART)
        return g
    return label


def kernel_weights(x: np.ndarray, grid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """([len(grid), len(x)] kernel weight of each observation at each grid speed, [len(grid)]
    the kernel sd): per speed the narrowest Gaussian (from DYN_H_MIN up, at most DYN_H_MAX)
    that holds DYN_N_EFF seconds of data, the nearest-neighbour bandwidth rule. Where data
    is dense, a table describes its own speed; where it is sparse, it pools as widely as it
    must for its probabilities to be about as well determined everywhere."""
    d = grid[:, None] - x[None, :]
    h = np.full(len(grid), DYN_H_MAX)
    for hh in DYN_H_GRID[::-1]:  # narrowest that still holds enough
        n = np.exp(-0.5 * (d / hh) ** 2).sum(axis=1)
        h = np.where(n >= DYN_N_EFF, hh, h)
    return np.exp(-0.5 * (d / h[:, None]) ** 2), h


def _switch_table(x: np.ndarray, y: np.ndarray, grid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """P(next regime | speed) at every speed of `grid`, from observations (speed x, next
    regime y): kernel-weighted counts (kernel_weights()), plus a Dirichlet prior of
    DYN_PRIOR pseudo-seconds distributed like all the observations at any speed, which is
    what a speed the kernel can't collect enough for falls back on.
    Returns ([len(grid), N_R] probabilities, [len(grid)] kernel sd)."""
    pooled = np.bincount(y, minlength=states.N_R) + 0.5
    pooled = pooled / pooled.sum()
    w, h = kernel_weights(x, grid)
    counts = w @ np.eye(states.N_R)[y] if len(y) else np.zeros((len(grid), states.N_R))
    return (counts + DYN_PRIOR * pooled) / (counts.sum(axis=1, keepdims=True) + DYN_PRIOR), h


def centred_steps(obs: np.ndarray, grid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """P(step | speed) at every speed of `grid` from one regime's observations (speed, step,
    speed change in m/s per second; dynamics_observations()), as ([len(grid), steps],
    [len(grid)] kernel sd).

    Simply counting the steps of nearby speeds would flatten a sloping curve wherever the
    kernel has to be wide (few seconds of the regime there), pulling the table towards the
    regime's values at other speeds. Instead the kernel (kernel_weights()) is used twice:
      centre  at each speed, the local-linear fit of the speed change (weighted least squares
              on a line through the speed, its value there), which follows a sloping curve.
              Not extended past the data: beyond the speeds with fewer than CLAMP_N of the
              regime's seconds outside them, held at its value there (a straight line read
              off where all the data is on one side would just continue the trend);
      shape   each observation's deviation from the centre at its own speed, pooled with the
              same weights and placed around the centre at this speed. Each value is shared
              between the two steps it falls between, in proportion.
    Plus DYN_PRIOR pseudo-seconds of all the regime's deviations, equally weighted, around
    the same centre."""
    M, DV = DYN_MAX_STEP, states.DV
    x, a = obs[:, 0], obs[:, 2]
    w, h = kernel_weights(x, grid)
    d = x[None, :] - grid[:, None]
    s0, s1, s2 = w.sum(1), (w * d).sum(1), (w * d * d).sum(1)
    t0, t1 = w @ a, (w * d) @ a
    det = s0 * s2 - s1**2
    flat = det <= 1e-9 * np.maximum(s0 * s2, 1e-300)
    centre = np.where(flat, t0 / np.maximum(s0, 1e-300), (s2 * t0 - s1 * t1) / np.where(flat, 1.0, det))
    xs = np.sort(x)
    lo, hi = xs[min(CLAMP_N, len(xs) - 1)], xs[max(len(xs) - 1 - CLAMP_N, 0)]
    centre = np.interp(np.clip(grid, lo, hi), grid, centre)
    e = a - np.interp(x, grid, centre)

    def binned(weights: np.ndarray) -> np.ndarray:
        pos = np.clip((centre[:, None] + e[None, :]) / DV + M, 0, 2 * M)
        lo = np.minimum(np.floor(pos).astype(int), 2 * M - 1)
        frac = pos - lo
        out = np.zeros((len(grid), 2 * M + 1))
        rows = np.broadcast_to(np.arange(len(grid))[:, None], pos.shape)
        np.add.at(out, (rows, lo), weights * (1 - frac))
        np.add.at(out, (rows, lo + 1), weights * frac)
        return out

    counts = binned(w)
    prior = binned(np.full(w.shape, 1.0 / len(x))) + 1e-4
    prior /= prior.sum(axis=1, keepdims=True)
    return (counts + DYN_PRIOR * prior) / (counts.sum(axis=1, keepdims=True) + DYN_PRIOR), h


def fit_dynamics(labels: list[FrameLabels]) -> dict:
    """Per regime (states.REGIMES) and per speed (the grid, and 0 for pulling away):

      split     the part / full traction mixture the regimes were labelled with (fit_split())
      p_step    P(speed change over the next second, in steps of DV | regime of that
                second, speed) (centred_steps())
      p_regime  P(regime of the next second | regime of the last one, speed) (_switch_table())
      p_launch  P(regime | pulling away from a stand)
      bandwidth the kernel sd of each speed's tables; n: seconds behind each regime's tables

    From consecutive, confidently labelled frames of which at least one is moving, each
    step labelled by regime_labels()."""
    grid = np.r_[0.0, states.V_MOVING]
    split = fit_split(labels)
    step_obs, switch_obs = dynamics_observations(labels, regime_labels(split))
    p_step, p_regime, h_step, h_regime, n = [], [], [], [], []
    for r in range(states.N_R):
        so, wo = step_obs[r], switch_obs[r]
        ps, hs = centred_steps(so, grid)
        pr, hr = _switch_table(wo[:, 0], wo[:, 1].astype(int), grid)
        p_step.append(ps.round(7).tolist()); p_regime.append(pr.round(7).tolist())
        h_step.append(hs.tolist()); h_regime.append(hr.tolist())
        n.append({"steps": len(so), "switches": int((wo[:, 1] != r).sum()), "of": len(wo)})
    launches = np.array([np.sum((so[:, 0] == 0) & (so[:, 1] > 0)) for so in step_obs]) + 0.5
    return {"max_step": DYN_MAX_STEP, "a_coast": A_COAST, "split": split, "p_step": p_step, "p_regime": p_regime,
            "p_launch": (launches / launches.sum()).round(5).tolist(),
            "bandwidth": {"step": h_step, "regime": h_regime}, "n": n}


def dynamics_observations(labels: list[FrameLabels], label_regimes):
    """What fit_dynamics() counts, per regime, with the regimes `label_regimes` gives
    (regime_labels()):
      steps     [n, 3]: speed, speed change over the next second in steps of DV (clipped to
                +-DYN_MAX_STEP), and the same unrounded, in m/s per second
      switches  [n, 2]: speed, the regime of the next second
    From consecutive, confidently labelled frames of which at least one is moving."""
    M = DYN_MAX_STEP
    step_obs = [[] for _ in range(states.N_R)]
    switch_obs = [[] for _ in range(states.N_R)]
    for l in labels:
        v, c = np.nan_to_num(l.v), l.confident & np.isfinite(l.v)
        g = label_regimes(l.v, l.confident)
        ok = c[:-1] & c[1:] & ((v[:-1] > 0) | (v[1:] > 0)) & (g[:-1] >= 0)
        dv = (v[1:] - v[:-1]) / states.DT
        step = np.clip(np.round(dv * states.DT / states.DV), -M, M)
        for r in range(states.N_R):
            m = ok & (g[:-1] == r)
            step_obs[r].append(np.c_[v[:-1][m], step[m], dv[m]])
        # the regime the train was in at t (that of the step into t) and the one it takes next
        sw = c[1:-1] & (v[1:-1] > 0) & (g[:-2] >= 0) & (g[1:-1] >= 0)
        for r in range(states.N_R):
            m = sw & (g[:-2] == r)
            switch_obs[r].append(np.c_[v[1:-1][m], g[1:-1][m]])
    return [np.concatenate(x) for x in step_obs], [np.concatenate(x) for x in switch_obs]


def fit_dwell(labels: list[FrameLabels]) -> dict:
    """Mean seconds in each stopped mode (complete visits only)."""
    out = {}
    for mode in DWELL_PRIOR_S:
        durs = []
        for l in labels:
            for a, b in _runs(l.mode == mode):
                complete = a > 0 and b < len(l.mode) and l.mode[a - 1] != UNKNOWN and l.mode[b] != UNKNOWN
                if complete:
                    durs.append(b - a)
        w, m0 = DWELL_PRIOR_WEIGHT, DWELL_PRIOR_S[mode]
        out[str(mode)] = round((sum(durs) + w * m0) / (len(durs) + w), 2)
    return out


def fit_accel(frames, labels, p_rests) -> dict:
    """sd of a_long around sign * dv/dt + offset, sign and offset fitted per stretch.

    Only against speed labels that don't themselves come from the accelerometer (so, for
    now, tone-traced legs with a resting phone); otherwise the prior stands."""
    res = []
    for f, l, p in zip(frames, labels, p_rests):
        mo = motion.extract(f, p)
        dv = np.r_[np.diff(l.v), np.nan] / states.DT
        ok_all = (np.isfinite(mo["a_long"]) & np.isfinite(dv) & l.confident & np.r_[l.confident[1:], False]
                  & (l.source != "accel") & np.append(l.source[1:] != "accel", False))
        for s in range(mo["stretch"].max() + 1):
            ok = ok_all & (mo["stretch"] == s)
            if ok.sum() < 60:
                continue
            A = np.c_[dv[ok], np.ones(ok.sum())]
            coef, *_ = np.linalg.lstsq(A, mo["a_long"][ok], rcond=None)
            res.append(mo["a_long"][ok] - np.sign(coef[0]) * dv[ok] - coef[1])
    sd0, w = ACCEL_PRIOR
    r = np.concatenate(res) if res else np.zeros(0)
    # robust: label speeds have their own second-to-second noise
    var = (len(r) * (1.4826 * np.median(np.abs(r))) ** 2 + w * sd0**2) / (len(r) + w) if len(r) else sd0**2
    return {"sd": round(float(np.sqrt(var)), 4), "n_frames": int(len(r))}


def fit(frames: list[dict], labels: list[FrameLabels]) -> dict:
    p = {"phone": phone_state.fit(frames, labels)}
    phones = [phone_state.apply(p["phone"], f) for f in frames]
    p_rests = [ph[:, phone_state.RESTING] for ph in phones]
    # learn each phone-dependent block from frames that are labelled, and classified, as that state
    rest, held = ([(ph.argmax(axis=1) == k) & (l.phone == k) for ph, l in zip(phones, labels)]
                  for k in (phone_state.RESTING, phone_state.HELD))
    vs = [np.nan_to_num(l.v) for l in labels]
    known = [l.confident & np.isfinite(l.v) for l in labels]

    p["tone"] = comb.fit([f["tone_spec"] for f in frames], frames[0]["tone_f"], vs, known,
                         ratio_range=(12.0, 66.0), ratio_step=0.05)
    # wheel-rotation lines: one set of tables per phone state they can be seen in (a hand
    # damps and adds to what the phone feels, so the same line is fainter there)
    p["wheel"] = {name: comb.fit([f["accel_spec"] for f in frames], frames[0]["accel_f"], vs,
                                 [k & m for k, m in zip(known, masks)], ratio_range=(0.2, 1.38), ratio_step=0.0025)
                  for name, masks in (("resting", rest), ("held", held))}
    p["chime"] = {"open": fit_chime([f["open_chime"] for f in frames], labels, OPEN_CHIME),
                  "close": fit_chime([f["close_chime"] for f in frames], labels, CLOSE_CHIME)}
    p["level"] = {"rumble": fit_level([f["rumble_db"] for f in frames], labels, [np.ones(len(l.v), bool) for l in labels])}
    for i, band in enumerate(("vib_low", "vib_high")):
        for name, masks in (("resting", rest), ("held", held)):
            p["level"][f"{band}/{name}"] = fit_level([f["vib_db"][:, i] for f in frames], labels, masks)
    p["accel"] = fit_accel(frames, labels, p_rests)
    p["dynamics"] = fit_dynamics(labels)
    p["dwell"] = fit_dwell(labels)
    return p
