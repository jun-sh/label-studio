/**
 * P2 derive unit: immutable per-session artifacts under derived/<session_id>/.
 */

import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

import { rawSegmentArchivePath, ensureDir, readJsonl, writeJsonlAtomic } from "./io.mjs";
import { DEFAULT_FPS, deriveLog } from "./station-context.mjs";
import { appendJournalEvent } from "./manifest.mjs";
import { encodeFramesToMp4 } from "./encode-pool.mjs";
import {
  UNIT_JSON_VERSION,
  UNIT_PIPELINE_VERSION,
  tmpUnitDir,
  unitDir,
} from "./unit-paths.mjs";
import { buildMainTableRows } from "./parquet-writer.mjs";
import { videoKeysForStation } from "../ingest/staging-materialize.mjs";
import { unpackFrameBin } from "../frame_bin_codec.mjs";
import { probeMp4FrameCount } from "../mux-exec.mjs";
import {
  cleanupExtractDir,
  extractTarZstSync,
  makeExtractDir,
  validateRawArchive,
} from "./tar-io.mjs";
import { readManifestFromArchive } from "./frame-map.mjs";
import { alignMainTableImu, syncMainParquet } from "./parquet-writer.mjs";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const IMU_INGEST_SCRIPT = path.join(__dirname, "imu", "ingest-raw.py");

export function buildUnitFrameMap(sessionId, segmentEntries) {
  const frameSegments = [];
  let cursor = 0;
  for (const seg of segmentEntries) {
    const frameCount = Math.max(0, Number(seg.frameCount || 0));
    if (frameCount <= 0) continue;
    frameSegments.push({
      session_id: sessionId,
      segment_id: seg.segmentId,
      local_frame_min: 0,
      local_frame_max: frameCount - 1,
      global_start: cursor,
      global_end: cursor + frameCount - 1,
      frame_count: frameCount,
    });
    cursor += frameCount;
  }
  return {
    episode_index: 0,
    length: cursor,
    frame_index_min: 0,
    frame_index_max: cursor > 0 ? cursor - 1 : -1,
    sessions: [{ session_id: sessionId, segment_count: segmentEntries.length }],
    frame_segments: frameSegments,
  };
}

export function sha256File(filePath) {
  const hash = createHash("sha256");
  hash.update(fs.readFileSync(filePath));
  return hash.digest("hex");
}

function resolvePython() {
  for (const bin of ["python3", "python"]) {
    const res = spawnSync(bin, ["--version"], { encoding: "utf8" });
    if (res.status === 0) return bin;
  }
  return "python3";
}

function unitFramesDir(tmpRoot, videoKey) {
  return path.join(tmpRoot, "frames", videoKey.replace(/\./g, "_"));
}

function materializeUnitFrames(tmpRoot, extractDir, stationId, globalStart = 0) {
  const rows = readJsonl(path.join(extractDir, "rows.jsonl"));
  const keys = videoKeysForStation(stationId);
  let written = 0;
  for (const row of rows) {
    const localIndex = Number(row.frame_index ?? row.frameIndex ?? -1);
    if (!Number.isInteger(localIndex) || localIndex < 0) continue;
    const binPath = path.join(extractDir, "frames", `${String(localIndex).padStart(8, "0")}.bin`);
    if (!fs.existsSync(binPath)) {
      throw new Error(`missing frame bin for index ${localIndex}`);
    }
    const cameraJpegs = unpackFrameBin(fs.readFileSync(binPath));
    const globalIndex = globalStart + localIndex;
    for (const videoKey of keys) {
      const jpeg = cameraJpegs[videoKey];
      if (!jpeg) continue;
      const outDir = unitFramesDir(tmpRoot, videoKey);
      ensureDir(outDir);
      const dest = path.join(outDir, `frame_${String(globalIndex).padStart(6, "0")}.jpg`);
      fs.writeFileSync(dest, jpeg);
      written += 1;
    }
  }
  return { written, frameCount: rows.length };
}

