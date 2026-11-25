// Viewer for phone-sensor-fusion: features, filtered and smoothed HMM inference, and the
// audio, on one time axis. Data comes from viewer/export.py (see viewer/README.md).
// No build step, no dependencies: canvases drawn by hand.
"use strict";

const $ = (id) => document.getElementById(id);
const PADL = 56, PADR = 10; // plot area inside every track canvas (CSS px)
const V_MAX = 40; // m/s, top of the speed axis
const MODE_SHORT = ["moving", "stopped, before doors", "door-open chime", "doors open", "door-close chime", "stopped, after doors"];
const MODE_VARS = ["--m0", "--m1", "--m2", "--m3", "--m4", "--m5"];
const ABOARD_VARS = ["--off", "--aboard"]; // label only: off the train, on it
const PHONE_VARS = ["--p0", "--p1", "--p2"];

let D = null; // meta.json of the current recording
let BIN = {}; // spec, smooth, filt, tone: Uint8Array
let S = null; // D.series
let view = { t0: 0, t1: 1 };
let hoverT = null;
let dirty = true;
let css = {};
let tracks = [];
const audio = $("audio");

// ---------- small helpers ----------
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const fmtTime = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
const fmt = (v, nd = 1) => (v == null || Number.isNaN(v) ? "–" : v.toFixed(nd));
let pendingT = null; // a seek asked for before the audio was ready
const nowT = () => (!D ? 0 : pendingT != null ? pendingT : clamp(audio.currentTime + D.audio_t0, 0, D.T - 0.001));
const cursorT = () => (hoverT != null ? hoverT : nowT());

function readCss() {
  const cs = getComputedStyle(document.documentElement);
  for (const k of ["--surface", "--border", "--grid", "--text", "--text2", "--muted", "--smooth", "--filt", "--label",
    "--heat-mid", "--heat-hi", "--spec-hi", "--alt", "--off", "--aboard", ...MODE_VARS, ...PHONE_VARS]) css[k] = cs.getPropertyValue(k).trim();
  css.heatLut = lut([css["--surface"], css["--heat-mid"], css["--heat-hi"]]);
  css.specLut = lut([css["--surface"], css["--muted"], css["--spec-hi"]]);
}
function hex(c) { const n = parseInt(c.slice(1), 16); return [n >> 16, (n >> 8) & 255, n & 255]; }
function lut(stops) { // 256 x RGB, piecewise linear through the given hex colours
  const cols = stops.map(hex), out = new Uint8Array(768), seg = cols.length - 1;
  for (let i = 0; i < 256; i++) {
    const u = (i / 255) * seg, k = Math.min(Math.floor(u), seg - 1), f = u - k;
    for (let c = 0; c < 3; c++) out[i * 3 + c] = Math.round(cols[k][c] + f * (cols[k + 1][c] - cols[k][c]));
  }
  return out;
}

// ---------- geometry ----------
function plotW() { return $("tracks").clientWidth - 2 - PADL - PADR; }
function xOf(t) { return PADL + ((t - view.t0) / (view.t1 - view.t0)) * plotW(); }
function tOf(x) { return view.t0 + ((x - PADL) / plotW()) * (view.t1 - view.t0); }
function setView(t0, t1) {
  const span = clamp(t1 - t0, 4, D.T);
  t0 = clamp(t0, 0, D.T - span);
  view = { t0, t1: t0 + span };
  dirty = true;
}
function zoom(factor, around) {
  const span = (view.t1 - view.t0) * factor, f = (around - view.t0) / (view.t1 - view.t0);
  setView(around - f * span, around - f * span + span);
}

