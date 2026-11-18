"""Learned feature: speed from spectral lines whose frequency is proportional to speed.

Used twice: on the audio line spectrum (traction-motor tone, ~26 Hz per m/s, and its
partials) and on the accelerometer line spectrum (wheel rotation, ~0.35 Hz per m/s, and
its harmonics).

What is learned from the speed labels:

  ratios   which frequency/speed ratios carry a line: the peaks of the "ratio spectrum"
           (the mean line prominence at f = ratio x speed, exploration/tone_vs_speed.py)
  llr      per ratio and per speed band, a table over line prominence z of
           log p(z | a partial is here) - log p(z | no partial here).
           "Here" = the bin at ratio x labelled speed; "not here" = the same frames at
           detuned frequencies. Where a partial can't be seen (the motor tone below
           ~10 m/s) the two distributions coincide and the table is ~0 by itself.

Applied: for every candidate speed v, sum the tables at the bins ratio_i x v. Under the
assumption that bins are independent given speed, that sum is log p(spectrum | v) minus
log p(spectrum | no lines at all), which is exactly the term an HMM needs; standing still
gets 0.
"""

import numpy as np
from scipy import ndimage, signal

Z_EDGES = np.arange(0.0, 24.1, 2.0)  # dB bins of line prominence (plus one below, one above)
V_EDGES = np.array([5.0, 10.0, 15.0, 20.0, 25.0, 30.0])  # m/s bands (plus below / above)
DETUNE = np.array([0.62, 0.71, 0.8, 0.88, 1.13, 1.21, 1.33, 1.46])
MIN_V = 1.0  # labelled speeds below this aren't used
PEAK_MIN_V = 8.0  # the ratio spectrum is averaged over frames faster than this
PEAK_PROMINENCE_DB = 1.0
MAX_PARTIALS = 4
SMOOTH = 2.0  # pseudo-count per table cell
LLR_CLIP = (-2.0, 5.0)
MIN_FRAMES = 200  # fewer labelled frames than this: no feature


def _sample(spec, f, freq):
    """spec[t, bin nearest freq[t]]; nan where freq is outside the spectrum."""
    df = f[1] - f[0]
    idx = np.round(freq / df).astype(int)
    ok = (idx >= 1) & (idx < len(f))
    out = np.full(len(freq), np.nan)
    out[ok] = spec[np.flatnonzero(ok), idx[ok]]
    return out


def ratio_spectrum(spec, f, v, ratios):
    """Mean line prominence at f = ratio * v over the frames given."""
    return np.array([np.nanmean(_sample(spec, f, r * v)) for r in ratios])


def _centroid(ratios, rs, i, rel_width=0.025):
    """Centre of mass of the peak at index i: the labels' speed scale differs a little from
    leg to leg (where exactly the train stopped), which smears the peak; its centre is the
    best single ratio."""
    m = np.abs(ratios / ratios[i] - 1) <= rel_width
    w = rs[m] - rs[m].min()
    return float(np.sum(w * ratios[m]) / np.sum(w))


def _isotonic(y, w):
    """Weighted least-squares non-decreasing fit (pool adjacent violators)."""
    blocks = [[float(v), float(wt), 1] for v, wt in zip(y, w)]  # mean, weight, length
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0]:
            a, b = blocks[i], blocks.pop(i + 1)
            a[0], a[1], a[2] = (a[0] * a[1] + b[0] * b[1]) / (a[1] + b[1]), a[1] + b[1], a[2] + b[2]
            i = max(i - 1, 0)
        else:
            i += 1
    return np.concatenate([np.full(n, m) for m, _, n in blocks])


def fit(specs, f, vs, masks, ratio_range, ratio_step) -> dict | None:
    """specs: [T, F] per recording; vs: labelled speed; masks: frames to learn from."""
    spec = np.concatenate([s[m] for s, m in zip(specs, masks)]).astype(np.float32)
    v = np.concatenate([x[m] for x, m in zip(vs, masks)])
    keep = v >= MIN_V
    spec, v = spec[keep], v[keep]
    if len(v) < MIN_FRAMES:
        return None
    ratios = np.arange(ratio_range[0], ratio_range[1], ratio_step)
    fast = v >= PEAK_MIN_V
    rs = ratio_spectrum(spec[fast], f, v[fast], ratios)
    peaks, props = signal.find_peaks(rs, prominence=PEAK_PROMINENCE_DB)
    order = np.argsort(-props["prominences"])[:MAX_PARTIALS]
    chosen = sorted(_centroid(ratios, rs, peaks[i]) for i in order)
    vb = np.digitize(v, V_EDGES)
    nz, nv = len(Z_EDGES) + 1, len(V_EDGES) + 1
    tables = []
    for r in chosen:
        on = np.zeros((nv, nz))
        off = np.zeros((nv, nz))
        z = _sample(spec, f, r * v)
        ok = np.isfinite(z)
        np.add.at(on, (vb[ok], np.digitize(z[ok], Z_EDGES)), 1)
        for d in DETUNE:
            z = _sample(spec, f, r * v * d)
            ok = np.isfinite(z)
            np.add.at(off, (vb[ok], np.digitize(z[ok], Z_EDGES)), 1)
        p_on = (on + SMOOTH) / (on + SMOOTH).sum(axis=1, keepdims=True)
        p_off = (off + SMOOTH) / (off + SMOOTH).sum(axis=1, keepdims=True)
        # more prominence can't be less evidence: per speed band, the non-decreasing
        # table closest to the raw log-ratios (weighted by how many frames each cell has),
        # then shifted so that a spectrum without the partial averages a ratio of 1
        llr = np.stack([_isotonic(np.log(p_on[b] / p_off[b]), on[b] + off[b] / len(DETUNE) + SMOOTH)
                        for b in range(nv)])
        llr -= np.log(np.sum(p_off * np.exp(llr), axis=1, keepdims=True))
        # a speed band with hardly any data says nothing
        llr[on.sum(axis=1) < 30] = 0.0
        tables.append(np.clip(llr, *LLR_CLIP).round(3).tolist())
    return {"ratios": chosen, "llr": tables, "n_frames": int(len(v)),
            "ratio_spectrum_peaks": [(float(ratios[p]), float(rs[p])) for p in peaks]}


def apply(p: dict | None, spec, f, v_grid, pooled_bins: int = 0) -> np.ndarray:
    """log p(spectrum | v) - log p(spectrum | no lines), [T, len(v_grid)].

    `pooled_bins`: the spectrum is already max-pooled over +- this many bins. A grid speed
    stands for every speed within half a grid step, so each partial is looked up as the
    maximum over the bins that range of speeds covers, if that is wider."""
    out = np.zeros((len(spec), len(v_grid)))
    if p is None:
        return out
    df = f[1] - f[0]
    vb = np.digitize(v_grid, V_EDGES)
    spec = np.asarray(spec, dtype=np.float32)
    for r, table in zip(p["ratios"], p["llr"]):
        table = np.asarray(table)
        idx = np.round(r * v_grid / df).astype(int)
        ok = (idx >= 1) & (idx < len(f))
        extra = int(np.ceil(r * (v_grid[1] - v_grid[0]) / (2 * df))) - pooled_bins
        z = ndimage.maximum_filter1d(spec, 2 * extra + 1, axis=1) if extra > 0 else spec
        zb = np.digitize(z[:, idx[ok]], Z_EDGES)
        out[:, ok] += table[vb[ok][None, :], zb]
    return out