function readJson(p, fallback = null) {
  if (!fs.existsSync(p)) return fallback;
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return fallback;
  }
}

function ingestUnitImu(tmpRoot, extractDir, sessionId, segmentId, { replace = true } = {}) {
  const imuPath = path.join(extractDir, "imu_raw.jsonl");
  if (!fs.existsSync(imuPath)) {
    throw new Error(`imu_raw.jsonl missing for ${segmentId}`);
  }
  const py = resolvePython();
  const res = spawnSync(
    py,
    [
      IMU_INGEST_SCRIPT,
      tmpRoot,
      "--imu-jsonl",
      imuPath,
      "--segment-id",
      segmentId,
      "--session-id",
      sessionId,
      replace ? "--replace" : "--append",
    ],
    { encoding: "utf8" },
  );
  if (res.status !== 0) {
    throw new Error(String(res.stderr || res.stdout || "imu ingest failed").slice(0, 500));
  }
  const src = path.join(tmpRoot, "sensor_raw", "imu", "chunk-000", "file-000.parquet");
  const dest = path.join(tmpRoot, "imu.parquet");
  if (!fs.existsSync(src)) {
    throw new Error("imu parquet missing after ingest");
  }
  fs.copyFileSync(src, dest);
  return dest;
}

function writeUnitTable(tmpRoot, stationId, frameMap, segmentExtracts, stationRoot) {
  const rows = buildMainTableRows(frameMap, segmentExtracts).map((row) => ({
    ...row,
    episode_index: 0,
  }));
  const layoutRoot = path.join(tmpRoot, "_table");
  const jsonlPath = path.join(layoutRoot, "data", "chunk-000", "file-000.jsonl");
  writeJsonlAtomic(jsonlPath, rows);
  // align-main reads IMU from station_root/sensor_raw — use tmpRoot where ingest wrote it.
  alignMainTableImu(tmpRoot, jsonlPath);
  const infoSrc = path.join(stationRoot, "meta", "info.json");
  if (fs.existsSync(infoSrc)) {
    const infoDest = path.join(layoutRoot, "meta", "info.json");
    ensureDir(path.dirname(infoDest));
    fs.copyFileSync(infoSrc, infoDest);
  }
  syncMainParquet(layoutRoot, stationId);
  const parquetSrc = path.join(layoutRoot, "data", "chunk-000", "file-000.parquet");
  if (!fs.existsSync(parquetSrc)) {
    throw new Error(`unit parquet missing after sync (${parquetSrc})`);
  }
  const jsonlDest = path.join(tmpRoot, "data.jsonl");
  const parquetDest = path.join(tmpRoot, "data.parquet");
  fs.copyFileSync(jsonlPath, jsonlDest);
  fs.copyFileSync(parquetSrc, parquetDest);
  return { rows: rows.length, jsonlDest, parquetDest };
}

export function runUnitReadyGate(unitRoot, stationId, expectedFrames) {
  const checks = [];
  const jsonl = readJsonl(path.join(unitRoot, "data.jsonl"));
  const indices = jsonl.map((r) => Number(r.frame_index)).sort((a, b) => a - b);
  if (indices.length !== expectedFrames) {
    checks.push({ ok: false, checkId: "G1", reason: `rows ${indices.length} != ${expectedFrames}` });
  } else if (indices.some((v, i) => v !== i)) {
    checks.push({ ok: false, checkId: "G1", reason: "frame_index not continuous from 0" });
  } else {
    checks.push({ ok: true, checkId: "G1" });
  }
  for (const videoKey of videoKeysForStation(stationId)) {
    const mp4 = path.join(unitRoot, "videos", `${videoKey}.mp4`);
    const frames = fs.existsSync(mp4) ? probeMp4FrameCount(mp4, { defaultFps: DEFAULT_FPS }) : 0;
    if (frames < expectedFrames) {
      checks.push({ ok: false, checkId: "G2", reason: `${videoKey}: ${frames} < ${expectedFrames}` });
    }
  }
  if (!checks.some((c) => c.checkId === "G2" && !c.ok)) {
    checks.push({ ok: true, checkId: "G2" });
  }
  const imuOk = fs.existsSync(path.join(unitRoot, "imu.parquet"));
  checks.push(imuOk ? { ok: true, checkId: "G3" } : { ok: false, checkId: "G3", reason: "imu.parquet missing" });
  const failed = checks.find((c) => !c.ok);
  const passed = checks.filter((c) => c.ok).length;
  return {
    ok: !failed,
    checks,
    checksPassed: passed,
    checksTotal: 3,
    failedCheckId: failed?.checkId || null,
    reason: failed
      ? { code: failed.checkId === "G2" ? "MUX_FRAME_MISMATCH" : "PARQUET_INDEX_GAP", message: failed.reason, category: failed.checkId === "G2" ? "mux" : "derive" }
      : null,
  };
}

