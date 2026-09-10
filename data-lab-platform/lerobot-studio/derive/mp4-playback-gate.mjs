/**
 * P1b commercial playback gates: decode probe (G2b) + browser_playable (G7).
 * G7 validates post-materialize MP4s (H.264 streams are trim-to-IDR in mcap-materialize.py).
 */

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

import { probeMp4FrameCount } from "../mux-exec.mjs";
import { DEFAULT_FPS } from "./station-context.mjs";
import { videoKeysForStation } from "../ingest/staging-materialize.mjs";
import { primaryVideoKeyForStation } from "./mcap-single-fast.mjs";

export const UNIT_GATE_CHECKS_COMMERCIAL = 5;
export const UNIT_GATE_CHECKS_LEGACY = 3;

function fail(checkId, code, message, category = "mux") {
  return {
    ok: false,
    checkId,
    reason: { code, message: String(message).slice(0, 500), category, checkId },
  };
}

function pass(checkId) {
  return { ok: true, checkId };
}

/** Commercial gate on by default (P1b). Set EGO_DERIVE_COMMERCIAL_GATE=0 to restore legacy warn-only. */
export function commercialGateEnabled() {
  const mode = String(process.env.EGO_DERIVE_COMMERCIAL_GATE ?? "1").trim().toLowerCase();
  return !["0", "false", "off", "legacy"].includes(mode);
}

export function remuxUsesExactFrameCount(muxMode = null) {
  if (muxMode === "remux") return true;
  return String(process.env.DERIVE_FFPROBE_COUNT_FRAMES || "").trim() === "1";
}

export function mp4FrameProbeOptions({ muxMode = null } = {}) {
  return {
    defaultFps: DEFAULT_FPS,
    exact: remuxUsesExactFrameCount(muxMode) || commercialGateEnabled(),
  };
}

/** ffmpeg decode probe: any stderr error → not browser-playable. */
export function probeMp4DecodeHealth(filePath) {
  if (!filePath || !fs.existsSync(filePath)) {
    return { ok: false, errors: ["missing_file"], stderr: "missing_file" };
  }
  const res = spawnSync(
    "ffmpeg",
    ["-v", "error", "-i", filePath, "-f", "null", "-"],
    { encoding: "utf8" },
  );
  const stderr = String(res.stderr || "").trim();
  const errors = stderr ? stderr.split("\n").filter(Boolean) : [];
  const ok = res.status === 0 && errors.length === 0;
  return { ok, errors, stderr: stderr.slice(0, 500) };
}

export function collectUnitMp4FrameCounts(unitRoot, stationId, probeOptions = {}) {
  const opts = { defaultFps: DEFAULT_FPS, exact: true, ...probeOptions };
  return videoKeysForStation(stationId).map((videoKey) => {
    const mp4 = path.join(unitRoot, "videos", `${videoKey}.mp4`);
    const frames = fs.existsSync(mp4) ? probeMp4FrameCount(mp4, opts) : 0;
    return { videoKey, mp4, frames };
  });
}

/** G2b: each camera MP4 must decode without ffmpeg errors. */
export function checkG2bMp4Decode(unitRoot, stationId) {
  const failures = [];
  for (const videoKey of videoKeysForStation(stationId)) {
    const mp4 = path.join(unitRoot, "videos", `${videoKey}.mp4`);
    const health = probeMp4DecodeHealth(mp4);
    if (!health.ok) {
      failures.push(`${videoKey}: ${health.errors[0] || health.stderr || "decode_failed"}`);
    }
  }
  if (failures.length) {
    return fail("G2b", "MUX_DECODE_FAILED", failures.join("; "), "mux");
  }
  return pass("G2b");
}

/** G2b fast: decode primary camera only (D1 single-segment MCAP). */
export function checkG2bMp4DecodeFast(unitRoot, stationId) {
  const videoKey = primaryVideoKeyForStation(stationId);
  const mp4 = path.join(unitRoot, "videos", `${videoKey}.mp4`);
  const health = probeMp4DecodeHealth(mp4);
  if (!health.ok) {
    return fail("G2b", "MUX_DECODE_FAILED", `${videoKey}: ${health.errors[0] || health.stderr || "decode_failed"}`, "mux");
  }
  return pass("G2b");
}

/**
 * G7 browser_playable: four-way frame parity + parquet rows == MP4 frames + decode clean.
 */
export function checkG7BrowserPlayable(unitRoot, stationId, expectedRows, probeOptions = {}) {
  const counts = collectUnitMp4FrameCounts(unitRoot, stationId, probeOptions);
  const frameValues = counts.map((c) => c.frames).filter((n) => n > 0);
  if (!frameValues.length) {
    return fail("G7", "BROWSER_NOT_PLAYABLE", "no mp4 frames probed", "mux");
  }
  const minFrames = Math.min(...frameValues);
  const maxFrames = Math.max(...frameValues);
  if (minFrames !== maxFrames) {
    return fail(
      "G7",
      "BROWSER_NOT_PLAYABLE",
      `camera frame mismatch min=${minFrames} max=${maxFrames}`,
      "mux",
    );
  }
  if (Number(expectedRows) !== minFrames) {
    return fail(
      "G7",
      "BROWSER_NOT_PLAYABLE",
      `parquet rows ${expectedRows} != mp4 frames ${minFrames}`,
      "mux",
    );
  }
  const decode = checkG2bMp4Decode(unitRoot, stationId);
  if (!decode.ok) {
    return fail("G7", "BROWSER_NOT_PLAYABLE", decode.reason?.message || "decode_failed", "mux");
  }
  return pass("G7");
}

/** G7 fast: trust ingest row count; probe primary MP4 decode only. */
export function checkG7BrowserPlayableFast(unitRoot, stationId, expectedRows, probeOptions = {}) {
  const videoKey = primaryVideoKeyForStation(stationId);
  const mp4 = path.join(unitRoot, "videos", `${videoKey}.mp4`);
  const opts = { defaultFps: DEFAULT_FPS, exact: false, ...probeOptions };
  const frames = fs.existsSync(mp4) ? probeMp4FrameCount(mp4, opts) : 0;
  if (frames <= 0) {
    return fail("G7", "BROWSER_NOT_PLAYABLE", `${videoKey}: no mp4 frames`, "mux");
  }
  if (Number(expectedRows) > 0 && Math.abs(Number(expectedRows) - frames) > 2) {
    return fail(
      "G7",
      "BROWSER_NOT_PLAYABLE",
      `parquet rows ${expectedRows} != primary mp4 frames ${frames} (fast tol 2)`,
      "mux",
    );
  }
  const decode = checkG2bMp4DecodeFast(unitRoot, stationId);
  if (!decode.ok) {
    return fail("G7", "BROWSER_NOT_PLAYABLE", decode.reason?.message || "decode_failed", "mux");
  }
  return pass("G7");
}

export function reconcileExceedsSlack(reconcileWarning) {
  if (!reconcileWarning) return false;
  const trimmed = Number(reconcileWarning.trimmed || 0);
  const slack = Number(reconcileWarning.slack || 0);
  return trimmed > slack;
}