// ---------- drawing primitives ----------
function setupCanvas(cv, hCss) {
  const dpr = window.devicePixelRatio || 1, w = cv.clientWidth;
  cv.width = Math.round(w * dpr); cv.height = Math.round(hCss * dpr); cv.style.height = hCss + "px";
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}
function niceTimeStep(span) {
  for (const s of [1, 2, 5, 10, 30, 60, 120, 300, 600]) if (span / s <= 12) return s;
  return 600;
}
function timeGrid(ctx, h, labels) {
  const step = niceTimeStep(view.t1 - view.t0);
  ctx.strokeStyle = css["--grid"]; ctx.lineWidth = 1; ctx.fillStyle = css["--muted"]; ctx.font = "11px system-ui";
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  for (let t = Math.ceil(view.t0 / step) * step; t <= view.t1; t += step) {
    const x = Math.round(xOf(t)) + 0.5;
    if (labels) ctx.fillText(step < 60 ? fmtTime(t) : `${t / 60} min`, x, h / 2);
    else { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
  }
}
function yAxis(ctx, h, lo, hi, ticks, label = (v) => v) {
  ctx.font = "11px system-ui"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (const v of ticks) {
    const y = Math.round(h - ((v - lo) / (hi - lo)) * h) + 0.5;
    ctx.strokeStyle = css["--grid"]; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(PADL, y); ctx.lineTo(PADL + plotW(), y); ctx.stroke();
    ctx.fillStyle = css["--muted"]; ctx.fillText(label(v), PADL - 6, clamp(y, 6, h - 6));
  }
}
function clipPlot(ctx, h, fn) {
  ctx.save(); ctx.beginPath(); ctx.rect(PADL, 0, plotW(), h); ctx.clip(); fn(); ctx.restore();
}
function visibleRange() { return [Math.max(0, Math.floor(view.t0) - 1), Math.min(D.T, Math.ceil(view.t1) + 1)]; }
function line(ctx, arr, yOf, color, width = 2, alpha = 1, mask = null) {
  const [i0, i1] = visibleRange();
  ctx.strokeStyle = color; ctx.lineWidth = width; ctx.globalAlpha = alpha; ctx.lineJoin = "round";
  ctx.beginPath();
  let pen = false;
  for (let i = i0; i < i1; i++) {
    const v = arr[i];
    if (v == null || (mask && !mask[i])) { pen = false; continue; }
    const x = xOf(i + 0.5), y = yOf(v);
    if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    pen = true;
  }
  ctx.stroke(); ctx.globalAlpha = 1;
}
// one horizontal strip of stacked probabilities (rows: seconds, cols: classes)
function probStrip(ctx, probs, vars, y, h) {
  forEachColumn((i, x0, x1) => {
    const p = probs[i]; let yy = y;
    for (let k = 0; k < p.length; k++) {
      if (p[k] < 0.004) continue;
      const hh = p[k] * h;
      ctx.fillStyle = css[vars[k]]; ctx.fillRect(x0, yy, x1 - x0, hh); yy += hh;
    }
  });
}
function labelStrip(ctx, labels, vars, y, h) {
  forEachColumn((i, x0, x1) => {
    const k = labels[i];
    if (k < 0 || k >= vars.length) return; // unlabelled / off the train
    ctx.fillStyle = css[vars[k]]; ctx.fillRect(x0, y, x1 - x0, h);
  });
}
function forEachColumn(fn) { // per second when zoomed in, per pixel when zoomed out
  const pps = plotW() / (view.t1 - view.t0), [i0, i1] = visibleRange();
  if (pps >= 1) for (let i = i0; i < i1; i++) fn(i, xOf(i), xOf(i + 1) + 0.5);
  else for (let x = PADL; x < PADL + plotW(); x++) fn(clamp(Math.floor(tOf(x)), 0, D.T - 1), x, x + 1);
}
function rowLabel(ctx, text, y) {
  ctx.fillStyle = css["--muted"]; ctx.font = "11px system-ui"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
  ctx.fillText(text, PADL - 6, y);
}
// raster of a [time][bin] uint8 array into the plot area. binOfRow(yFrac 0=bottom..1=top) -> bin index or -1
function raster(ctx, cv, h, data, nBins, perSecond, binOfFrac, lutRgb, alpha = 255) {
  const dpr = window.devicePixelRatio || 1;
  const X0 = Math.round(PADL * dpr), W = Math.round(plotW() * dpr), H = Math.round(h * dpr);
  if (W <= 0) return;
  const img = ctx.createImageData(W, H), px = img.data, nT = data.length / nBins;
  const rowBin = new Int32Array(H);
  for (let y = 0; y < H; y++) rowBin[y] = binOfFrac(1 - (y + 0.5) / H);
  const tPerPx = (view.t1 - view.t0) / W;
  for (let x = 0; x < W; x++) {
    const fa = Math.floor((view.t0 + x * tPerPx) * perSecond);
    const fb = Math.max(fa + 1, Math.floor((view.t0 + (x + 1) * tPerPx) * perSecond));
    const step = Math.max(1, Math.floor((fb - fa) / 6));
    for (let y = 0; y < H; y++) {
      const b = rowBin[y]; let m = 0;
      if (b >= 0) for (let fr = fa; fr < fb; fr += step) {
        if (fr < 0 || fr >= nT) continue;
        const v = data[fr * nBins + b]; if (v > m) m = v;
      }
      const o = (y * W + x) * 4;
      px[o] = lutRgb[m * 3]; px[o + 1] = lutRgb[m * 3 + 1]; px[o + 2] = lutRgb[m * 3 + 2]; px[o + 3] = alpha;
    }
  }
  ctx.putImageData(img, X0, 0);
}

// ---------- tracks ----------
function autoRange(arrs, pad = 0.08) {
  const v = arrs.flat().filter((x) => x != null).sort((a, b) => a - b);
  const lo = v[Math.floor(v.length * 0.005)], hi = v[Math.floor(v.length * 0.995)], d = (hi - lo) * pad || 1;
  return [lo - d, hi + d];
}
function ticksFor(lo, hi, n = 4) {
  const raw = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(+v.toFixed(6));
  return out;
}
function lineTrack(title, desc, series, opts = {}) {
  // series: [{name, key, color var, nd}]
  const [lo, hi] = opts.range || autoRange(series.map((s) => S[s.key]));
  return {
    title, desc, h: opts.h || 84,
    legend: series.map((s) => ({ name: s.name, color: s.color, line: true, value: (i) => fmt(S[s.key][i], s.nd ?? 2) })),
    draw(ctx, cv, h) {
      timeGrid(ctx, h);
      yAxis(ctx, h, lo, hi, opts.ticks || ticksFor(lo, hi));
      clipPlot(ctx, h, () => {
        for (const s of series) line(ctx, S[s.key], (v) => h - ((v - lo) / (hi - lo)) * h, css[s.color], s.width || 1.5);
      });
    },
  };
}
function buildTracks() {
  const spec = D.spec, heatBins = D.heat.bins, dv = D.speed_grid.dv;
  const yV = (h) => (v) => h - (v / V_MAX) * h;
  tracks = [
    { title: null, h: 18, draw(ctx, cv, h) { timeGrid(ctx, h, true); } },
    {
      title: "Audio", desc: "spectrogram, 0–5 kHz, each frequency relative to its own median. Lines: where the motor tone would be at the model's speed",
      h: 190,
      legend: [{ name: "tone at smoothed speed", color: "--smooth", line: true }, { name: "at filtered speed", color: "--filt", line: true }],
      draw(ctx, cv, h) {
        raster(ctx, cv, h, BIN.spec, spec.bins, spec.fps, (f) => Math.min(spec.bins - 1, Math.floor(f * spec.bins)), css.specLut);
        const kHz = [1, 2, 3, 4, 5].filter((k) => k * 1000 <= spec.fmax);
        ctx.font = "11px system-ui"; ctx.textAlign = "right"; ctx.textBaseline = "middle"; ctx.fillStyle = css["--muted"];
        for (const k of kHz) ctx.fillText(`${k} kHz`, PADL - 6, clamp(h - (k * 1000 / spec.fmax) * h, 6, h - 6));
        if (!$("toneline").checked) return;
        clipPlot(ctx, h, () => {
          for (const [key, col] of [["v_filt", "--filt"], ["v_smooth", "--smooth"]])
            for (const ratio of D.tone_ratios) {
              const moving = S[key].map((v) => v > 1.5);
              line(ctx, S[key], (v) => h - ((v * ratio) / spec.fmax) * h, css[col], 1.2, ratio === D.tone_ratios[0] ? 0.9 : 0.45, moving);
            }
        });
      },
    },
    {
      title: "Accelerometer", desc: `line spectrum of |acceleration|, 0–50 Hz. Lines: wheel-rotation partials at the model's speed (${D.accel.note})`,
      h: 130,
      legend: [{ name: "wheel lines at smoothed speed", color: "--smooth", line: true }, { name: "at filtered speed", color: "--filt", line: true }],
      draw(ctx, cv, h) {
        const A = D.accel;
        raster(ctx, cv, h, BIN.accel, A.bins, 1, (f) => Math.min(A.bins - 1, Math.floor(f * A.bins)), css.specLut);
        ctx.font = "11px system-ui"; ctx.textAlign = "right"; ctx.textBaseline = "middle"; ctx.fillStyle = css["--muted"];
        for (const hz of [10, 20, 30, 40]) ctx.fillText(`${hz} Hz`, PADL - 6, h - (hz / A.fmax) * h);
        if (!$("toneline").checked) return;
        clipPlot(ctx, h, () => {
          for (const [key, col] of [["v_filt", "--filt"], ["v_smooth", "--smooth"]])
            for (const ratio of A.wheel_ratios) {
              const moving = S[key].map((v) => v > 1.5);
              line(ctx, S[key], (v) => h - ((v * ratio) / A.fmax) * h, css[col], 1.2, 0.6, moving);
            }
        });
      },
    },
    {
      title: "Speed", desc: "m/s", h: 230,
      legend: [
        { name: "label", color: "--label", line: true, value: (i) => fmt(S.v_label[i]) + (S.v_label[i] != null && !S.v_label_confident[i] ? " (guess)" : "") },
        { name: "smoothed ± sd", color: "--smooth", line: true, value: (i) => `${fmt(S.v_smooth[i])} ± ${fmt(S.v_smooth_sd[i])}` },
        { name: "filtered ± sd", color: "--filt", line: true, value: (i) => `${fmt(S.v_filt[i])} ± ${fmt(S.v_filt_sd[i])}` },
      ],
      draw(ctx, cv, h) {
        const which = $("heat").value;
        if (which !== "none") {
          raster(ctx, cv, h, BIN[which], heatBins, 1, (f) => {
            const k = Math.round((f * V_MAX) / dv); return k < heatBins ? k : -1;
          }, css.heatLut);
        }
        timeGrid(ctx, h);
        yAxis(ctx, h, 0, V_MAX, [0, 10, 20, 30, 40]);
        clipPlot(ctx, h, () => {
          // labelled door openings
          ctx.font = "11px system-ui"; ctx.textAlign = "left"; ctx.textBaseline = "top";
          for (const st of D.stops) {
            const x = Math.round(xOf(st.t_open)) + 0.5;
            ctx.strokeStyle = css["--muted"]; ctx.lineWidth = 1; ctx.setLineDash([2, 3]);
            ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); ctx.setLineDash([]);
            ctx.fillStyle = css["--text2"]; ctx.fillText(st.station, x + 4, 3);
          }
          const y = yV(h);
          line(ctx, S.v_label, y, css["--label"], 1, 0.55);
          line(ctx, S.v_label, y, css["--label"], 2.5, 0.9, S.v_label_confident);
          line(ctx, S.v_filt, y, css["--filt"], 1.5);
          line(ctx, S.v_smooth, y, css["--smooth"], 2);
        });
      },
    },
    {
      title: "Mode", desc: "probability of each mode, per second, and of the rider being off the train; last row: was the rider on the train (label)", h: 114,
      legend: [...MODE_SHORT.map((name, k) => ({ name, color: MODE_VARS[k] })),
        { name: "on the train", color: "--aboard" }, { name: "off the train", color: "--off" }],
      draw(ctx, cv, h) {
        clipPlot(ctx, h, () => {
          probStrip(ctx, S.mode_smooth, [...MODE_VARS, "--off"], 2, 24); // 7th column: off the train
          probStrip(ctx, S.mode_filt, [...MODE_VARS, "--off"], 30, 24);
          labelStrip(ctx, S.mode_label, MODE_VARS, 58, 24);
          labelStrip(ctx, S.on_train_label, ABOARD_VARS, 86, 24);
        });
        rowLabel(ctx, "smooth", 14); rowLabel(ctx, "filter", 42); rowLabel(ctx, "label", 70); rowLabel(ctx, "aboard?", 98);
      },
    },
    {
      title: "Phone", desc: "what the phone is doing (decides which measurements count)", h: 58,
      legend: D.phone_names.map((name, k) => ({ name, color: PHONE_VARS[k] })),
      draw(ctx, cv, h) {
        clipPlot(ctx, h, () => {
          probStrip(ctx, S.phone, PHONE_VARS, 2, 24);
          labelStrip(ctx, S.phone_label, PHONE_VARS, 30, 24);
        });
        rowLabel(ctx, "filter", 14); rowLabel(ctx, "label", 42);
      },
    },
    lineTrack("Door chimes", "fraction of the second each chime's partials are present",
      [{ name: "open", key: "open_chime", color: "--m2" }, { name: "close", key: "close_chime", color: "--m4" }],
      { range: [-0.05, 1.05], ticks: [0, 0.5, 1], h: 64 }),
    lineTrack("Along-track acceleration", "m/s², phone resting only; sign and offset as the model estimated them",
      [{ name: "change of smoothed speed", key: "dv_smooth", color: "--smooth", width: 2.5 }, { name: "accelerometer", key: "a_long", color: "--text", width: 1 }],
      { range: [-1.6, 1.6], ticks: [-1, 0, 1] }),
    lineTrack("Rumble", "audio level 20–70 Hz, dB", [{ name: "level", key: "rumble_db", color: "--text2", nd: 1 }]),
    lineTrack("Vibration", "accelerometer level, dB",
      [{ name: "2–10 Hz", key: "vib_low_db", color: "--p2", nd: 1 }, { name: "10–45 Hz", key: "vib_high_db", color: "--alt", nd: 1 }]),
    lineTrack("Yaw rate", "rad/s about the vertical, phone resting only (curves)",
      [{ name: "gyro", key: "yaw_rate", color: "--text2", nd: 4 }], { range: [-0.03, 0.03], ticks: [-0.02, 0, 0.02] }),
    lineTrack("Gyroscope energy", "log₁₀ RMS rad/s (drives the phone state)", [{ name: "gyro", key: "gyro_rms", color: "--text2" }]),
  ];
  const host = $("tracks");
  host.querySelectorAll(".track").forEach((e) => e.remove());
  for (const tr of tracks) {
    const el = document.createElement("div"); el.className = "track";
    if (tr.title) {
      const head = document.createElement("div"); head.className = "head";
      head.innerHTML = `<span class="title">${tr.title}</span><span class="desc">${tr.desc || ""}</span>`;
      const lg = document.createElement("div"); lg.className = "legend";
      tr.valueEls = [];
      for (const it of tr.legend || []) {
        const s = document.createElement("span");
        s.innerHTML = `<i class="sw ${it.line ? "line" : ""}" style="background:var(${it.color})"></i>${it.name} `;
        if (it.value) { const b = document.createElement("b"); s.appendChild(b); tr.valueEls.push([b, it.value]); }
        lg.appendChild(s);
      }
      head.appendChild(lg); el.appendChild(head);
    } else el.style.padding = "0";
    tr.cv = document.createElement("canvas"); el.appendChild(tr.cv); host.appendChild(el);
  }
}
function drawTracks() {
  for (const tr of tracks) {
    const ctx = setupCanvas(tr.cv, tr.h);
    ctx.clearRect(0, 0, tr.cv.clientWidth, tr.h);
    tr.draw(ctx, tr.cv, tr.h);
  }
  drawMini();
}
function drawMini() {
  const cv = $("mini"), h = 44, ctx = setupCanvas(cv, h), w = cv.clientWidth;
  const X = (t) => PADL + (t / D.T) * (w - PADL - PADR);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = css["--m0"]; ctx.beginPath(); ctx.moveTo(X(0), h);
  for (let i = 0; i < D.T; i += 2) ctx.lineTo(X(i + 0.5), h - 2 - (S.v_smooth[i] / V_MAX) * (h - 6));
  ctx.lineTo(X(D.T), h); ctx.fill();
  ctx.fillStyle = css["--m3"];
  for (let i = 0; i < D.T; i++) if (S.mode_smooth[i][0] < 0.5) ctx.fillRect(X(i), h - 4, Math.max(1, X(i + 1) - X(i)), 4);
  ctx.strokeStyle = css["--text"]; ctx.lineWidth = 1.5;
  ctx.strokeRect(X(view.t0) + 0.5, 1, Math.max(3, X(view.t1) - X(view.t0)), h - 2);
  ctx.fillStyle = css["--muted"]; ctx.font = "11px system-ui"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
  ctx.fillText("ride", PADL - 6, h / 2);
  cv._X = X;
}

