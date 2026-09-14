/**
 * Batch-2: ffmpeg/ffprobe execution layer (legacy spawn | fluent-ffmpeg).
 * Business logic (staging indices, mux state) stays in stream-ingest.mjs.
 */

import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

let fluentFfmpeg = null;

function loadFluent() {
  if (fluentFfmpeg) return fluentFfmpeg;
  return import("fluent-ffmpeg").then((mod) => {
    fluentFfmpeg = mod.default || mod;
    return fluentFfmpeg;
  });
}

/** Batch-2: legacy | fluent. Default legacy for gray rollout. */
export function deriveMuxBackend() {
  return String(process.env.DERIVE_MUX_BACKEND || "legacy").trim().toLowerCase();
}

/** full = one-shot encode from staging (no incremental mux-state). incremental = legacy path. */
export function deriveMuxMode() {
  return String(process.env.DERIVE_MUX_MODE || "full").trim().toLowerCase();
}

function x264OutputArgs() {
  const preset = String(process.env.DERIVE_MUX_X264_PRESET || "veryfast").trim();
  const threads = String(process.env.DERIVE_MUX_THREADS ?? "0").trim();
  const args = ["-c:v", "libx264", "-preset", preset, "-pix_fmt", "yuv420p"];
  if (threads) args.push("-threads", threads);
  return args;
}

export function isFullMuxMode() {
  return deriveMuxMode() !== "incremental";
}

/** @typedef {{ ok: boolean, exitCode?: number|null, stderr?: string, backend?: string }} MuxExecResult */

function spawnFfmpeg(args) {
  return new Promise((resolve) => {
    let stderr = "";
    const ff = spawn("ffmpeg", args, { stdio: ["ignore", "ignore", "pipe"] });
    ff.stderr?.on("data", (d) => {
      stderr += d.toString();
    });
    ff.on("close", (code) =>
      resolve({
        ok: code === 0,
        exitCode: code,
        stderr: stderr.trim().slice(0, 2000),
        backend: "legacy",
      }),
    );
    ff.on("error", (err) =>
      resolve({
        ok: false,
        exitCode: null,
        stderr: String(err?.message || err).slice(0, 500),
        backend: "legacy",
      }),
    );
  });
}

function probeMp4StreamJson(filePath, { countFrames = false } = {}) {
  const args = [
    "-v",
    "error",
    "-select_streams",
    "v:0",
    "-show_entries",
    "stream=nb_frames,nb_read_frames,duration,r_frame_rate",
    "-of",
    "json",
  ];
  if (countFrames) args.push("-count_frames");
  args.push(filePath);
  const res = spawnSync("ffprobe", args, { encoding: "utf8" });
  if (res.status !== 0) return null;
  try {
    return JSON.parse(res.stdout || "{}")?.streams?.[0] || null;
  } catch {
    return null;
  }
}

function frameCountFromStream(stream, defaultFps = 30) {
  if (!stream) return 0;
  const nb = Number(stream.nb_read_frames ?? stream.nb_frames);
  if (Number.isFinite(nb) && nb > 0) return Math.floor(nb);
  const duration = Number(stream.duration);
  const rateParts = String(stream.r_frame_rate || "0/1").split("/");
  const fps =
    rateParts.length === 2 && Number(rateParts[1])
      ? Number(rateParts[0]) / Number(rateParts[1])
      : defaultFps;
  if (Number.isFinite(duration) && duration > 0 && fps > 0) {
    return Math.max(1, Math.round(duration * fps));
  }
  return 0;
}

export function probeMp4DurationSec(filePath, fallbackFps = 30) {
  const stream = probeMp4StreamJson(filePath);
  if (!stream) return 0;
  const duration = Number(stream.duration);
  if (Number.isFinite(duration) && duration > 0) return duration;
  const nb = Number(stream.nb_read_frames ?? stream.nb_frames);
  const rateParts = String(stream.r_frame_rate || "0/1").split("/");
  const fps =
    rateParts.length === 2 && Number(rateParts[1])
      ? Number(rateParts[0]) / Number(rateParts[1])
      : fallbackFps;
  if (Number.isFinite(nb) && nb > 0 && fps > 0) return nb / fps;
  return 0;
}

