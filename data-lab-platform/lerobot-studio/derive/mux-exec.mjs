/**
 * Four-camera staging → MP4 mux + mux_validated.json (Phase0 §7.5, Phase4).
 * Low-level ffmpeg in ../mux-exec.mjs; business orchestration here.
 */

import fs from "node:fs";
import path from "node:path";

import { encodeFromConcatList, probeMp4FrameCount } from "../mux-exec.mjs";
import { videoKeysForStation } from "../ingest/staging-materialize.mjs";
import { ensureDir, writeJsonAtomic } from "./io.mjs";
import { DEFAULT_FPS } from "./station-context.mjs";

function stagingDir(root, videoKey) {
  return path.join(root, "_staging", videoKey.replace(/\./g, "_"));
}

function videoOutPath(root, videoKey) {
  return path.join(root, "videos", videoKey, "chunk-000", "file-000.mp4");
}

function escapeConcatPath(p) {
  return String(p).replace(/'/g, "'\\''");
}

function stagingFrameIndicesInRange(inDir, fromIdx, toIdx) {
  if (!fs.existsSync(inDir) || toIdx < fromIdx) return [];
  const indices = [];
  for (const f of fs.readdirSync(inDir)) {
    const m = /^frame_(\d+)\.jpg$/.exec(f);
    if (!m) continue;
    const n = Number.parseInt(m[1], 10);
    if (!Number.isFinite(n) || n < fromIdx || n > toIdx) continue;
    indices.push(n);
  }
  indices.sort((a, b) => a - b);
  return indices;
}

function buildStagingConcatList(inDir, fps, frameIndices) {
  if (!frameIndices.length) return null;
  const dt = 1 / fps;
  const lines = ["ffconcat version 1.0"];
  for (const idx of frameIndices) {
    const name = `frame_${String(idx).padStart(6, "0")}.jpg`;
    const full = path.join(inDir, name);
    if (!fs.existsSync(full)) continue;
    lines.push(`file '${escapeConcatPath(full)}'`);
    lines.push(`duration ${dt}`);
  }
  return lines.length > 1 ? lines.join("\n") : null;
}

async function muxOneCamera(root, videoKey, frameMap) {
  const inDir = stagingDir(root, videoKey);
  const outFile = videoOutPath(root, videoKey);
  const frameMin = frameMap.frame_index_min ?? 0;
  const frameMax = frameMap.frame_index_max ?? -1;
  const frameIndices = stagingFrameIndicesInRange(inDir, frameMin, frameMax);
  if (!frameIndices.length) {
    return { ok: true, skipped: true, videoKey, frames: 0 };
  }

  for (const p of [outFile, `${outFile}.concat.txt`]) {
    try {
      if (fs.existsSync(p)) fs.rmSync(p, { force: true });
    } catch {
      /* ignore */
    }
  }
  ensureDir(path.dirname(outFile));

  const listPath = `${outFile}.concat.txt`;
  const listContent = buildStagingConcatList(inDir, DEFAULT_FPS, frameIndices);
  if (!listContent) {
    return { ok: false, videoKey, frames: 0, error: "empty_concat_list" };
  }
  fs.writeFileSync(listPath, listContent);

  const encRes = await encodeFromConcatList(listPath, outFile, {
    withScale: true,
    fps: DEFAULT_FPS,
    frameCount: frameIndices.length,
  });
  try {
    fs.unlinkSync(listPath);
  } catch {
    /* ignore */
  }

  if (!encRes?.ok) {
    return { ok: false, videoKey, frames: 0, error: encRes?.stderr || "encode_failed" };
  }
  const verified = probeMp4FrameCount(outFile, { defaultFps: DEFAULT_FPS });
  return { ok: true, videoKey, frames: verified, stagingFrames: frameIndices.length };
}

export async function runFourCameraMux(stationId, root, frameMap) {
  const videoKeys = videoKeysForStation(stationId);
  const results = [];
  for (const videoKey of videoKeys) {
    // eslint-disable-next-line no-await-in-loop
    results.push(await muxOneCamera(root, videoKey, frameMap));
  }
  const allOk = results.every((r) => r.ok);
  return { allOk, results, videoKeys };
}

export function writeMuxValidatedSnapshot(root, sessionId, frameMap, stationId = null) {
  const expected = frameMap.length ?? 0;
  const frames = {};
  const keys = stationId ? videoKeysForStation(stationId) : [];
  for (const videoKey of keys) {
    const outFile = videoOutPath(root, videoKey);
    frames[videoKey] = fs.existsSync(outFile)
      ? probeMp4FrameCount(outFile, { defaultFps: DEFAULT_FPS })
      : 0;
  }

  const ok =
    expected > 0 &&
    Object.values(frames).length > 0 &&
    Object.values(frames).every((n) => Number(n) >= expected - 1 && Number(n) <= expected + 1);

  const payload = {
    ok,
    checked_at: new Date().toISOString(),
    session_id: sessionId,
    expected_frames: expected,
    frames,
    source: "frame_map",
  };
  writeJsonAtomic(path.join(root, "live", "derive", "mux_validated.json"), payload);
  return payload;
}

export async function runSessionMux(stationId, root, frameMap, sessionId) {
  const muxResult = await runFourCameraMux(stationId, root, frameMap);
  const validated = writeMuxValidatedSnapshot(root, sessionId, frameMap, stationId);
  return { ...muxResult, validated };
}