function collectSegmentArchives(root, sessionId) {
  const rawDir = path.join(root, "raw", "segments", sessionId);
  if (!fs.existsSync(rawDir)) return [];
  return fs
    .readdirSync(rawDir)
    .filter((n) => n.endsWith(".tar.zst"))
    .sort()
    .map((archive) => {
      const segmentId = archive.replace(/\.tar\.zst$/, "");
      const archivePath = rawSegmentArchivePath(root, sessionId, segmentId);
      const manifest = readManifestFromArchive(archivePath);
      return {
        segmentId,
        archivePath,
        frameCount: Number(manifest?.frame_count || manifest?.frameCount || 0),
        sha256: sha256File(archivePath),
      };
    });
}

function buildUnitJson(unitRoot, stationId, sessionId, sourceSegments, gate, deriveMeta) {
  const artifacts = {};
  const relFiles = [
    "data.parquet",
    "data.jsonl",
    "imu.parquet",
    ...videoKeysForStation(stationId).map((k) => `videos/${k}.mp4`),
  ];
  for (const rel of relFiles) {
    const full = path.join(unitRoot, rel);
    if (!fs.existsSync(full)) continue;
    const st = fs.statSync(full);
    const entry = { bytes: st.size, sha256: sha256File(full) };
    if (rel.endsWith(".parquet") || rel.endsWith(".jsonl")) {
      entry.rows = rel.endsWith(".jsonl") ? readJsonl(full).length : undefined;
    }
    if (rel.endsWith(".mp4")) {
      entry.frames = probeMp4FrameCount(full, { defaultFps: DEFAULT_FPS });
    }
    artifacts[rel] = entry;
  }
  return {
    version: UNIT_JSON_VERSION,
    session_id: sessionId,
    station_id: stationId,
    fps: DEFAULT_FPS,
    frames: deriveMeta.frames,
    source_segments: sourceSegments.map((s) => ({
      segment_id: s.segmentId,
      sha256: s.sha256,
      frame_count: s.frameCount,
    })),
    artifacts,
    gate: { version: 2, checks_passed: gate.checksPassed, checks_total: gate.checksTotal },
    derive: deriveMeta,
  };
}

