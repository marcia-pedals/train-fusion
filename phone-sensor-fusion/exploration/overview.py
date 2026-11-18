"""First look at a recording: whole-ride accelerometer, gyro and audio spectrogram on one
time axis. Writes data/phone-sensor-fusion/exploration/<id>.overview.png.

    python -m exploration.overview [recording-id ...]
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal

from common import paths
from common.recording import load_all


def lowpass(x, fs, fc):
    sos = signal.butter(2, fc, fs=fs, output="sos")
    return signal.sosfiltfilt(sos, x, axis=0)


def main(only=None):
    for rec in load_all():
        if only and rec.id not in only:
            continue
        ta, a = rec.accel
        tg, g = rec.gyro
        fs = 1 / np.median(np.diff(ta))
        print(rec.id, f"dur {rec.duration:.0f}s accel fs {fs:.2f} max gap {np.diff(ta).max():.3f}",
              f"gyro max gap {np.diff(tg).max():.3f}", "audio t0", rec.audio()[0])
        fig, ax = plt.subplots(6, 1, figsize=(28, 20), sharex=True)
        al = lowpass(a, fs, 0.5)
        for i, c in enumerate("xyz"):
            ax[0].plot(ta, al[:, i], lw=0.6, label=c)
        ax[0].set_ylabel("accel <0.5Hz (g)"); ax[0].legend(loc="upper right")
        mag = np.linalg.norm(a, axis=1)
        hp = a - lowpass(a, fs, 1.0)
        rms = np.sqrt(lowpass(np.sum(hp**2, axis=1), fs, 0.2).clip(1e-12))
        ax[1].semilogy(ta, rms, lw=0.6); ax[1].set_ylabel("accel >1Hz rms (g)")
        gl = lowpass(g, fs, 0.5)
        for i, c in enumerate("xyz"):
            ax[2].plot(tg, gl[:, i], lw=0.6, label=c)
        ax[2].set_ylabel("gyro <0.5Hz (rad/s)"); ax[2].set_ylim(-0.1, 0.1)
        grms = np.sqrt(lowpass(np.sum((g - gl) ** 2, axis=1), fs, 0.2).clip(1e-12))
        ax[3].semilogy(tg, grms, lw=0.6); ax[3].set_ylabel("gyro >0.5Hz rms")
        # accelerometer spectrogram (vibration)
        f, tt, S = signal.spectrogram(mag - mag.mean(), fs=fs, nperseg=256, noverlap=128)
        ax[4].pcolormesh(tt + ta[0], f, 10 * np.log10(S + 1e-14), shading="auto", cmap="magma",
                         vmin=-90, vmax=-40)
        ax[4].set_ylabel("|accel| spectrogram (Hz)")
        t0, x = rec.audio()
        f, tt, S = signal.spectrogram(np.asarray(x), fs=16000, nperseg=2048, noverlap=0)
        k = 8  # average 8 frames (~1 s) per column
        n = S.shape[1] // k * k
        S = S[:, :n].reshape(len(f), -1, k).mean(-1)
        tt = tt[:n].reshape(-1, k).mean(-1)
        ax[5].pcolormesh(tt + t0, f, 10 * np.log10(S + 1e-14), shading="auto", cmap="magma",
                         vmin=-110, vmax=-50)
        ax[5].set_ylabel("audio spectrogram (Hz)"); ax[5].set_yscale("symlog", linthresh=500)
        ax[5].set_ylim(30, 8000)
        ax[5].set_xlabel("t (s since recording start)")
        ax[5].set_xticks(np.arange(0, rec.duration, 60))
        for x_ in ax:
            x_.grid(alpha=0.3)
        fig.suptitle(rec.id)
        fig.tight_layout()
        out = paths.derived("exploration", f"{rec.id}.overview.png")
        fig.savefig(out, dpi=70)
        print("wrote", out)


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
