"""Candidate door-chime times, to be checked by eye (labels/README.md) before they become
labels. Not a feature extractor: just enough signal processing to tell the labeller where
to look.

The two chimes (found in exploration/tones.py):
  door-open chime : ~6 s steady tone at 2200 Hz with harmonics at 4400 and 6600 Hz
  door-close chime: 3520 Hz (harmonics of 1760 Hz), ~5 s then ~3 s

    python -m labels.propose [recording-id ...]
"""

import numpy as np
from scipy import ndimage, signal

from common.dsp import band_prominence
from common.recording import load_all

FS, NFFT, HOP = 16000, 1024, 256
CHIMES = {"open": (2200, 4400, 6600), "close": (3520, 5280, 7040)}


def runs(on, t, min_s):
    lab, _ = ndimage.label(on)
    out = []
    for (sl,) in ndimage.find_objects(lab):
        if t[sl.stop - 1] - t[sl.start] >= min_s:
            out.append((t[sl.start], t[sl.stop - 1]))
    return out


def main(only=()):
    for rec in load_all():
        if only and rec.id not in only:
            continue
        t0, x = rec.audio()
        f, t, S = signal.spectrogram(np.asarray(x), fs=FS, nperseg=NFFT, noverlap=NFFT - HOP)
        db = 10 * np.log10(S + 1e-16)
        t = t + t0
        print(f"## {rec.id}")
        for name, freqs in CHIMES.items():
            prom = np.array([band_prominence(db, f, fc) for fc in freqs])
            # at least two of the three partials clearly present
            on = (prom > 12).sum(axis=0) >= 2
            on = ndimage.binary_closing(on, np.ones(int(0.5 * FS / HOP)))
            print(name, " ".join(f"{a:.2f}-{b:.2f}" for a, b in runs(on, t, 1.0)))


if __name__ == "__main__":
    import sys

    main(sys.argv[1:])
