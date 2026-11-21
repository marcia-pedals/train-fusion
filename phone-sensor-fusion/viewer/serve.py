"""Serve the viewer: http://127.0.0.1:3040

    python -m viewer.export      # once, and after anything upstream changes
    python -m viewer.serve [port]

/            viewer/static/
/data/...    data/phone-sensor-fusion/viewer/   (what viewer/export.py wrote)
/audio/<id>  the recording's audio, straight from data/recordings/raw

A small server of its own because the browser needs HTTP range requests to seek in the
audio, which python's http.server doesn't do.
"""

import mimetypes
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from common import paths

STATIC = Path(__file__).parent / "static"
DATA = paths.DERIVED / "viewer"


def resolve(url_path: str) -> Path | None:
    path = url_path.split("?")[0]
    if m := re.fullmatch(r"/audio/(\w+)", path):
        return paths.RECORDINGS / m[1] / "audio_000.m4a"
    root, rel = (DATA, path[len("/data/"):]) if path.startswith("/data/") else (STATIC, path.lstrip("/") or "index.html")
    full = (root / rel).resolve()
    return full if full.is_relative_to(root.resolve()) else None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        full = resolve(self.path)
        if full is None or not full.is_file():
            self.send_error(404)
            return
        size = full.stat().st_size
        start, end = 0, size - 1
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m and (m[1] or m[2]):
            if m[1]:
                start, end = int(m[1]), min(int(m[2]), size - 1) if m[2] else size - 1
            else:
                start = max(size - int(m[2]), 0)
            if start > end:
                self.send_error(416)
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        ctype = "audio/mp4" if full.suffix == ".m4a" else mimetypes.guess_type(full.name)[0] or "application/octet-stream"
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with full.open("rb") as fh:
            fh.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = fh.read(min(left, 1 << 16))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser dropped the request (seeking does that)

    def log_message(self, *args):
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3040
    assert (DATA / "index.json").exists(), "run `python -m viewer.export` first"
    print(f"viewer at http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