export function probeMp4FrameCount(filePath, { defaultFps = 30, exact = false } = {}) {
  if (!filePath || !fs.existsSync(filePath)) return 0;
  const wantExact =
    exact || String(process.env.DERIVE_FFPROBE_COUNT_FRAMES || "").trim() === "1";
  const fast = frameCountFromStream(probeMp4StreamJson(filePath), defaultFps);
  if (fast > 0 && !wantExact) return fast;
  if (wantExact) {
    const exactCount = frameCountFromStream(
      probeMp4StreamJson(filePath, { countFrames: true }),
      defaultFps,
    );
    if (exactCount > 0) return exactCount;
  }
  return fast;
}

/** Build ffconcat for MP4 concat with explicit duration on first segment (batch-2 drift fix). */
export function buildMp4ConcatList(firstPath, secondPath, { firstDurationSec = null } = {}) {
  const esc = (p) => String(p).replace(/'/g, "'\\''");
  const dur =
    Number.isFinite(firstDurationSec) && firstDurationSec > 0
      ? firstDurationSec
      : probeMp4DurationSec(firstPath);
  const lines = ["ffconcat version 1.0", `file '${esc(firstPath)}'`];
  if (dur > 0) lines.push(`duration ${dur}`);
  lines.push(`file '${esc(secondPath)}'`);
  return lines.join("\n");
}

async function encodeFromConcatListLegacy(
  listPath,
  destPath,
  { withScale = true, fps = null, frameCount = null } = {},
) {
  const args = [
    "-y",
    "-hide_banner",
    "-loglevel",
    "error",
    "-f",
    "concat",
    "-safe",
    "0",
    "-i",
    listPath,
  ];
  if (withScale) {
    args.push("-vf", "scale='max(2,trunc(iw/2)*2)':'max(2,trunc(ih/2)*2)'");
  }
  args.push(...x264OutputArgs());
  if (Number.isFinite(fps) && fps > 0) {
    args.push("-r", String(fps));
  }
  if (Number.isFinite(frameCount) && frameCount > 0) {
    args.push("-frames:v", String(Math.floor(frameCount)));
  }
  args.push("-movflags", "+faststart", destPath);
  const res = await spawnFfmpeg(args);
  return { ...res, ok: res.ok && fs.existsSync(destPath) };
}

async function encodeFromConcatListFluent(
  listPath,
  destPath,
  { withScale = true, fps = null, frameCount = null } = {},
) {
  const ffmpeg = await loadFluent();
  return new Promise((resolve) => {
    let stderr = "";
    let cmd = ffmpeg(listPath).inputOptions(["-f", "concat", "-safe", "0"]);
    if (withScale) {
      cmd = cmd.videoFilters("scale='max(2,trunc(iw/2)*2)':'max(2,trunc(ih/2)*2)'");
    }
    const outputOpts = [...x264OutputArgs(), "-movflags", "+faststart"];
    if (Number.isFinite(fps) && fps > 0) {
      outputOpts.push("-r", String(fps));
    }
    if (Number.isFinite(frameCount) && frameCount > 0) {
      outputOpts.push("-frames:v", String(Math.floor(frameCount)));
    }
    cmd.on("stderr", (line) => {
      stderr += `${line}\n`;
    });
    cmd
      .outputOptions(outputOpts)
      .on("end", () =>
        resolve({
          ok: fs.existsSync(destPath),
          exitCode: 0,
          stderr: stderr.trim().slice(0, 2000),
          backend: "fluent",
        }),
      )
      .on("error", (err, _stdout, errMsg) =>
        resolve({
          ok: false,
          exitCode: typeof err?.exitCode === "number" ? err.exitCode : null,
          stderr: String(errMsg || err?.message || err).trim().slice(0, 2000),
          backend: "fluent",
        }),
      )
      .save(destPath);
  });
}

/** Encode staging JPG concat list to MP4. */
export async function encodeFromConcatList(listPath, destPath, options = {}) {
  if (deriveMuxBackend() === "fluent") {
    return encodeFromConcatListFluent(listPath, destPath, options);
  }
  return encodeFromConcatListLegacy(listPath, destPath, options);
}

async function concatMp4OnceLegacy(listPath, destPath, codecMode) {
  const outputArgs = codecMode === "copy" ? ["-c", "copy"] : x264OutputArgs();
  const res = await spawnFfmpeg([
    "-y",
    "-hide_banner",
    "-loglevel",
    "error",
    "-f",
    "concat",
    "-safe",
    "0",
    "-i",
    listPath,
    ...outputArgs,
    "-movflags",
    "+faststart",
    destPath,
  ]);
  return { ...res, ok: res.ok && fs.existsSync(destPath) };
}

async function concatMp4OnceFluent(listPath, destPath, codecMode) {
  const ffmpeg = await loadFluent();
  const outputOpts =
    codecMode === "copy"
      ? ["-c", "copy", "-movflags", "+faststart"]
      : [...x264OutputArgs(), "-movflags", "+faststart"];
  return new Promise((resolve) => {
    let stderr = "";
    const cmd = ffmpeg(listPath).inputOptions(["-f", "concat", "-safe", "0"]);
    cmd.on("stderr", (line) => {
      stderr += `${line}\n`;
    });
    cmd
      .outputOptions(outputOpts)
      .on("end", () =>
        resolve({
          ok: fs.existsSync(destPath),
          exitCode: 0,
          stderr: stderr.trim().slice(0, 2000),
          backend: "fluent",
        }),
      )
      .on("error", (err, _stdout, errMsg) =>
        resolve({
          ok: false,
          exitCode: typeof err?.exitCode === "number" ? err.exitCode : null,
          stderr: String(errMsg || err?.message || err).trim().slice(0, 2000),
          backend: "fluent",
        }),
      )
      .save(destPath);
  });
}

export async function concatMp4FromList(listPath, destPath, codecMode = "copy") {
  if (deriveMuxBackend() === "fluent") {
    return concatMp4OnceFluent(listPath, destPath, codecMode);
  }
  return concatMp4OnceLegacy(listPath, destPath, codecMode);
}

/** Remux annex-B H.264 elementary stream to MP4 (copy, no libx264). */
/** Drop leading decoded frames after remux (H.264 decode-warmup must not become training content). */
/** Default on: MP4 PTS from device_timestamps.json (scheme A). Set DERIVE_MUX_DEVICE_PTS=0 for legacy CFR. */
export function devicePtsMuxEnabled() {
  return String(process.env.DERIVE_MUX_DEVICE_PTS ?? "1").trim().toLowerCase() !== "0";
}

function nalHeaderOffset(nal) {
  if (nal.length >= 4 && nal[0] === 0 && nal[1] === 0 && nal[2] === 0 && nal[3] === 1) return 4;
  if (nal.length >= 3 && nal[0] === 0 && nal[1] === 0 && nal[2] === 1) return 3;
  return -1;
}

function nalType(nal) {
  const off = nalHeaderOffset(nal);
  if (off < 0 || off >= nal.length) return null;
  return nal[off] & 0x1f;
}

function iterAnnexBNals(buf) {
  if (!buf?.length) return [];
  let i = 0;
  const starts = [];
  while (i < buf.length - 3) {
    if (buf[i] === 0 && buf[i + 1] === 0 && buf[i + 2] === 1) {
      starts.push(i);
      i += 3;
      continue;
    }
    if (i < buf.length - 4 && buf[i] === 0 && buf[i + 1] === 0 && buf[i + 2] === 0 && buf[i + 3] === 1) {
      starts.push(i);
      i += 4;
      continue;
    }
    i += 1;
  }
  if (!starts.length) return [buf];
  const nals = [];
  for (let idx = 0; idx < starts.length; idx += 1) {
    const start = starts[idx];
    const end = idx + 1 < starts.length ? starts[idx + 1] : buf.length;
    nals.push(buf.subarray(start, end));
  }
  return nals;
}

function isVclNalType(type) {
  return type != null && type >= 1 && type <= 5;
}

/** Split annex-B elementary stream into access units (one coded picture each). */
export function splitAnnexBAccessUnits(buf) {
  const nals = iterAnnexBNals(buf);
  if (!nals.length) return [];
  const units = [];
  let current = [];
  let hasVcl = false;
  for (const nal of nals) {
    const type = nalType(nal);
    const vcl = isVclNalType(type);
    if (vcl && hasVcl) {
      units.push(Buffer.concat(current));
      current = [nal];
      hasVcl = true;
    } else {
      current.push(nal);
      if (vcl) hasVcl = true;
    }
  }
  if (current.length) units.push(Buffer.concat(current));
  return units;
}

/** Build ffmpeg setpts expression: frame n -> pts in seconds (relative to episode start). */
export function buildSetptsExprFromPtsSec(ptsSec) {
  if (!ptsSec?.length) return "0";
  if (ptsSec.length === 1) return String(ptsSec[0]);
  let expr = Number(ptsSec[ptsSec.length - 1]).toFixed(9);
  for (let i = ptsSec.length - 2; i >= 0; i -= 1) {
    expr = `if(eq(n\\,${i})\\,${Number(ptsSec[i]).toFixed(9)}\\,${expr})`;
  }
  return expr;
}

export function relativePtsSecFromTimestampsNs(timestampsNs) {
  const ts = (timestampsNs || []).map((v) => Number(v)).filter((v) => Number.isFinite(v) && v > 0);
  if (ts.length < 2 || ts[ts.length - 1] <= ts[0]) return null;
  const base = ts[0];
  return ts.map((v) => (v - base) / 1e9);
}

export function effectiveFpsFromTimestampsNs(timestampsNs) {
  const ts = (timestampsNs || []).map((v) => Number(v)).filter((v) => Number.isFinite(v) && v > 0);
  if (ts.length < 2 || ts[ts.length - 1] <= ts[0]) return null;
  const spanS = (ts[ts.length - 1] - ts[0]) / 1e9;
  if (spanS <= 0) return null;
  return (ts.length - 1) / spanS;
}

function alignAccessUnitsToTimestamps(accessUnits, timestampsNs, skipFrames = 0) {
  const skip = Math.max(0, Number(skipFrames || 0));
  let units = accessUnits.slice(skip);
  let ts = timestampsNs.slice(skip);
  const n = Math.min(units.length, ts.length);
  if (n < 2) return null;
  if (units.length !== ts.length) {
    units = units.slice(0, n);
    ts = ts.slice(0, n);
  }
  const ptsSec = relativePtsSecFromTimestampsNs(ts);
  if (!ptsSec || ptsSec.length !== n) return null;
  return { accessUnits: units, ptsSec, frameCount: n };
}

export async function trimMp4LeadingFrames(srcPath, destPath, { skipFrames = 0, fps = 30 } = {}) {
  const skip = Math.max(0, Number(skipFrames || 0));
  if (!srcPath || !fs.existsSync(srcPath)) {
    return { ok: false, error: "mp4_missing", backend: "legacy" };
  }
  if (skip <= 0) {
    if (path.resolve(srcPath) !== path.resolve(destPath)) {
      fs.copyFileSync(srcPath, destPath);
    }
    return { ok: true, skipFrames: 0, backend: "legacy" };
  }
  const tmpPath = `${destPath}.warmup-trim.tmp.mp4`;
  const args = [
    "-y",
    "-hide_banner",
    "-loglevel",
    "error",
    "-i",
    srcPath,
    "-vf",
    `select='gte(n\\,${skip})',setpts=N/(${fps}*TB)`,
    "-an",
    ...x264OutputArgs(),
    tmpPath,
  ];
  const res = await spawnFfmpeg(args);
  if (res.ok && fs.existsSync(tmpPath)) {
    fs.renameSync(tmpPath, destPath);
  }
  return { ...res, ok: res.ok && fs.existsSync(destPath), skipFrames: skip, backend: "legacy" };
}

export async function remuxH264AnnexBToMp4(h264Path, destPath, { fps = 30 } = {}) {
  if (!h264Path || !fs.existsSync(h264Path)) {
    return { ok: false, error: "h264_missing", backend: "legacy" };
  }
  const args = [
    "-y",
    "-hide_banner",
    "-loglevel",
    "error",
    "-fflags",
    "+genpts",
    "-f",
    "h264",
    "-r",
    String(fps),
    "-i",
    h264Path,
    "-c:v",
    "copy",
    "-movflags",
    "+faststart",
    destPath,
  ];
  const res = await spawnFfmpeg(args);
  return { ...res, ok: res.ok && fs.existsSync(destPath), mode: "remux_cfr" };
}

/**
 * Remux annex-B H.264 to VFR MP4 with per-frame PTS from device timestamps (scheme A).
 * Falls back to CFR remux when timestamps are missing or ffmpeg rejects the filter graph.
 */
export async function remuxH264AnnexBToMp4DevicePts(
  h264Path,
  destPath,
  { timestampsNs = [], skipFrames = 0, fpsFallback = 30 } = {},
) {
  if (!h264Path || !fs.existsSync(h264Path)) {
    return { ok: false, error: "h264_missing", backend: "legacy" };
  }
  if (!devicePtsMuxEnabled()) {
    return remuxH264AnnexBToMp4(h264Path, destPath, { fps: fpsFallback });
  }

  const tsInput = (timestampsNs || []).map((v) => Number(v)).filter((v) => Number.isFinite(v) && v > 0);
  if (tsInput.length < 2) {
    return remuxH264AnnexBToMp4(h264Path, destPath, { fps: fpsFallback });
  }

  const accessUnits = splitAnnexBAccessUnits(fs.readFileSync(h264Path));
  const aligned = alignAccessUnitsToTimestamps(accessUnits, tsInput, skipFrames);
  if (!aligned) {
    const eff = effectiveFpsFromTimestampsNs(tsInput) || fpsFallback;
    return remuxH264AnnexBToMp4(h264Path, destPath, { fps: eff });
  }

  const tmpH264 = `${destPath}.device-pts.tmp.h264`;
  const filterPath = `${destPath}.device-pts.filter.txt`;
  const tmpDest = `${destPath}.device-pts.tmp.mp4`;
  try {
    fs.writeFileSync(tmpH264, Buffer.concat(aligned.accessUnits));
    const setptsExpr = buildSetptsExprFromPtsSec(aligned.ptsSec);
    fs.writeFileSync(filterPath, `[0:v]setpts='${setptsExpr}'[vout]\n`, "utf8");

    const args = [
      "-y",
      "-hide_banner",
      "-loglevel",
      "error",
      "-f",
      "h264",
      "-i",
      tmpH264,
      "-filter_complex_script",
      filterPath,
      "-map",
      "[vout]",
      ...x264OutputArgs(),
      "-vsync",
      "0",
      "-movflags",
      "+faststart",
      tmpDest,
    ];
    const res = await spawnFfmpeg(args);
    if (!res.ok || !fs.existsSync(tmpDest)) {
      const eff = effectiveFpsFromTimestampsNs(tsInput) || fpsFallback;
      return remuxH264AnnexBToMp4(h264Path, destPath, { fps: eff });
    }
    fs.renameSync(tmpDest, destPath);
    const effectiveFps = effectiveFpsFromTimestampsNs(tsInput);
    return {
      ...res,
      ok: true,
      mode: "remux_device_pts",
      frameCount: aligned.frameCount,
      device_span_s: aligned.ptsSec[aligned.ptsSec.length - 1],
      effective_fps: effectiveFps,
      backend: "legacy",
    };
  } finally {
    for (const p of [tmpH264, filterPath, tmpDest]) {
      try {
        if (fs.existsSync(p)) fs.unlinkSync(p);
      } catch {
        /* ignore */
      }
    }
  }
}

/** Concat two MP4 files; fluent backend uses duration-aware ffconcat. */
export async function concatMp4Files(firstPath, secondPath, destPath, listPath) {
  const useDuration = deriveMuxBackend() === "fluent";
  const listContent = useDuration
    ? buildMp4ConcatList(firstPath, secondPath, {
        firstDurationSec: probeMp4DurationSec(firstPath),
      })
    : `file '${String(firstPath).replace(/'/g, "'\\''")}'\nfile '${String(secondPath).replace(/'/g, "'\\''")}'\n`;
  fs.writeFileSync(listPath, listContent);
  try {
    let res = await concatMp4FromList(listPath, destPath, "copy");
    if (res.ok) return res;
    res = await concatMp4FromList(listPath, destPath, "reencode");
    return res;
  } finally {
    try {
      fs.unlinkSync(listPath);
    } catch {
      /* ignore */
    }
  }
}