// ---------- the "now" panel ----------
function buildBars(host, names, vars) {
  host.className = "bars"; host.innerHTML = "";
  return names.map((name, k) => {
    const row = document.createElement("div"); row.className = "row";
    row.innerHTML = `<span class="name"><i class="sw" style="background:var(${vars[k]})"></i>${name}</span>
      <span class="pair"><span class="bar"><i style="background:var(--smooth)"></i></span><span class="bar"><i style="background:var(--filt)"></i></span></span>
      <span class="val"></span>`;
    host.appendChild(row);
    return { name: row.querySelector(".name"), a: row.querySelectorAll(".bar i")[0], b: row.querySelectorAll(".bar i")[1], val: row.querySelector(".val") };
  });
}
let modeBars = [], phoneBars = [];
function updateNow() {
  const t = cursorT(), i = clamp(Math.floor(t), 0, D.T - 1);
  $("clock").textContent = fmtTime(t);
  $("clocksub").textContent = hoverT != null ? "under the cursor" : audio.paused ? "paused" : "playing";
  const mph = (v) => (v == null ? "" : `${(v * 2.23694).toFixed(0)} mph`);
  $("vS").textContent = fmt(S.v_smooth[i]) + " m/s"; $("vSsub").textContent = `± ${fmt(S.v_smooth_sd[i])} · ${mph(S.v_smooth[i])}`;
  $("vF").textContent = fmt(S.v_filt[i]) + " m/s"; $("vFsub").textContent = `± ${fmt(S.v_filt_sd[i])} · ${mph(S.v_filt[i])}`;
  for (const tr of tracks) for (const [el, fn] of tr.valueEls || []) el.textContent = fn(i);
  modeBars.forEach((b, k) => {
    const ps = S.mode_smooth[i][k], pf = S.mode_filt[i][k];
    b.a.style.width = `${ps * 100}%`; b.b.style.width = `${pf * 100}%`;
    b.val.textContent = `${ps.toFixed(2)} · ${pf.toFixed(2)}`;
    b.name.classList.toggle("on", S.mode_label[i] === k);
  });
  phoneBars.forEach((b, k) => {
    const p = S.phone[i][k];
    b.a.style.width = "0"; b.a.parentElement.style.display = "none";
    b.b.style.width = `${p * 100}%`; b.val.textContent = p.toFixed(2);
    b.name.classList.toggle("on", S.phone_label[i] === k);
  });
  drawDist(i); drawLine(i);
}
function drawDist(i) {
  const cv = $("dist"), h = 120, ctx = setupCanvas(cv, h), w = cv.clientWidth;
  const n = D.heat.bins, dv = D.speed_grid.dv, L = 26, R = 6, B = 18, top = 6;
  const X = (v) => L + (v / V_MAX) * (w - L - R), Y = (p) => h - B - p * (h - B - top);
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = css["--grid"]; ctx.fillStyle = css["--muted"]; ctx.font = "11px system-ui"; ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (const v of [0, 10, 20, 30, 40]) {
    ctx.beginPath(); ctx.moveTo(X(v) + 0.5, top); ctx.lineTo(X(v) + 0.5, h - B); ctx.stroke();
    ctx.fillText(v === 40 ? "40 m/s" : v, v === 40 ? X(v) - 14 : X(v), h - B + 4);
  }
  const draw = (data, col, off) => {
    const p = (k) => (data[i * n + k] / 255) ** 2;
    ctx.fillStyle = col; ctx.fillRect(L - 12 + off, Y(p(0)), 5, Y(0) - Y(p(0))); // standing
    ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.beginPath();
    for (let k = 1; k < n; k++) { const x = X(k * dv), y = Y(p(k)); if (k === 1) ctx.moveTo(x, y); else ctx.lineTo(x, y); }
    ctx.stroke();
  };
  draw(BIN.filt, css["--filt"], 6); draw(BIN.smooth, css["--smooth"], 0);
  const vl = S.v_label[i];
  if (vl != null) {
    ctx.fillStyle = css["--label"]; const x = X(vl);
    ctx.beginPath(); ctx.moveTo(x, h - B); ctx.lineTo(x - 5, h - B + 8); ctx.lineTo(x + 5, h - B + 8); ctx.fill();
  }
}
function drawLine(i) {
  const Ln = D.line, n = Ln.stations.length, rowH = 17, h = n * rowH + 4, cv = $("line"), ctx = setupCanvas(cv, h), w = cv.clientWidth;
  ctx.clearRect(0, 0, w, h);
  let j = -1;
  for (let k = 0; k < Ln.t_stop.length; k++) if (Ln.t_stop[k] <= i) j = k;
  let truth = null;
  for (const st of D.stops) if (st.t_open <= i + 1) truth = st.station;
  const yRow = (k) => 2 + k * rowH + rowH / 2, GX = 9, NX = 22, BX = 172, BW = w - BX - 4;
  ctx.strokeStyle = css["--border"]; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(GX, yRow(0)); ctx.lineTo(GX, yRow(n - 1)); ctx.stroke();
  ctx.font = "11.5px system-ui"; ctx.textBaseline = "middle";
  for (let k = 0; k < n; k++) {
    const y = yRow(k), isTruth = Ln.stations[k] === truth, onRide = D.journey.stations.includes(Ln.stations[k]);
    ctx.fillStyle = css["--surface"]; ctx.strokeStyle = css["--muted"]; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(GX, y, 3, 0, 7); ctx.fill(); ctx.stroke();
    ctx.textAlign = "left"; ctx.fillStyle = isTruth ? css["--text"] : onRide ? css["--text2"] : css["--muted"];
    ctx.font = `${isTruth ? "650 " : ""}11.5px system-ui`;
    ctx.fillText(Ln.stations[k].replace(" 4th & King Street", "") + (isTruth ? "  ◂ label" : ""), NX, y);
    ctx.fillStyle = css["--grid"]; ctx.fillRect(BX, y - 6, BW, 5); ctx.fillRect(BX, y + 1, BW, 5);
    if (j >= 0) {
      const ps = Ln.smooth[j][2 * k] + Ln.smooth[j][2 * k + 1], pf = Ln.filt[j][2 * k] + Ln.filt[j][2 * k + 1];
      ctx.fillStyle = css["--smooth"]; ctx.fillRect(BX, y - 6, BW * ps, 5);
      ctx.fillStyle = css["--filt"]; ctx.fillRect(BX, y + 1, BW * pf, 5);
    }
  }
  const note = $("linenote");
  if (j < 0) { note.textContent = "No door opening heard yet: every station and both directions are equally likely."; return; }
  // best estimate of position: most probable (station, direction) given all stops, plus distance since
  const post = Ln.smooth[j]; let m = 0;
  for (let k = 1; k < post.length; k++) if (post[k] > post[m]) m = k;
  const a = m >> 1, dir = m & 1 ? -1 : 1, since = S.dist_smooth[i] - S.dist_smooth[Ln.t_stop[j]];
  const s = Ln.s_m[a] + dir * since;
  let k = 0; while (k < n - 2 && Ln.s_m[k + 1] < s) k++;
  const y = yRow(k) + clamp((s - Ln.s_m[k]) / (Ln.s_m[k + 1] - Ln.s_m[k]), -0.5, 1.5) * rowH;
  ctx.fillStyle = css["--smooth"]; ctx.strokeStyle = css["--surface"]; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(GX, y, 6, 0, 7); ctx.fill(); ctx.stroke();
  let mf = 0; for (let q = 1; q < Ln.filt[j].length; q++) if (Ln.filt[j][q] > Ln.filt[j][mf]) mf = q;
  note.innerHTML = `After door opening ${j + 1}: bars are the belief over stations, ` +
    `<i class="sw s-smooth"></i>given all stops and <i class="sw s-filt"></i>given the stops so far ` +
    `(now: ${Ln.stations[mf >> 1].replace(" 4th & King Street", "")}, ${mf & 1 ? "north" : "south"}bound, p = ${Ln.filt[j][mf].toFixed(2)}). ` +
    `Dot: best position, ${(since / 1000).toFixed(2)} km past ${Ln.stations[a].replace(" 4th & King Street", "")}.`;
}

