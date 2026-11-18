"""Which narrow-band tones occur in the audio, at what frequencies and when?

Per 0.128 s frame, a bin is "tonal" if it is >= 15 dB above the median of its +-300 Hz
neighbourhood. Prints, per frequency (25 Hz bins), the runs of >= 1 s where that frequency
is tonal: persistent pure tones are chimes/beeps, not speech or noise.

    python -m exploration.tones [min_run_s]
"""

import sys

import numpy as np
from scipy import ndimage, signal

from common.recording import load_all

FS, NFFT, HOP = 16000, 2048, 1024


def tonal_map(x):
    f, t, S = signal.spectrogram(x, fs=FS, nperseg=NFFT, noverlap=NFFT - HOP)
    db = 10 * np.log10(S + 1e-16)
    k = int(600 / (FS / NFFT)) | 1
    bg = ndimage.median_filter(db, size=(k, 1))
    return f, t, db - bg


def main(min_run=1.0):
    for rec in load_all():
        t0, x = rec.audio()
        f, t, prom = tonal_map(np.asarray(x))
        t = t + t0
        dt = HOP / FS
        tonal = prom > 15
        print(f"## {rec.id}")
        # group into 25 Hz bins (a tone smears over ~3 FFT bins)
        edges = np.arange(200, 8000, 25)
        rows = []
        for lo in edges:
            m = (f >= lo) & (f < lo + 25)
            on = tonal[m].any(axis=0)
            on = ndimage.binary_closing(on, np.ones(3))
            lab, n = ndimage.label(on)
            for sl in ndimage.find_objects(lab):
                dur = (sl[0].stop - sl[0].start) * dt
                if dur >= min_run:
                    rows.append((lo, t[sl[0].start], dur))
        by_f = {}
        for lo, ts, dur in rows:
            by_f.setdefault(lo, []).append((ts, dur))
        for lo in sorted(by_f):
            runs = by_f[lo]
            print(f"{lo:5d} Hz  n={len(runs):3d}  " + " ".join(f"{ts:.0f}({dur:.1f})" for ts, dur in runs[:40]))


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 1.0)