export async function deriveUnit(stationId, root, sessionId, options = {}) {
  const attempt = Number(options.attempt || 1);
  const startedAt = new Date().toISOString();
  const attemptId = randomUUID();
  const tmpRoot = tmpUnitDir(root, attemptId);
  if (fs.existsSync(tmpRoot)) fs.rmSync(tmpRoot, { recursive: true, force: true });
  ensureDir(tmpRoot);
  ensureDir(path.join(tmpRoot, "videos"));

  appendJournalEvent(root, { event: "derive_started", session_id: sessionId, attempt });

  const segments = collectSegmentArchives(root, sessionId);
  if (!segments.length) {
    throw new Error(`no raw segments for ${sessionId}`);
  }

  const frameMap = buildUnitFrameMap(
    sessionId,
    segments.map((s) => ({ segmentId: s.segmentId, frameCount: s.frameCount })),
  );
  const segmentExtracts = [];
  const extractDirs = [];
  let globalStart = 0;
  let imuReplace = true;

  for (const seg of segments) {
    const validation = await validateRawArchive(seg.archivePath);
    if (!validation.ok) {
      throw new Error(`tar validation failed: ${(validation.issues || []).join("; ")}`);
    }
    const extractDir = makeExtractDir(root, sessionId, seg.segmentId);
    extractDirs.push(extractDir);
    try {
      extractTarZstSync(seg.archivePath, extractDir);
      materializeUnitFrames(tmpRoot, extractDir, stationId, globalStart);
      ingestUnitImu(tmpRoot, extractDir, sessionId, seg.segmentId, { replace: imuReplace });
      imuReplace = false;
      segmentExtracts.push({ sessionId, segmentId: seg.segmentId, extractDir });
      globalStart += seg.frameCount;
    } catch (err) {
      for (const dir of extractDirs) cleanupExtractDir(dir);
      throw err;
    }
  }

  let table;
  try {
    table = writeUnitTable(tmpRoot, stationId, frameMap, segmentExtracts, root);
  } finally {
    for (const dir of extractDirs) cleanupExtractDir(dir);
  }
  const videoKeys = videoKeysForStation(stationId);
  const muxResults = [];
  for (const videoKey of videoKeys) {
    const inDir = unitFramesDir(tmpRoot, videoKey);
    const indices = [];
    for (let i = 0; i < frameMap.length; i += 1) indices.push(i);
    const dest = path.join(tmpRoot, "videos", `${videoKey}.mp4`);
    const started = Date.now();
    // eslint-disable-next-line no-await-in-loop
    const enc = await encodeFramesToMp4(inDir, indices, dest, { workDir: `${dest}.chunks` });
    muxResults.push({ videoKey, ...enc, elapsedMs: Date.now() - started });
    if (!enc.ok) {
      throw new Error(`unit mux failed ${videoKey}: ${enc.error || "encode_failed"}`);
    }
  }

  try {
    fs.rmSync(path.join(tmpRoot, "frames"), { recursive: true, force: true });
  } catch {
    /* ignore */
  }
  try {
    fs.rmSync(path.join(tmpRoot, "_table"), { recursive: true, force: true });
  } catch {
    /* ignore */
  }
  try {
    fs.rmSync(path.join(tmpRoot, "sensor_raw"), { recursive: true, force: true });
  } catch {
    /* ignore */
  }

  const gate = runUnitReadyGate(tmpRoot, stationId, frameMap.length);
  const deriveMeta = {
    attempt,
    pipeline_version: UNIT_PIPELINE_VERSION,
    started_at: startedAt,
    elapsed_ms: Date.now() - new Date(startedAt).getTime(),
    encode: {
      preset: process.env.DERIVE_MUX_X264_PRESET || "veryfast",
      chunk_frames: Number(process.env.DERIVE_MUX_CHUNK_FRAMES || 256),
      parallel_jobs: muxResults[0]?.parallelJobs || 1,
    },
    frames: frameMap.length,
    rows: table.rows,
  };
  const unitJson = buildUnitJson(tmpRoot, stationId, sessionId, segments, gate, deriveMeta);
  fs.writeFileSync(path.join(tmpRoot, "unit.json"), `${JSON.stringify(unitJson, null, 2)}\n`);

  if (!gate.ok) {
    appendJournalEvent(root, {
      event: "derive_failed",
      session_id: sessionId,
      attempt,
      reason: gate.reason,
    });
    return { ok: false, gate, tmpRoot, unitJson: null };
  }

  const finalDir = unitDir(root, sessionId);
  if (fs.existsSync(finalDir)) fs.rmSync(finalDir, { recursive: true, force: true });
  fs.renameSync(tmpRoot, finalDir);
  appendJournalEvent(root, {
    event: "unit_ready",
    session_id: sessionId,
    frames: frameMap.length,
    unit_sha256: sha256File(path.join(finalDir, "unit.json")),
  });
  return { ok: true, gate, unitDir: finalDir, unitJson, muxResults, frames: frameMap.length };
}