// ---------- playhead, loop ----------
function placeCursors() {
  const ph = $("playhead"), x = xOf(nowT());
  ph.style.display = x < PADL || x > PADL + plotW() ? "none" : "block";
  ph.style.left = `${x + 1}px`;
  const hv = $("hover");
  if (hoverT == null) hv.style.display = "none";
  else { hv.style.display = "block"; hv.style.left = `${xOf(hoverT) + 1}px`; $("hovert").textContent = fmtTime(hoverT); }
}
let lastSecond = -1;
function frame() {
  if (D) {
    const t = nowT();
    if (!audio.paused && $("follow").checked && view.t1 - view.t0 < D.T - 1) {
      const f = (t - view.t0) / (view.t1 - view.t0);
      if (f > 0.85 || f < 0) setView(t - 0.15 * (view.t1 - view.t0), t + 0.85 * (view.t1 - view.t0));
    }
    if (dirty) { drawTracks(); dirty = false; lastSecond = -1; }
    placeCursors();
    const key = Math.floor(cursorT()) + (hoverT != null ? 1e6 : 0) + (audio.paused ? 2e6 : 0);
    if (key !== lastSecond) { updateNow(); lastSecond = key; }
  }
  requestAnimationFrame(frame);
}
function seek(t) {
  t = clamp(t, 0, D.T - 0.001); lastSecond = -1;
  if (audio.readyState < 1) { pendingT = t; return; }
  pendingT = null; audio.currentTime = Math.max(0, t - D.audio_t0);
}
audio.addEventListener("loadedmetadata", () => { if (pendingT != null) seek(pendingT); });

