"""Follow-ups to exploration/magnet.py: the things that first look left untested.

 A. Bandwidth: is the magnetometer's noise floor flat up to 50 Hz, or filtered?
 B. The 60 Hz line: its exact frequency, and whether it is there off the train too.
 C. Lines proportional to speed at ANY ratio, including ones above the 50 Hz limit that
    fold back (a motor's electrical frequency would be several Hz per m/s).
 D. Traction, tested on events rather than on a power estimate: what the field does in
    the seconds around pulling away (traction current steps on) and around the wheels
    stopping (braking current steps off), stacked over all stops.

    python -m exploration.magnet_gaps
"""

import numpy as np
from scipy import ndimage, signal

from common.dsp import line_prominence, spectrogram_db
from common.recording import Recording
from exploration.newdata import NEW, truth
from features import comb, frames
from features.frames import uniform_imu
from labels import load


def psd_at(x, fs, bands):
    f, P = signal.welch(x - x.mean(axis=0), fs=fs, nperseg=1024, axis=0)
    P = P.sum(axis=1)
    return [float(np.median(P[(f >= a) & (f < b)])) for a, b in bands], f, P


def fold(f, fs=100.0):
    return np.abs((f + fs / 2) % fs - fs / 2)


def main():
    bands = [(1, 3), (5, 10), (10, 20), (20, 30), (30, 38), (42, 46), (46, 49.5)]
    for rid in NEW:
        rec = Recording(rid)
        lab, pos, direction = truth(rid)
        T = len(lab.v)
        tm, m = rec.mag
        fs_true = (len(tm) - 1) / (tm[-1] - tm[0])
        mu = uniform_imu(tm, m)[:T * 100]
        mu = np.r_[mu, np.repeat(mu[-1:], T * 100 - len(mu), axis=0)]
        print(f"## {rid}   magnetometer delivers {fs_true:.2f} samples/s")
        sec = lambda mask: np.repeat(mask, 100)

        # A. noise floor shape, phone still: standing at platforms with the phone resting
        still = (lab.phone == 0) & (lab.on_train == 1) & (lab.v == 0)
        moving = (lab.phone == 0) & (lab.on_train == 1) & (lab.v > 20)
        for name, mask in (("standing", still), ("above 20 m/s", moving)):
            lev, f, P = psd_at(mu[sec(mask)], 100, bands)
            print(f"   A. floor, {name:13s} (µT²/Hz ×1e4): " + "  ".join(f"{a}-{b} Hz: {x * 1e4:6.2f}" for (a, b), x in zip(bands, lev)))

        # B. the 60 Hz line
        for name, mask in (("on the train, resting", (lab.phone == 0) & (lab.on_train == 1)),
                           ("off the train", lab.on_train == 0)):
            x = mu[sec(mask)]
            if len(x) < 3000:
                continue
            f, P = signal.welch(x - x.mean(axis=0), fs=100, nperseg=8192, axis=0)
            P = P.sum(axis=1)
            band = (f > 36) & (f < 44)
            i = np.flatnonzero(band)[np.argmax(P[band])]
            side = np.median(P[band])
            src = fs_true - f[i] * fs_true / 100  # undo the resampling's relabelling, then unfold
            print(f"   B. {name:22s}: strongest line 36-44 Hz at {f[i]:.2f} Hz, {10 * np.log10(P[i] / side):4.1f} dB over its neighbours, "
                  f"amplitude ~{np.sqrt(P[i] * (f[1] - f[0]) * 2) * 1000:.1f} nT  (a {src:.1f} Hz source folds here)")

        # C. speed-proportional lines at any ratio, folded
        ok = (lab.phone == 0) & (lab.on_train == 1) & lab.confident & np.isfinite(pos)
        ratios = np.arange(0.1, 40, 0.02)
        best = []
        for ax_i, ax_name in enumerate("xyz"):
            x = mu[:, ax_i]
            pad = frames.ACC_NWIN // 2 - 50
            fq, db = spectrogram_db(np.r_[np.full(pad, x[0]), x, np.full(frames.ACC_NWIN, x[-1])] - x.mean(), 100,
                                    frames.ACC_NWIN, 100, frames.ACC_NFFT)
            spec = ndimage.maximum_filter1d(line_prominence(db, fq, frames.ACC_BG_HZ), 5, axis=0).T[:T].astype(np.float32)
            for name, mask in (("all speeds > 3 m/s", ok & (lab.v > 3)), ("2-10 m/s only", ok & (lab.v > 2) & (lab.v < 10))):
                v = lab.v[mask]
                rs = np.array([np.nanmean(comb._sample(spec[mask], fq, fold(r * v))) for r in ratios])
                # a ratio whose folded frequency barely moves over these speeds is just a fixed line: ignore
                moves = np.array([np.ptp(fold(r * v)) for r in ratios]) > 5
                rs_m = np.where(moves, rs, np.nan)
                i = int(np.nanargmax(rs_m))
                best.append((rs_m[i] - np.nanmedian(rs_m), ratios[i], ax_name, name, np.nanstd(rs_m)))
        for name in ("all speeds > 3 m/s", "2-10 m/s only"):
            b = max(x for x in best if x[3] == name)
            print(f"   C. {name:18s}: best ratio {b[1]:5.2f} Hz per m/s ({b[2]} axis), {b[0]:.2f} dB above the median over ratios "
                  f"(sd across ratios {b[4]:.2f} dB; the accelerometer's wheel line: 3-4 dB)")

        # D. event stacks, within resting stretches, raw axes (the phone does not move in the car)
        stops = load.stops(rid)
        lp = signal.sosfiltfilt(signal.butter(2, 1.0, fs=100, output="sos"), mu, axis=0)
        sos60 = signal.butter(4, [38, 42], btype="band", fs=100, output="sos")
        ac = np.sqrt(signal.sosfiltfilt(signal.butter(2, 0.5, fs=100, output="sos"),
                                        (signal.sosfiltfilt(sos60, mu, axis=0) ** 2).sum(axis=1)).clip(0)) * 1000  # nT
        at = lambda arr, t: arr[int(round(t * 100))]
        for name, key, offsets in (("pulling away (t = wheel start)", "t_depart", (1, 3, 6, 10)),
                                   ("stopping (t = wheel stop)", "t_arrive", (-10, -6, -3, -1))):
            dB, dAC, dist = [], [], []
            for st in stops:
                t0 = getattr(st, key)
                if not np.isfinite(t0) or not (lab.phone[int(t0) - 12:int(t0) + 12] == 0).all():
                    continue
                # reference: standing, 2-6 s on the stationary side of the event
                ref_t = np.arange(-6, -2, 0.1) if key == "t_depart" else np.arange(2, 6, 0.1)
                ref = np.mean([at(lp, t0 + r) for r in ref_t], axis=0)
                ref_ac = np.mean([at(ac, t0 + r) for r in ref_t])
                dB.append([np.linalg.norm(at(lp, t0 + o) - ref) for o in offsets])
                dAC.append([at(ac, t0 + o) - ref_ac for o in offsets])
                i = int(t0)
                v = lab.v[min(i, i + offsets[-1] if offsets[-1] > 0 else i + offsets[0]):max(i, i + offsets[-1] if offsets[-1] > 0 else i + offsets[0])]
                # metres from the standing point at each offset
                dist.append([abs(np.nansum(lab.v[min(i, i + o):max(i, i + o)])) for o in offsets])
            dB, dAC, dist = np.array(dB), np.array(dAC), np.array(dist)
            # noise reference: the same differences taken entirely while standing
            noise = []
            for st in stops:
                if np.isfinite(st.t_arrive) and np.isfinite(st.t_depart) and st.t_depart - st.t_arrive > 20 and (lab.phone[int(st.t_arrive):int(st.t_depart)] == 0).all():
                    c = (st.t_arrive + st.t_depart) / 2
                    ref = np.mean([at(lp, c + r) for r in np.arange(-6, -2, 0.1)], axis=0)
                    noise.append(np.linalg.norm(at(lp, c + 3) - ref))
            print(f"   D. {name}, {len(dB)} events; change of the field vector from standing (µT), median [and metres moved]:")
            print("        " + "   ".join(f"{o:+d} s: {np.median(dB[:, k]):5.2f} [{np.median(dist[:, k]):4.0f} m]" for k, o in enumerate(offsets))
                  + f"      (same measure with the train standing throughout: {np.median(noise):.2f})")
            print("        60 Hz-line amplitude change (nT), median ± sd: "
                  + "   ".join(f"{o:+d} s: {np.median(dAC[:, k]):+5.1f} ± {dAC[:, k].std():4.1f}" for k, o in enumerate(offsets)))


if __name__ == "__main__":
    main()
