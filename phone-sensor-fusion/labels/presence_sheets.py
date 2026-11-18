"""Sheets for labelling when the phone's owner was on the train (presence.csv): the first
and last stretch of a recording, with what tells a platform from a carriage.

Per stretch: the audio spectrogram (ambience changes when stepping through the doors; the
door chimes are marked), the accelerometer level at walking pace (1.2-2.5 Hz: steps), and
the gyro level (phone being carried).

    python -m labels.presence_sheets [seconds, default 150]     # first and last stretch of every recording
    python -m labels.presence_sheets <recording-id> <t0> <t1>   # any stretch (a long wait before boarding)

Writes data/phone-sensor-fusion/labels/<id>.presence_{start,end}.png.
"""

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal

from common import paths
from common.recording import Recording, load_all
from labels import load


def sheet(rec, t0, t1, out):
    ta0, x = rec.audio()
    fs = 16000
    i0, i1 = max(0, int((t0 - ta0) * fs)), int((t1 - ta0) * fs)
    f, tt, S = signal.spectrogram(np.asarray(x[i0:i1]), fs=fs, nperseg=1024, noverlap=1024 - 400)
    db = 10 * np.log10(S + 1e-16)
    fig, ax = plt.subplots(4, 1, figsize=(24, 13), sharex=True, gridspec_kw={"height_ratios": [4, 1.2, 1.2, 1.2]})
    ax[0].pcolormesh(tt + ta0 + i0 / fs, f, db, shading="auto", cmap="magma",
                     vmin=np.percentile(db, 20), vmax=np.percentile(db, 99.7))
    ax[0].set_ylabel("audio (Hz), absolute level")
    level = 10 * np.log10(S.sum(axis=0) + 1e-16)
    ax[1].plot(tt + ta0 + i0 / fs, level, lw=0.6); ax[1].set_ylabel("audio level (dB)")
    t, a = rec.accel
    m = (t >= t0) & (t <= t1)
    mag = np.linalg.norm(a, axis=1)
    steps = signal.sosfiltfilt(signal.butter(2, [1.2, 2.5], btype="band", fs=100, output="sos"), mag)
    ax[2].plot(t[m], steps[m], lw=0.5); ax[2].set_ylabel("|accel| 1.2–2.5 Hz (g)\nsteps"); ax[2].set_ylim(-0.4, 0.4)
    tg, g = rec.gyro
    mg = (tg >= t0) & (tg <= t1)
    ax[3].semilogy(tg[mg], np.linalg.norm(g[mg], axis=1) + 1e-4, lw=0.4); ax[3].set_ylabel("|gyro| (rad/s)")
    for st in load.stops(rec.id):
        for x_ in ax:
            for a_, b_, c in ((st.t_open, st.t_open_end, "c"), (st.t_close, st.t_close_end, "lime")):
                if np.isfinite(a_) and t0 <= a_ <= t1:
                    x_.axvspan(a_, b_, color=c, alpha=0.18)
    ax[3].set_xticks(np.arange(np.ceil(t0 / 5) * 5, t1, 5))
    for x_ in ax:
        x_.grid(alpha=0.3)
    ax[0].set_title(f"{rec.id}  {t0:.0f}–{t1:.0f} s   (cyan: door-open chime, green: door-close chime)")
    fig.tight_layout(); fig.savefig(out, dpi=60); plt.close(fig)
    print(out)


def main(args):
    if len(args) == 3:  # one stretch of one recording
        rid, t0, t1 = args[0], float(args[1]), float(args[2])
        sheet(Recording(rid), t0, t1, paths.derived("labels", f"{rid}.presence_{t0:.0f}_{t1:.0f}.png"))
        return
    span = float(args[0]) if args else 150.0
    for rec in load_all():
        sheet(rec, 0, min(span, rec.duration), paths.derived("labels", f"{rec.id}.presence_start.png"))
        sheet(rec, max(0, rec.duration - span), rec.duration, paths.derived("labels", f"{rec.id}.presence_end.png"))


if __name__ == "__main__":
    main(sys.argv[1:])
