// The viewer's dev server. Local only: what it shows names recordings, which are private
// (AGENTS.md), so it is never built for or deployed anywhere.
//
//   /data/<path>         data/phone-sensor-fusion/<path>: what the pipeline wrote
//   /audio/<id>/<file>   a recording's own audio (data/recordings/raw/<id>/<file>, .m4a only),
//                        which model/ride_view.py names; nothing else of the raw recording
//
// Both with HTTP range requests, which seeking in audio needs. Open pages reload when the
// evaluation's results change.
import {createReadStream, statSync} from "node:fs";
import type {IncomingMessage, ServerResponse} from "node:http";
import path from "node:path";
import {fileURLToPath} from "node:url";
import react from "@vitejs/plugin-react";
import {defineConfig, type Plugin} from "vite";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DATA = path.resolve(HERE, "../../data/phone-sensor-fusion");
const RAW = path.resolve(HERE, "../../data/recordings/raw");
const TYPES: Record<string, string> = {
  ".parquet": "application/vnd.apache.parquet", ".json": "application/json", ".bin": "application/octet-stream",
  ".m4a": "audio/mp4", ".png": "image/png",
};

/** Serve files under `root`, those `allow` accepts, with range requests. */
function serveFiles(root: string, allow: (file: string) => boolean = () => true) {
  return (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    const rel = decodeURIComponent((req.url ?? "/").split("?")[0]);
    const file = path.resolve(root, "." + rel);
    if (!file.startsWith(root + path.sep) || !allow(file)) return next();
    let size: number;
    try {
      const st = statSync(file);
      if (!st.isFile()) return next();
      size = st.size;
    } catch {
      res.statusCode = 404;
      return res.end(`not found: ${rel} (has the pipeline run?)`);
    }
    res.setHeader("Content-Type", TYPES[path.extname(file)] ?? "application/octet-stream");
    res.setHeader("Accept-Ranges", "bytes");
    res.setHeader("Cache-Control", "no-cache");
    const m = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range ?? "");
    if (m && (m[1] || m[2])) {
      const start = m[1] ? Number(m[1]) : Math.max(size - Number(m[2]), 0);
      const end = m[1] && m[2] ? Math.min(Number(m[2]), size - 1) : size - 1;
      if (start > end) {
        res.statusCode = 416;
        res.setHeader("Content-Range", `bytes */${size}`);
        return res.end();
      }
      res.statusCode = 206;
      res.setHeader("Content-Range", `bytes ${start}-${end}/${size}`);
      res.setHeader("Content-Length", end - start + 1);
      createReadStream(file, {start, end}).on("error", () => res.end()).pipe(res);
    } else {
      res.setHeader("Content-Length", size);
      createReadStream(file).on("error", () => res.end()).pipe(res);
    }
  };
}

function data(): Plugin {
  return {
    name: "psf-data",
    configureServer(server) {
      // a new pipeline run: reload the page, so it never shows new code over old data
      const results = path.join(DATA, "eval");
      server.watcher.add(results);
      let pending: NodeJS.Timeout | undefined;
      server.watcher.on("all", (_event, file) => {
        if (!file.startsWith(results + path.sep)) return;
        clearTimeout(pending);
        pending = setTimeout(() => server.ws.send({type: "full-reload"}), 500);  // the run writes several files
      });
      server.middlewares.use("/data", serveFiles(DATA));
      server.middlewares.use("/audio", serveFiles(RAW, (f) => path.extname(f) === ".m4a"));
    },
  };
}

export default defineConfig({
  plugins: [react(), data()],
  server: {host: "127.0.0.1", port: 3040, strictPort: true},
});
