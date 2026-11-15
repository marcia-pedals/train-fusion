"""What a JPEG's headers say about it, without decoding any pixels.

Enough to judge a webcam capture by metadata alone: dimensions, whether the file is
complete, a fingerprint of the quantization tables (the encoder's signature: Caltrans's
"Temporarily Unavailable" placeholder is made by a different encoder than the camera
frames, so it has different tables), the EXIF capture time, and the comment.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field


@dataclass
class JpegInfo:
    width: int | None = None
    height: int | None = None
    complete: bool = False          # ends with EOI (a cut-off download doesn't)
    qt: str | None = None           # short digest of all quantization tables, in order
    exif_time: str | None = None    # DateTimeOriginal (else DateTime), "YYYY:MM:DD HH:MM:SS"
    comment: str | None = None
    progressive: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if v not in (None, [], False)}
        d["complete"] = self.complete
        return d


def parse(b: bytes) -> JpegInfo | None:
    """Header info, or None if this isn't a JPEG at all (e.g. an HTML error page)."""
    if b[:3] != b"\xff\xd8\xff":
        return None
    info = JpegInfo(complete=b.rstrip(b"\x00").endswith(b"\xff\xd9"))
    qts = []
    i = 2
    while i + 4 <= len(b):
        if b[i] != 0xFF:
            info.errors.append(f"no marker at {i}")
            break
        m = b[i + 1]
        if m == 0xFF:  # fill byte
            i += 1
            continue
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        (n,) = struct.unpack(">H", b[i + 2:i + 4])
        seg = b[i + 4:i + 2 + n]
        if m == 0xDB:
            qts.append(seg)
        elif m in (0xC0, 0xC1, 0xC2, 0xC3) and len(seg) >= 5:
            info.height, info.width = struct.unpack(">HH", seg[1:5])
            info.progressive = m == 0xC2
        elif m == 0xE1 and seg[:6] == b"Exif\x00\x00":
            try:
                info.exif_time = exif_time(seg[6:])
            except (struct.error, IndexError, UnicodeDecodeError) as e:
                info.errors.append(f"exif: {e}")
        elif m == 0xFE:
            info.comment = seg.decode("latin-1").strip()
        if m == 0xDA:  # start of scan: the rest is entropy-coded data
            break
        i += 2 + n
    if qts:
        info.qt = hashlib.sha1(b"".join(qts)).hexdigest()[:8]
    return info


def exif_time(tiff: bytes) -> str | None:
    """DateTimeOriginal from the Exif sub-IFD if present, else IFD0's DateTime."""
    bo = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    if bo is None:
        return None

    def entries(off):
        (n,) = struct.unpack(bo + "H", tiff[off:off + 2])
        for k in range(n):
            e = off + 2 + 12 * k
            tag, typ, count = struct.unpack(bo + "HHI", tiff[e:e + 8])
            yield tag, typ, count, tiff[e + 8:e + 12]

    def ascii_at(count, raw):
        (o,) = struct.unpack(bo + "I", raw)
        s = raw[:count] if count <= 4 else tiff[o:o + count]
        return s.split(b"\x00")[0].decode("ascii").strip() or None

    (ifd0,) = struct.unpack(bo + "I", tiff[4:8])
    dt = sub = None
    for tag, typ, count, raw in entries(ifd0):
        if tag == 0x0132 and typ == 2:
            dt = ascii_at(count, raw)
        elif tag == 0x8769:
            (sub,) = struct.unpack(bo + "I", raw)
    if sub:
        for tag, typ, count, raw in entries(sub):
            if tag == 0x9003 and typ == 2:
                return ascii_at(count, raw) or dt
    return dt
