/**
 * Batch-2: ffmpeg/ffprobe execution layer (legacy spawn | fluent-ffmpeg).
 * Business logic (staging indices, mux state) stays in stream-ingest.mjs.
 */

import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";

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

function probeMp4StreamJson(filePath) {
  const res = spawnSync(
    "ffprobe",
    [
      "-v",
      "error",
      "-select_streams",
      "v:0",
      "-show_entries",
      "stream=nb_frames,nb_read_frames,duration,r_frame_rate",
      "-count_frames",
      "-of",
      "json",
      filePath,
    ],
    { encoding: "utf8" },
  );
  if (res.status !== 0) return null;
  try {
    return JSON.parse(res.stdout || "{}")?.streams?.[0] || null;
  } catch {
    return null;
  }
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

export function probeMp4FrameCount(filePath, { defaultFps = 30 } = {}) {
  if (!filePath || !fs.existsSync(filePath)) return 0;
  const stream = probeMp4StreamJson(filePath);
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

async function encodeFromConcatListLegacy(listPath, destPath, { withScale = true } = {}) {
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
  args.push(
    "-c:v",
    "libx264",
    "-pix_fmt",
    "yuv420p",
    "-movflags",
    "+faststart",
    destPath,
  );
  const res = await spawnFfmpeg(args);
  return { ...res, ok: res.ok && fs.existsSync(destPath) };
}

async function encodeFromConcatListFluent(listPath, destPath, { withScale = true } = {}) {
  const ffmpeg = await loadFluent();
  return new Promise((resolve) => {
    let stderr = "";
    let cmd = ffmpeg(listPath).inputOptions(["-f", "concat", "-safe", "0"]);
    if (withScale) {
      cmd = cmd.videoFilters("scale='max(2,trunc(iw/2)*2)':'max(2,trunc(ih/2)*2)'");
    }
    cmd.on("stderr", (line) => {
      stderr += `${line}\n`;
    });
    cmd
      .outputOptions(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"])
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
  const outputArgs = codecMode === "copy" ? ["-c", "copy"] : ["-c:v", "libx264", "-pix_fmt", "yuv420p"];
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
      : ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"];
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
