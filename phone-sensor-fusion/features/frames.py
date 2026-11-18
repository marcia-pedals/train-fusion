"""Stage 1 of the features: raw per-second measurements, straight from the sensors.

Everything here is deterministic and causal up to a couple of seconds (a frame needs only
samples from its own second, except `accel_spec`, whose 5 s window is centred on it), so
it can run on the phone as the samples arrive. Nothing here knows about labels or the map.

Frame n covers recording time [n, n+1) s. Per recording, features/<id>.frames.npz holds:

  open_chime, close_chime [T]  fraction of the second in which the door-open /
                               door-close chime's partials are present (0..1)
  tone_spec [T, F], tone_f [F] audio line spectrum 0..2.4 kHz: dB above the local
                               spectral floor, pooled over +-6 Hz and the 4 sub-frames
  rumble_db [T]                audio level, 20..70 Hz (rolling noise), dB
  accel_spec [T, Fa], accel_f  line spectrum of |acceleration|, 0..50 Hz
  vib_db [T, 2]                |acceleration| level in 2..10 Hz and 10..45 Hz, dB
  gyro_rms [T]                 log10 of the gyroscope RMS (rad/s)
  acc_mean, gyro_mean [T, 3]   mean specific force (g) and rotation rate (rad/s), phone axes

Only in recordings that have the sensor (absent otherwise, and everything downstream copes):

  baro_alt [T]                 pressure altitude, m (standard atmosphere; the weather shifts it)
  att_q [T, 4]                 iOS's fused attitude as a quaternion (x, y, z, w) turning phone
                               axes into world axes: x magnetic north, y west, z up

    python -m features.frames
"""

import numpy as np
from scipy import ndimage, signal

from common import paths
from common.dsp import band_prominence, line_prominence, quat_rotate, spectrogram_db
from common.recording import Recording, load_all

DT = 1.0
FS = 16000  # audio sample rate used throughout
IMU_FS = 100

# door chimes (exploration/tones.py): partials, and how present a partial must be
CHIME_PARTIALS = {"open": (2200.0, 4400.0, 6600.0), "close": (3520.0, 5280.0, 7040.0)}
CHIME_DB = 12.0  # a partial counts if it is this far above the neighbouring spectrum
CHIME_MIN_PARTIALS = 2
CHIME_NWIN, CHIME_HOP = 1024, 256  # 64 ms window, 16 ms hop

TONE_NWIN, TONE_NFFT, TONE_HOP = 4096, 8192, 4000  # 0.26 s window, ~2 Hz bins, 4 per second
TONE_FMAX = 2400.0
TONE_BG_HZ = 160.0
TONE_POOL_BINS = 3  # +-6 Hz: the tone moves up to ~16 Hz within a second

ACC_NWIN, ACC_NFFT = 512, 2048  # 5.12 s window, 0.05 Hz bins
ACC_BG_HZ = 3.0
ACC_POOL_BINS = 2

RUMBLE_BAND = (20.0, 70.0)  # below the pitch of voices
VIB_BANDS = ((2.0, 10.0), (10.0, 45.0))


def aligned_audio(rec: Recording) -> np.ndarray:
    """Audio with sample k at recording time k / FS (front padded / trimmed)."""
    t_first, x = rec.audio(FS)
    x = np.asarray(x)
    shift = int(round(t_first * FS))
    return np.r_[np.zeros(shift, np.float32), x] if shift >= 0 else x[-shift:]


def uniform_imu(t, x):
    """Resample onto a uniform IMU_FS grid starting at t = 0 (edges held)."""
    grid = np.arange(0, t[-1], 1 / IMU_FS)
    return np.stack([np.interp(grid, t, x[:, i]) for i in range(x.shape[1])], axis=1)


def per_second(values, per_s, T, how=np.mean):
    """Reduce an array with `per_s` leading entries per second to T frames."""
    n = T * per_s
    v = values[:n]
    if len(v) < n:
        v = np.r_[v, np.repeat(v[-1:], n - len(v), axis=0)]
    return how(v.reshape(T, per_s, *v.shape[1:]), axis=1)


def band_db(x, fs, band, T):
    sos = signal.butter(4, band, btype="band", fs=fs, output="sos")
    y = signal.sosfilt(sos, x) ** 2  # causal
    return 10 * np.log10(per_second(y, fs, T) + 1e-16)