// ---------- loading ----------
async function load(id) {
  audio.pause(); $("play").textContent = "Play";
  D = null; pendingT = null;
  const meta = await (await fetch(`/data/${id}/meta.json`)).json();
  const bin = async (f) => new Uint8Array(await (await fetch(`/data/${id}/${f}`)).arrayBuffer());
  const [spec, smooth, filt, tone, wheel, accel] = await Promise.all([meta.spec.file, meta.heat.smooth, meta.heat.filt,
    meta.heat.tone, meta.heat.wheel, meta.accel.file].map(bin));
  BIN = { spec, smooth, filt, tone, wheel, accel };
  D = meta; S = meta.series;
  audio.src = `/audio/${id}`; audio.playbackRate = +$("rate").value;
  $("summary").textContent = `${D.journey.direction}bound, ${D.journey.stations[0]} → ${D.journey.stations.at(-1)}; phone ${D.journey.phone}. ` +
    `Model: ${D.params}. ${D.summary}`;
  readCss(); buildTracks();
  modeBars = buildBars($("modes"), MODE_SHORT, MODE_VARS);
  phoneBars = buildBars($("phone"), D.phone_names, PHONE_VARS);
  view = { t0: 0, t1: D.T }; dirty = true;
}
// #<recording>&t=<seconds>&span=<seconds>: open at a moment (and keep the address in step)
function readHash() {
  const [id, ...rest] = location.hash.slice(1).split("&");
  return { id, ...Object.fromEntries(rest.map((kv) => kv.split("="))) };
}
function writeHash() {
  if (D) history.replaceState(null, "", `#${D.id}&t=${Math.floor(nowT())}&span=${Math.round(view.t1 - view.t0)}`);
}
setInterval(writeHash, 1000);

