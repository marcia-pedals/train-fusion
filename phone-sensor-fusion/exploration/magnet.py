"""What does the magnetometer see on an electric train?

 1. Its spectrum: which AC lines are there (the 25 kV supply is 60 Hz; sampled at 100 Hz
    that folds to 40 Hz).
 2. Traction: do the field's size, or its AC parts, follow the train's acceleration or
    power (motoring / regenerative braking)?
 3. Speed: are there spectral lines proportional to speed (magnetised wheels turning)?
 4. Place: does the field repeat at the same place on another ride (a fingerprint of the
    track: bridges, substations, feeders)?

Phone resting, on the train, labelled position and speed.

    python -m exploration.magnet
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import ndimage, signal

from common import paths
from common.recording import Recording
from exploration.newdata import NEW, per_second, truth
from common.dsp import line_prominence, spectrogram_db
from features import comb, frames
from features.frames import uniform_imu

BANDS = {"0.5-3 Hz": (0.5, 3), "3-15 Hz": (3, 15), "15-35 Hz": (15, 35), "60 Hz line (38-42)": (38, 42), "42-49 Hz": (42, 49)}


def band_level(x, fs, band, T):
    """Per-second dB of the summed power of the three axes in a band."""
    sos = signal.butter(4, band, btype="band", fs=fs, output="sos")
    y = (signal.sosfiltfilt(sos, x, axis=0) ** 2).sum(axis=1)
    n = T * fs
    y = np.r_[y, np.zeros(max(0, n - len(y)))][:n]
    return 10 * np.log10(y.reshape(T, fs).mean(axis=1) + 1e-9)


def corr(a, b, m):
    m = m & np.isfinite(a) & np.isfinite(b)
    return np.corrcoef(a[m], b[m])[0, 1] if m.sum() > 50 else np.nan


def main():
    prof = {}
    fig, axes = plt.subplots(4, 1, figsize=(24, 15), sharex=True)
    for rid in NEW:
        rec = Recording(rid)
        lab, pos, direction = truth(rid)
        T = len(lab.v)
        tm, m = rec.mag
        mu = uniform_imu(tm, m)  # 100 Hz from t = 0
        dm = rec.device_motion
        Bc = per_second(dm["t"], dm["mag"], T)
        grav = per_second(dm["t"], dm["gravity"], T)
        up = -grav / np.linalg.norm(grav, axis=1, keepdims=True)
        size = np.linalg.norm(Bc, axis=1)
        vert = np.sum(Bc * up, axis=1)
        horiz = np.sqrt(np.maximum(size**2 - vert**2, 0))
        ok = (lab.phone == 0) & (lab.on_train == 1) & lab.confident & np.isfinite(pos)
        a = np.r_[np.diff(lab.v), np.nan]
        a = np.convolve(np.nan_to_num(a), np.ones(3) / 3, mode="same")
        power = a * lab.v  # per unit mass, W/kg: + motoring, - braking
        print(f"## {rid}: {ok.sum()} s resting on the train with labels")
        f, P = signal.welch(mu[int(lab.on_train.argmax()) * 100:] - mu.mean(axis=0), fs=100, nperseg=2048, axis=0)
        P = P.sum(axis=1)
        pk, _ = signal.find_peaks(10 * np.log10(P), prominence=6)
        print("   spectral lines (Hz, dB over the neighbourhood):",
              [(round(f[i], 1), round(10 * np.log10(P[i] / np.median(P[max(0, i - 40):i + 40])), 0)) for i in pk if f[i] > 1][:10])
        feats = {"|B|": size, "B vertical": vert, "B horizontal": horiz}
        feats.update({f"AC {k}": band_level(mu, 100, b, T) for k, b in BANDS.items()})
        moving = ok & (lab.v > 3)
        print(f"   field on the train: |B| median {np.nanmedian(size[ok]):.0f} µT, 5-95 % {np.nanpercentile(size[ok], 5):.0f}..{np.nanpercentile(size[ok], 95):.0f}"
              f" (Earth's field here: 48 µT)")
        print("   correlation with:            accel   |accel|   power   |power|   speed")
        for name, x in feats.items():
            print(f"     {name:22s}  " + "  ".join(f"{corr(x, y, moving):+7.2f}" for y in (a, np.abs(a), power, np.abs(power), lab.v)))
        # motoring vs coasting vs braking, same speeds
        mid = moving & (lab.v > 10) & (lab.v < 30)
        for name in ("AC 60 Hz line (38-42)", "AC 3-15 Hz", "|B|"):
            x = feats[name]
            print(f"     {name:22s} at 10-30 m/s: motoring (a>0.3) {np.nanmedian(x[mid & (a > 0.3)]):6.1f}   coasting (|a|<0.1) "
                  f"{np.nanmedian(x[mid & (np.abs(a) < 0.1)]):6.1f}   braking (a<-0.3) {np.nanmedian(x[mid & (a < -0.3)]):6.1f}"
                  f"   (sd within a group ~{np.nanstd(x[mid & (a > 0.3)]):.1f})")
        # lines proportional to speed, as for the audio and the accelerometer (features/comb.py)
        best = (0, 0, "")
        for axis_name, x in (("x", mu[:, 0]), ("y", mu[:, 1]), ("z", mu[:, 2])):
            pad = frames.ACC_NWIN // 2 - 50
            fq, db = spectrogram_db(np.r_[np.full(pad, x[0]), x, np.full(frames.ACC_NWIN, x[-1])] - x.mean(), 100,
                                    frames.ACC_NWIN, 100, frames.ACC_NFFT)
            spec = ndimage.maximum_filter1d(line_prominence(db, fq, frames.ACC_BG_HZ), 5, axis=0).T[:T]
            ratios = np.arange(0.1, 1.38, 0.0025)
            fast = ok & (lab.v >= 8)
            rs = comb.ratio_spectrum(spec[fast].astype(np.float32), fq, lab.v[fast], ratios)
            i = int(np.argmax(rs))
            if rs[i] - np.median(rs) > best[0]:
                best = (rs[i] - np.median(rs), ratios[i], axis_name)
        print(f"   lines proportional to speed: strongest at {best[1]:.3f} Hz per m/s on the {best[2]} axis, {best[0]:.1f} dB above the "
              f"median ratio (the wheel turns at 0.35; the accelerometer's line there stands 3-4 dB up)")
        prof[rid] = (pos, ok, feats, lab.v)
        if rid == NEW[1]:
            t = np.arange(T) + 0.5
            axes[0].plot(t, np.where(ok, lab.v, np.nan), "k"); axes[0].set_ylabel("speed (m/s)")
            axes[1].plot(t, np.where(ok, power, np.nan), "k"); axes[1].set_ylabel("a × v (W/kg)\n+ motoring, − braking")
            axes[2].plot(t, np.where(ok, feats["AC 60 Hz line (38-42)"], np.nan), "C0", lw=0.8, label="60 Hz line")
            axes[2].plot(t, np.where(ok, feats["AC 3-15 Hz"], np.nan), "C1", lw=0.8, label="3–15 Hz")
            axes[2].set_ylabel("magnetometer AC level (dB)"); axes[2].legend()
            axes[3].plot(t, np.where(ok, size, np.nan), "C2", lw=0.8); axes[3].set_ylabel("|B| calibrated (µT)")
            axes[3].set_xlim(t[ok][0], t[ok][-1])
            for x_ in axes:
                x_.grid(alpha=0.3)
    fig.suptitle(NEW[1]); fig.tight_layout()
    out = paths.derived("exploration", "magnet.png"); fig.savefig(out, dpi=60); print(out)

    # the same place on the two rides (opposite directions, so opposite tracks)
    grid = np.arange(31000, 62000, 50.0)
    print("## same place, other ride (50 m cells, moving, after removing a 2 km running mean):")
    for name in ("|B|", "B vertical", "B horizontal", "AC 60 Hz line (38-42)", "AC 3-15 Hz"):
        cols = []
        for rid in NEW:
            pos, ok, feats, v = prof[rid]
            m = ok & (v > 3) & np.isfinite(feats[name])
            idx = np.digitize(pos[m], grid)
            cell = np.array([np.median(feats[name][m][idx == i]) if np.any(idx == i) else np.nan for i in range(1, len(grid))])
            k = np.ones(40) / 40
            filled = np.where(np.isfinite(cell), cell, np.nanmedian(cell))
            cols.append(np.where(np.isfinite(cell), cell - np.convolve(filled, k, mode="same"), np.nan))
        m = np.isfinite(cols[0]) & np.isfinite(cols[1])
        r0 = np.corrcoef(cols[0][m], cols[1][m])[0, 1]
        shifted = [np.corrcoef(cols[0][m][:-k], cols[1][m][k:])[0, 1] for k in (20, 40, 80)]
        print(f"   {name:22s} r = {r0:+.2f} over {m.sum()} cells   (same series shifted by 1, 2, 4 km: {', '.join(f'{x:+.2f}' for x in shifted)})")


if __name__ == "__main__":
    main()
