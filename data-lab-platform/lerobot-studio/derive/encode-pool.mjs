/**
 * P2 chunked parallel MP4 encoding (process-level parallelism for 128-core hosts).
 */

import fs from "node:fs";
import path from "node:path";

import { concatMp4Files, encodeFromConcatList, probeMp4FrameCount } from "../mux-exec.mjs";
import { DEFAULT_FPS } from "./station-context.mjs";
import { ensureDir } from "./io.mjs";

function escapeConcatPath(p) {
  return String(p).replace(/'/g, "'\\''");
}

export function deriveChunkFrames() {
  return Math.max(32, Number(process.env.DERIVE_MUX_CHUNK_FRAMES || 256));
}

export function deriveCpuBudget() {
  const raw = Number(process.env.DERIVE_CPU_BUDGET ?? 0.6);
  if (!Number.isFinite(raw) || raw <= 0) return 0.6;
  return Math.min(1, raw);
}

export function deriveEncodeConcurrency() {
  const cores = Number(process.env.DERIVE_CPU_CORES || 128);
  const threadsPerJob = Math.max(1, Number(process.env.DERIVE_MUX_THREADS_PER_JOB || 2));
  const budget = deriveCpuBudget();
  return Math.max(1, Math.floor((cores * budget) / threadsPerJob));
}

export function chunkSortedIndices(indices, chunkSize = deriveChunkFrames()) {
  const sorted = [...indices].sort((a, b) => a - b);
  const chunks = [];
  for (let i = 0; i < sorted.length; i += chunkSize) {
    chunks.push(sorted.slice(i, i + chunkSize));
  }
  return chunks;
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

async function runWithConcurrency(tasks, limit) {
  const results = new Array(tasks.length);
  let cursor = 0;
  const workers = Array.from({ length: Math.min(limit, tasks.length) }, async () => {
    while (cursor < tasks.length) {
      const idx = cursor;
      cursor += 1;
      results[idx] = await tasks[idx]();
    }
  });
  await Promise.all(workers);
  return results;
}

async function encodeChunk(inDir, frameIndices, chunkPath, { fps = DEFAULT_FPS } = {}) {
  const listPath = `${chunkPath}.concat.txt`;
  const listContent = buildStagingConcatList(inDir, fps, frameIndices);
  if (!listContent) return { ok: false, error: "empty_chunk" };
  fs.writeFileSync(listPath, listContent);
  const encRes = await encodeFromConcatList(listPath, chunkPath, {
    withScale: true,
    fps,
    frameCount: frameIndices.length,
  });
  try {
    fs.unlinkSync(listPath);
  } catch {
    /* ignore */
  }
  return encRes;
}

/**
 * Encode sorted local frame indices from inDir into destPath using chunked parallel ffmpeg.
 */
export async function encodeFramesToMp4(inDir, frameIndices, destPath, options = {}) {
  const { fps = DEFAULT_FPS, workDir = null } = options;
  if (!frameIndices.length) {
    return { ok: false, error: "no_frames" };
  }
  const chunks = chunkSortedIndices(frameIndices);
  const tmpBase = workDir || `${destPath}.chunks`;
  ensureDir(tmpBase);
  const chunkPaths = chunks.map((_, i) => path.join(tmpBase, `chunk_${String(i).padStart(4, "0")}.mp4`));

  const tasks = chunks.map((indices, i) => async () => {
    const res = await encodeChunk(inDir, indices, chunkPaths[i], { fps });
    return { index: i, res, frames: indices.length };
  });
  const encoded = await runWithConcurrency(tasks, deriveEncodeConcurrency());
  const failed = encoded.find((e) => !e?.res?.ok);
  if (failed) {
    return { ok: false, error: failed.res?.stderr || failed.res?.error || "chunk_encode_failed", chunks: encoded };
  }

  if (chunkPaths.length === 1) {
    fs.renameSync(chunkPaths[0], destPath);
    try {
      fs.rmSync(tmpBase, { recursive: true, force: true });
    } catch {
      /* ignore */
    }
    return {
      ok: true,
      frames: probeMp4FrameCount(destPath, { defaultFps: fps }),
      chunks: 1,
      parallelJobs: 1,
    };
  }

  let merged = chunkPaths[0];
  for (let i = 1; i < chunkPaths.length; i += 1) {
    const nextMerged = path.join(tmpBase, `merged_${i}.mp4`);
    const listPath = `${nextMerged}.concat.txt`;
    const mergeRes = await concatMp4Files(merged, chunkPaths[i], nextMerged, listPath);
    if (!mergeRes?.ok) {
      return { ok: false, error: mergeRes?.stderr || "chunk_concat_failed", chunks: encoded };
    }
    if (merged !== chunkPaths[0]) {
      try {
        fs.unlinkSync(merged);
      } catch {
        /* ignore */
      }
    }
    merged = nextMerged;
  }
  ensureDir(path.dirname(destPath));
  fs.renameSync(merged, destPath);
  try {
    fs.rmSync(tmpBase, { recursive: true, force: true });
  } catch {
    /* ignore */
  }
  return {
    ok: true,
    frames: probeMp4FrameCount(destPath, { defaultFps: fps }),
    chunks: chunkPaths.length,
    parallelJobs: Math.min(chunks.length, deriveEncodeConcurrency()),
  };
}