// ---------- events ----------
$("play").onclick = () => { if (audio.paused) audio.play(); else audio.pause(); };
audio.onplay = () => { $("play").textContent = "Pause"; lastSecond = -1; };
audio.onpause = () => { $("play").textContent = "Play"; lastSecond = -1; };
$("back").onclick = () => seek(nowT() - 10);
$("fwd").onclick = () => seek(nowT() + 10);
$("rate").onchange = (e) => { audio.playbackRate = +e.target.value; };
$("rec").onchange = (e) => load(e.target.value);
$("heat").onchange = $("toneline").onchange = () => { dirty = true; };
document.querySelectorAll("[data-span]").forEach((b) => (b.onclick = () => {
  const span = +b.dataset.span || D.T, t = nowT();
  setView(t - span * 0.3, t + span * 0.7);
}));
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "SELECT" || e.target.tagName === "INPUT") return;
  if (e.code === "Space") { e.preventDefault(); $("play").click(); }
  if (e.code === "ArrowLeft") seek(nowT() - (e.shiftKey ? 1 : 10));
  if (e.code === "ArrowRight") seek(nowT() + (e.shiftKey ? 1 : 10));
});
{
  const host = $("tracks"); let drag = null;
  const localX = (e) => e.clientX - host.getBoundingClientRect().left - 1;
  host.addEventListener("pointerdown", (e) => { drag = { x: localX(e), view: { ...view }, moved: false }; host.setPointerCapture(e.pointerId); });
  host.addEventListener("pointermove", (e) => {
    const x = localX(e);
    if (drag) {
      if (Math.abs(x - drag.x) > 4) drag.moved = true;
      if (drag.moved) {
        const dt = ((drag.x - x) / plotW()) * (drag.view.t1 - drag.view.t0);
        setView(drag.view.t0 + dt, drag.view.t1 + dt); hoverT = null;
      }
    } else hoverT = x >= PADL && x <= PADL + plotW() ? clamp(tOf(x), 0, D.T - 0.001) : null;
  });
  host.addEventListener("pointerup", (e) => { if (drag && !drag.moved) seek(tOf(localX(e))); drag = null; });
  host.addEventListener("pointerleave", () => { if (!drag) hoverT = null; });
  host.addEventListener("wheel", (e) => {
    if (e.ctrlKey || e.metaKey) { e.preventDefault(); zoom(Math.exp(e.deltaY * 0.01), tOf(localX(e))); }
    else if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
      e.preventDefault();
      const dt = (e.deltaX / plotW()) * (view.t1 - view.t0); setView(view.t0 + dt, view.t1 + dt);
    }
  }, { passive: false });
  const mini = $("mini"); let md = false;
  const go = (e) => {
    const w = mini.clientWidth, t = ((e.clientX - mini.getBoundingClientRect().left - PADL) / (w - PADL - PADR)) * D.T, span = view.t1 - view.t0;
    setView(t - span / 2, t + span / 2);
  };
  mini.addEventListener("pointerdown", (e) => { md = true; mini.setPointerCapture(e.pointerId); go(e); });
  mini.addEventListener("pointermove", (e) => md && go(e));
  mini.addEventListener("pointerup", () => { md = false; });
}
new ResizeObserver(() => { dirty = true; }).observe($("tracks"));
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readCss(); dirty = true; });

(async () => {
  const ids = await (await fetch("/data/index.json")).json();
  $("rec").innerHTML = ids.map((id) => `<option>${id}</option>`).join("");
  const h = readHash(), id = ids.includes(h.id) ? h.id : ids.at(-1);
  $("rec").value = id;
  await load(id);
  if (h.t) {
    const span = +h.span || D.T;
    seek(+h.t);
    setView(+h.t - span * 0.3, +h.t + span * 0.7);
  }
  requestAnimationFrame(frame);
})();