def extract(rec: Recording) -> dict:
    T = int(rec.duration // DT)
    out = {}
    x = aligned_audio(rec)

    f, db = spectrogram_db(x, FS, CHIME_NWIN, CHIME_HOP)
    per_s = FS // CHIME_HOP  # 62.5 -> use exact frame times instead
    frame_of = ((np.arange(db.shape[1]) * CHIME_HOP + CHIME_NWIN / 2) / FS).astype(int)
    for name, partials in CHIME_PARTIALS.items():
        prom = np.array([band_prominence(db, f, fc) for fc in partials])
        on = (prom > CHIME_DB).sum(axis=0) >= CHIME_MIN_PARTIALS
        frac = np.bincount(frame_of, on, minlength=T)[:T] / np.maximum(np.bincount(frame_of, minlength=T)[:T], 1)
        out[f"{name}_chime"] = frac.astype(np.float32)

    f, db = spectrogram_db(x, FS, TONE_NWIN, TONE_HOP, TONE_NFFT)
    keep = f <= TONE_FMAX
    prom = line_prominence(db[keep], f[keep], TONE_BG_HZ)
    prom = ndimage.maximum_filter1d(prom, 2 * TONE_POOL_BINS + 1, axis=0)
    out["tone_spec"] = per_second(prom.T, FS // TONE_HOP, T).astype(np.float16)
    out["tone_f"] = f[keep]
    out["rumble_db"] = band_db(x, FS, RUMBLE_BAND, T).astype(np.float32)

    ta, a = rec.accel
    tg, g = rec.gyro
    a, g = uniform_imu(ta, a), uniform_imu(tg, g)
    mag = np.linalg.norm(a, axis=1)
    # 5.12 s windows centred on each frame's middle
    pad = ACC_NWIN // 2 - IMU_FS // 2
    f, db = spectrogram_db(np.r_[np.full(pad, mag[0]), mag - 0, np.full(ACC_NWIN, mag[-1])] - mag.mean(),
                           IMU_FS, ACC_NWIN, IMU_FS, ACC_NFFT)
    prom = line_prominence(db, f, ACC_BG_HZ)
    prom = ndimage.maximum_filter1d(prom, 2 * ACC_POOL_BINS + 1, axis=0)
    out["accel_spec"] = prom.T[:T].astype(np.float16)
    out["accel_f"] = f
    out["vib_db"] = np.stack([band_db(mag, IMU_FS, b, T) for b in VIB_BANDS], axis=1).astype(np.float32)
    out["gyro_rms"] = (0.5 * np.log10(per_second((g**2).sum(axis=1), IMU_FS, T) + 1e-12)).astype(np.float32)
    out["acc_mean"] = per_second(a, IMU_FS, T).astype(np.float32)
    out["gyro_mean"] = per_second(g, IMU_FS, T).astype(np.float32)
    # optional sensors (newer recordings); absent keys = sensor not recorded
    centres = np.arange(T) + 0.5
    if rec.has("baro"):
        tb, p_kpa, _ = rec.baro
        out["baro_alt"] = np.interp(centres, tb, 44330.0 * (1 - (p_kpa / 101.325) ** 0.1903)).astype(np.float32)
    if rec.has("device_motion"):
        dm = rec.device_motion
        i = np.clip(np.searchsorted(dm["t"], centres), 0, len(dm["t"]) - 1)  # the sample at each second's middle
        q = dm["q"][i] / np.linalg.norm(dm["q"][i], axis=1, keepdims=True)
        # store the rotation phone -> world (x magnetic north, y west, z up): whichever sense
        # of iOS's quaternion sends the measured gravity straight down
        down = quat_rotate(q, dm["gravity"][i])[:, 2].mean()
        if down > quat_rotate(q * [-1, -1, -1, 1], dm["gravity"][i])[:, 2].mean():
            q = q * [-1, -1, -1, 1]
        out["att_q"] = q.astype(np.float32)
    for k, v in out.items():
        assert k.endswith("_f") or len(v) == T, (k, v.shape, T)
    return out


def load(rid: str) -> dict:
    return dict(np.load(paths.derived("features", f"{rid}.frames.npz")))


def main():
    for rec in load_all():
        out = extract(rec)
        np.savez_compressed(paths.derived("features", f"{rec.id}.frames.npz"), **out)
        print(rec.id, {k: v.shape for k, v in out.items()})


if __name__ == "__main__":
    main()
