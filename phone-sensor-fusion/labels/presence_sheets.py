"""Sheets for labelling where the phone's owner was (presence.csv): the stretches around
boarding and alighting, with what tells a platform from a carriage.

Per stretch:
  - audio 2.8-6 kHz: the car's hum (lines at 3720-4200 Hz, multiples of 120 Hz: inside a car,
    or right next to one with its doors open), the door chimes (4400 Hz open, 3520 / 5280 Hz
    close), the bells of a train coming or going (pulsed harmonic stacks);
  - audio 0-1.6 kHz: a train's motor tone sweeping as it pulls in or away, platform tones
    (steady lines), the broadband roar of a pass-by;
  - the hum as the model sees it (features/frames.py cabin_db), and the chime detectors;
  - the accelerometer at walking pace (1.2-2.5 Hz: steps) and the gyro (phone being carried).
presence.csv, where it exists, is drawn on top (white, dashed).

    python -m labels.presence_sheets [seconds, default 150]     # first and last stretch of every labelled recording
    python -m labels.presence_sheets <recording-id> <t0> <t1>   # any stretch (a long wait before boarding)

Writes data/phone-sensor-fusion/labels/<id>.presence_{start,end}.png. Needs the recording's
features (python -m pipeline computes them).
"""

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal

from common import paths
from common.recording import Recording
from features import frames
from labels import load


def sheet(rec, t0, t1, out):
    x = frames.aligned_audio(rec)
    fs = frames.FS
    i0, i1 = max(0, int(t0 * fs)), int(t1 * fs)
    f, tt, S = signal.spectrogram(np.asarray(x[i0:i1]), fs=fs, nperseg=2048, noverlap=2048 - 1600)
    tt = tt + i0 / fs
    db = 10 * np.log10(S + 1e-16)
    fig, ax = plt.subplots(6, 1, figsize=(24, 17), sharex=True, gridspec_kw={"height_ratios": [3, 3, 1, 1, 1, 1]})
    for a, (lo, hi), name in ((ax[0], (2800, 6000), "2.8-6 kHz: car hum 3.7-4.2 kHz,\nchimes, bells"),
                              (ax[1], (0, 1600), "0-1.6 kHz: motor tone,\nplatform tones")):
        m = (f >= lo) & (f <= hi)
        a.pcolormesh(tt, f[m], db[m], shading="auto", cmap="magma",
                     vmin=np.percentile(db[m], 30), vmax=np.percentile(db[m], 99.7))
        a.set_ylabel(name)
    F = frames.load(rec.id)
    t = np.arange(len(F["rumble_db"])) + 0.5
    m = (t >= t0) & (t <= t1)
    ax[2].plot(t[m], F["cabin_db"][m], "g"); ax[2].set_ylabel("car hum (dB)"); ax[2].set_ylim(-2, 30)
    ax[3].plot(t[m], F["open_chime"][m], label="open"); ax[3].plot(t[m], F["close_chime"][m], label="close")
    ax[3].set_ylabel("chime detectors"); ax[3].legend(loc="upper right")
    ta, acc = rec.accel
    ma = (ta >= t0) & (ta <= t1)
    steps = signal.sosfiltfilt(signal.butter(2, [1.2, 2.5], btype="band", fs=100, output="sos"), np.linalg.norm(acc, axis=1))
    ax[4].plot(ta[ma], steps[ma], lw=0.5); ax[4].set_ylabel("|accel| 1.2–2.5 Hz (g)\nsteps"); ax[4].set_ylim(-0.4, 0.4)
    tg, g = rec.gyro
    mg = (tg >= t0) & (tg <= t1)
    ax[5].semilogy(tg[mg], np.linalg.norm(g[mg], axis=1) + 1e-4, lw=0.4); ax[5].set_ylabel("|gyro| (rad/s)")
    for st in load.stops(rec.id):
        for x_ in ax:
            for a_, b_, c in ((st.t_open, st.t_open_end, "c"), (st.t_close, st.t_close_end, "lime")):
                if np.isfinite(a_) and t0 <= a_ <= t1:
                    x_.axvspan(a_, b_, color=c, alpha=0.18)
    lab = paths.LABELS / rec.id
    if (lab / "presence.csv").exists():
        for a_, b_, where in load.presence(rec.id):
            if t0 <= a_ <= t1:
                ax[0].axvline(a_, color="w", lw=1, ls="--")
                ax[0].text(a_, 5850, f" {where}", color="w", va="top")
    ax[5].set_xticks(np.arange(np.ceil(t0 / 5) * 5, t1, 5))
    for x_ in ax:
        x_.grid(alpha=0.3)
    ax[0].set_title(f"{rec.id}  {t0:.0f}–{t1:.0f} s   (cyan: door-open chime, green: door-close chime;"
                    f" white dashed: presence.csv)")
    fig.tight_layout(); fig.savefig(out, dpi=55); plt.close(fig)
    print(out)


def main(args):
    if len(args) == 3:  # one stretch of one recording
        rid, t0, t1 = args[0], float(args[1]), float(args[2])
        sheet(Recording(rid), t0, t1, paths.derived("labels", f"{rid}.presence_{t0:.0f}_{t1:.0f}.png"))
        return
    span = float(args[0]) if args else 150.0
    for rid in paths.labelled_ids():
        rec = Recording(rid)
        sheet(rec, 0, min(span, rec.duration), paths.derived("labels", f"{rec.id}.presence_start.png"))
        sheet(rec, max(0, rec.duration - span), rec.duration, paths.derived("labels", f"{rec.id}.presence_end.png"))


if __name__ == "__main__":
    main(sys.argv[1:])
