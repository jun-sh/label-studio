/**
 * P2 derive unit: immutable per-session artifacts under derived/<session_id>/.
 */

import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

import { rawSegmentArchivePath, rawMcapArchivePath, ensureDir, readJson, readJsonl, writeJsonlAtomic } from "./io.mjs";
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
import { frameBinName, readSegmentManifest, resolveFrameIndex, stagingFrameName } from "../ingest/frame-index.mjs";
import { unpackFrameBin } from "../frame_bin_codec.mjs";
import { probeMp4FrameCount, remuxH264AnnexBToMp4, trimMp4LeadingFrames } from "../mux-exec.mjs";
import {
  checkG2bMp4Decode,
  checkG2bMp4DecodeFast,
  checkG7BrowserPlayable,
  checkG7BrowserPlayableFast,
  commercialGateEnabled,
  mp4FrameProbeOptions,
  reconcileExceedsSlack,
  UNIT_GATE_CHECKS_COMMERCIAL,
  UNIT_GATE_CHECKS_LEGACY,
} from "./mp4-playback-gate.mjs";
import {
  fastMp4FrameProbeOptions,
  isMcapSingleFastCandidate,
} from "./mcap-single-fast.mjs";
import {
  cleanupExtractDir,
  extractTarZstSync,
  makeExtractDir,
  validateRawArchive,
} from "./tar-io.mjs";
import { readManifestFromArchive } from "./frame-map.mjs";
import { alignMainTableImu, syncMainParquet } from "./parquet-writer.mjs";
import {
  CAMERA_KEY_TO_VIDEO,
  materializeMcapExtract,
  materializeMcapUnitFrames,
  summarizeMcapArchive,
} from "./mcap-reader.mjs";
import { DERIVE_PROGRESS_PHASES, writeDeriveProgress } from "./progress.mjs";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const IMU_INGEST_SCRIPT = path.join(__dirname, "imu", "ingest-raw.py");

/** Concatenate per-segment annex-B H.264 elementary streams (multi-segment session merge). */
export function appendH264AnnexBStream(destPath, srcPath) {
  if (!srcPath || !fs.existsSync(srcPath)) return false;
  if (!fs.existsSync(destPath)) {
    fs.copyFileSync(srcPath, destPath);
    return true;
  }
  fs.appendFileSync(destPath, fs.readFileSync(srcPath));
  return true;
}

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

function materializeUnitFrames(tmpRoot, extractDir, stationId) {
  const manifest = readSegmentManifest(extractDir);
  const rows = readJsonl(path.join(extractDir, "rows.jsonl"));
  const keys = videoKeysForStation(stationId);
  let written = 0;
  for (const row of rows) {
    const frameIndex = resolveFrameIndex(row, manifest, 0);
    if (frameIndex < 0) continue;
    const binPath = path.join(extractDir, "frames", frameBinName(frameIndex));
    if (!fs.existsSync(binPath)) {
      throw new Error(`missing frame bin for index ${frameIndex}`);
    }
    const cameraJpegs = unpackFrameBin(fs.readFileSync(binPath));
    for (const videoKey of keys) {
      const jpeg = cameraJpegs[videoKey];
      if (!jpeg) continue;
      const outDir = unitFramesDir(tmpRoot, videoKey);
      ensureDir(outDir);
      const dest = path.join(outDir, stagingFrameName(frameIndex));
      fs.writeFileSync(dest, jpeg);
      written += 1;
    }
  }
  return { written, frameCount: rows.length };
}

function readSegmentIngestState(root, sessionId, segmentId) {
  return readJson(path.join(root, "state", "segments", sessionId, `${segmentId}.json`), null);
}

function ingestUnitImu(tmpRoot, extractDir, sessionId, segmentId, { replace = true, mcapArchivePath = null } = {}) {
  const imuPath = path.join(extractDir, "imu_raw.jsonl");
  const py = resolvePython();
  const args = [
    IMU_INGEST_SCRIPT,
    tmpRoot,
    "--segment-id",
    segmentId,
    "--session-id",
    sessionId,
    replace ? "--replace" : "--append",
  ];
  if (mcapArchivePath && !fs.existsSync(imuPath)) {
    args.push("--imu-mcap", mcapArchivePath);
  } else {
    if (!fs.existsSync(imuPath)) {
      throw new Error(`imu_raw.jsonl missing for ${segmentId}`);
    }
    args.push("--imu-jsonl", imuPath);
  }
  const res = spawnSync(py, args, { encoding: "utf8" });
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

/** After H.264 remux, ffprobe frame count can trail MCAP NAL count — trim table to min MP4. */
function reconcileUnitTableToRemuxedMp4(tmpRoot, stationId, stationRoot, probeOptions = {}) {
  const videoKeys = videoKeysForStation(stationId);
  const mp4Counts = videoKeys.map((videoKey) => {
    const mp4 = path.join(tmpRoot, "videos", `${videoKey}.mp4`);
    return fs.existsSync(mp4) ? probeMp4FrameCount(mp4, probeOptions) : 0;
  });
  const positive = mp4Counts.filter((n) => n > 0);
  if (!positive.length) return null;
  const minMp4 = Math.min(...positive);

  const jsonlPath = path.join(tmpRoot, "data.jsonl");
  const rows = readJsonl(jsonlPath);
  if (rows.length <= minMp4) return rows.length;

  const trimmed = rows.slice(0, minMp4).map((row, index) => ({
    ...row,
    frame_index: index,
  }));
  writeJsonlAtomic(jsonlPath, trimmed);

  const layoutRoot = path.join(tmpRoot, "_table_reconcile");
  const layoutJsonl = path.join(layoutRoot, "data", "chunk-000", "file-000.jsonl");
  writeJsonlAtomic(layoutJsonl, trimmed);
  const infoSrc = path.join(stationRoot, "meta", "info.json");
  if (fs.existsSync(infoSrc)) {
    ensureDir(path.join(layoutRoot, "meta"));
    fs.copyFileSync(infoSrc, path.join(layoutRoot, "meta", "info.json"));
  }
  syncMainParquet(layoutRoot, stationId);
  const parquetSrc = path.join(layoutRoot, "data", "chunk-000", "file-000.parquet");
  if (!fs.existsSync(parquetSrc)) {
    throw new Error(`reconcile parquet missing (${parquetSrc})`);
  }
  fs.copyFileSync(parquetSrc, path.join(tmpRoot, "data.parquet"));
  return minMp4;
}

export function runUnitReadyGate(unitRoot, stationId, expectedFrames, options = {}) {
  // Unit layout runs G1–G3 inline (G4–G6 apply at publish / legacy staging layout).
  // P1b commercial: G2b decode probe + G7 browser_playable (four-way parity + parquet=mp4).
  const remux = options.muxMode === "remux";
  const commercial = options.commercial ?? commercialGateEnabled();
  const fastPath = Boolean(options.fastPath);
  const probeOptions =
    options.probeOptions
    || (fastPath ? fastMp4FrameProbeOptions({ muxMode: options.muxMode }) : mp4FrameProbeOptions({ muxMode: options.muxMode }));
  const remuxSlack = remux ? Math.max(5, Math.ceil(expectedFrames * 0.1)) : 0;
  const minFrames = remux && !commercial ? Math.max(1, expectedFrames - remuxSlack) : expectedFrames;
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
    const frames = fs.existsSync(mp4) ? probeMp4FrameCount(mp4, probeOptions) : 0;
    if (frames < minFrames) {
      checks.push({
        ok: false,
        checkId: "G2",
        reason: `${videoKey}: ${frames} < ${minFrames}${remux && !commercial ? ` (remux tol ${remuxSlack})` : ""}`,
      });
    }
  }
  if (!checks.some((c) => c.checkId === "G2" && !c.ok)) {
    checks.push({ ok: true, checkId: "G2" });
  }
  const imuOk = fs.existsSync(path.join(unitRoot, "imu.parquet"));
  checks.push(imuOk ? { ok: true, checkId: "G3" } : { ok: false, checkId: "G3", reason: "imu.parquet missing" });

  if (commercial) {
    const g2b = fastPath
      ? checkG2bMp4DecodeFast(unitRoot, stationId)
      : checkG2bMp4Decode(unitRoot, stationId);
    checks.push(g2b.ok ? { ok: true, checkId: "G2b" } : { ok: false, checkId: "G2b", reason: g2b.reason?.message });
    const g7 = fastPath
      ? checkG7BrowserPlayableFast(unitRoot, stationId, expectedFrames, probeOptions)
      : checkG7BrowserPlayable(unitRoot, stationId, expectedFrames, probeOptions);
    checks.push(g7.ok ? { ok: true, checkId: "G7" } : { ok: false, checkId: "G7", reason: g7.reason?.message });
  }

  const failed = checks.find((c) => !c.ok);
  const passed = checks.filter((c) => c.ok).length;
  const checksTotal = commercial ? UNIT_GATE_CHECKS_COMMERCIAL : UNIT_GATE_CHECKS_LEGACY;
  const failedCheckId = failed?.checkId || null;
  let reason = null;
  if (failed) {
    const codeByCheck = {
      G2: "MUX_FRAME_MISMATCH",
      G2b: "MUX_DECODE_FAILED",
      G7: "BROWSER_NOT_PLAYABLE",
    };
    reason = {
      code: codeByCheck[failedCheckId] || "PARQUET_INDEX_GAP",
      message: failed.reason,
      category: ["G2", "G2b", "G7"].includes(failedCheckId) ? "mux" : "derive",
      checkId: failedCheckId,
    };
  }
  return {
    ok: !failed,
    checks,
    checksPassed: passed,
    checksTotal,
    commercial,
    failedCheckId,
    reason,
  };
}

function collectSegmentSources(root, sessionId) {
  const rawDir = path.join(root, "raw", "segments", sessionId);
  if (!fs.existsSync(rawDir)) return [];
  const segments = [];
  const archives = fs.readdirSync(rawDir).sort();
  const mcapSegmentIds = new Set(
    archives.filter((name) => name.endsWith(".mcap.zst")).map((name) => name.replace(/\.mcap\.zst$/, "")),
  );
  for (const archive of archives) {
    if (archive.endsWith(".mcap.zst")) {
      const segmentId = archive.replace(/\.mcap\.zst$/, "");
      const archivePath = rawMcapArchivePath(root, sessionId, segmentId);
      const state = readSegmentIngestState(root, sessionId, segmentId);
      let frameCount = Number(state?.frame_count || 0);
      if (frameCount <= 0) {
        const summary = summarizeMcapArchive(archivePath);
        frameCount = Number(summary?.frame_count || 0);
      }
      segments.push({
        segmentId,
        archivePath,
        frameCount,
        sha256: sha256File(archivePath),
        sourceFormat: state?.sourceFormat || "mcap",
      });
      continue;
    }
    if (archive.endsWith(".mcap")) {
      const segmentId = archive.replace(/\.mcap$/, "");
      const archivePath = path.join(rawDir, archive);
      const state = readSegmentIngestState(root, sessionId, segmentId);
      let frameCount = Number(state?.frame_count || 0);
      if (frameCount <= 0) {
        const summary = summarizeMcapArchive(archivePath);
        frameCount = Number(summary?.frame_count || 0);
      }
      segments.push({
        segmentId,
        archivePath,
        frameCount,
        sha256: sha256File(archivePath),
        sourceFormat: state?.sourceFormat || "mcap",
      });
      continue;
    }
    if (!archive.endsWith(".tar.zst")) continue;
    const segmentId = archive.replace(/\.tar\.zst$/, "");
    if (mcapSegmentIds.has(segmentId)) continue;
    const archivePath = rawSegmentArchivePath(root, sessionId, segmentId);
    const manifest = readManifestFromArchive(archivePath);
    const state = readSegmentIngestState(root, sessionId, segmentId);
    segments.push({
      segmentId,
      archivePath,
      frameCount: Number(manifest?.frame_count || manifest?.frameCount || state?.frame_count || 0),
      sha256: sha256File(archivePath),
      sourceFormat: state?.sourceFormat || "tarzst",
    });
  }
  return segments;
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
      const muxMode = String(deriveMeta.mux_mode || "").trim().toLowerCase();
      entry.frames = probeMp4FrameCount(full, mp4FrameProbeOptions({ muxMode }));
    }
    artifacts[rel] = entry;
  }
  return {
    version: UNIT_JSON_VERSION,
    session_id: sessionId,
    source_session_id: sessionId,
    station_id: stationId,
    video_codec: String(deriveMeta.video_codec || "jpeg").trim().toLowerCase(),
    pose_ready: false,
    fps: DEFAULT_FPS,
    frames: deriveMeta.frames,
    source_segments: sourceSegments.map((s) => ({
      segment_id: s.segmentId,
      sha256: s.sha256,
      frame_count: s.frameCount,
      source_format: s.sourceFormat || "tarzst",
    })),
    artifacts,
    gate: {
      version: 3,
      commercial: gate.commercial ?? commercialGateEnabled(),
      checks_passed: gate.checksPassed,
      checks_total: gate.checksTotal,
      browser_playable: gate.checks?.some((c) => c.checkId === "G7" && c.ok) ?? false,
    },
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

  const segments = collectSegmentSources(root, sessionId);
  if (!segments.length) {
    throw new Error(`no raw segments for ${sessionId}`);
  }
  const fastPath = isMcapSingleFastCandidate(segments);

  const segmentExtracts = [];
  const extractDirs = [];
  const materializedSegments = [];
  let imuReplace = true;
  let mcapVideoCodec = "jpeg";
  let h264TrimMeta = null;
  const h264StreamsDir = path.join(tmpRoot, "_h264_streams");

  for (const seg of segments) {
    const extractDir = makeExtractDir(root, sessionId, seg.segmentId);
    extractDirs.push(extractDir);
    let frameCount = Number(seg.frameCount || 0);
    try {
      if (seg.sourceFormat === "mcap") {
        const mat = materializeMcapExtract(seg.archivePath, extractDir);
        if (Number(mat.frame_count) > 0) {
          frameCount = Number(mat.frame_count);
        }
        if (mat.video_codec === "h264") {
          mcapVideoCodec = "h264";
          if (mat.h264_trim) {
            h264TrimMeta = mat.h264_trim;
          }
          ensureDir(h264StreamsDir);
          for (const camKey of Object.keys(CAMERA_KEY_TO_VIDEO)) {
            const src = path.join(extractDir, "streams", `${camKey}.h264`);
            const dest = path.join(h264StreamsDir, `${camKey}.h264`);
            appendH264AnnexBStream(dest, src);
          }
        } else {
          materializeMcapUnitFrames(tmpRoot, extractDir, stationId);
        }
        ingestUnitImu(tmpRoot, extractDir, sessionId, seg.segmentId, {
          replace: imuReplace,
          mcapArchivePath: seg.archivePath,
        });
      } else {
        const validation = await validateRawArchive(seg.archivePath);
        if (!validation.ok) {
          throw new Error(`tar validation failed: ${(validation.issues || []).join("; ")}`);
        }
        extractTarZstSync(seg.archivePath, extractDir);
        materializeUnitFrames(tmpRoot, extractDir, stationId);
        ingestUnitImu(tmpRoot, extractDir, sessionId, seg.segmentId, { replace: imuReplace });
      }
      imuReplace = false;
      segmentExtracts.push({ sessionId, segmentId: seg.segmentId, extractDir });
      materializedSegments.push({ segmentId: seg.segmentId, frameCount });
    } catch (err) {
      for (const dir of extractDirs) cleanupExtractDir(dir);
      throw err;
    }
  }

  const frameMap = buildUnitFrameMap(sessionId, materializedSegments);

  let table;
  try {
    table = writeUnitTable(tmpRoot, stationId, frameMap, segmentExtracts, root);
  } finally {
    for (const dir of extractDirs) cleanupExtractDir(dir);
  }
  const videoKeys = videoKeysForStation(stationId);
  const muxResults = [];
  if (mcapVideoCodec === "h264" && fastPath) {
    writeDeriveProgress(root, sessionId, {
      stationId,
      phase: DERIVE_PROGRESS_PHASES.MUX_REMUX,
      camera: "parallel",
      done: 0,
      total: videoKeys.length,
    });
    const parallel = await Promise.all(
      videoKeys.map(async (videoKey) => {
        const dest = path.join(tmpRoot, "videos", `${videoKey}.mp4`);
        const started = Date.now();
        const camKey = Object.entries(CAMERA_KEY_TO_VIDEO).find(([, vk]) => vk === videoKey)?.[0];
        const h264Path = path.join(h264StreamsDir, `${camKey}.h264`);
        const enc = await remuxH264AnnexBToMp4(h264Path, dest, { fps: DEFAULT_FPS });
        const skip = Number(h264TrimMeta?.decode_warmup_packets?.[camKey] || 0);
        if (enc.ok && skip > 0) {
          const trim = await trimMp4LeadingFrames(dest, dest, {
            skipFrames: skip,
            fps: DEFAULT_FPS,
          });
          if (!trim.ok) {
            return { videoKey, mode: "remux", ok: false, error: trim.error || "warmup_trim_failed" };
          }
        }
        return { videoKey, mode: "remux", ...enc, elapsedMs: Date.now() - started };
      }),
    );
    for (const enc of parallel) {
      if (!enc.ok) {
        throw new Error(`unit remux failed ${enc.videoKey}: ${enc.error || enc.stderr || "remux_failed"}`);
      }
      muxResults.push(enc);
    }
  } else {
    for (const videoKey of videoKeys) {
      const dest = path.join(tmpRoot, "videos", `${videoKey}.mp4`);
      const started = Date.now();
      if (mcapVideoCodec === "h264") {
        const camKey = Object.entries(CAMERA_KEY_TO_VIDEO).find(([, vk]) => vk === videoKey)?.[0];
        const h264Path = path.join(h264StreamsDir, `${camKey}.h264`);
        writeDeriveProgress(root, sessionId, {
          stationId,
          phase: DERIVE_PROGRESS_PHASES.MUX_REMUX,
          camera: videoKey,
          done: muxResults.length,
          total: videoKeys.length,
        });
        // eslint-disable-next-line no-await-in-loop
        const enc = await remuxH264AnnexBToMp4(h264Path, dest, { fps: DEFAULT_FPS });
        let muxOk = enc.ok;
        const skip = Number(h264TrimMeta?.decode_warmup_packets?.[camKey] || 0);
        if (muxOk && skip > 0) {
          const trim = await trimMp4LeadingFrames(dest, dest, {
            skipFrames: skip,
            fps: DEFAULT_FPS,
          });
          muxOk = trim.ok;
          if (!muxOk) {
            throw new Error(`unit warmup trim failed ${videoKey}: ${trim.error || "warmup_trim_failed"}`);
          }
        }
        muxResults.push({ videoKey, mode: "remux", ...enc, elapsedMs: Date.now() - started });
        if (!muxOk) {
          throw new Error(`unit remux failed ${videoKey}: ${enc.error || enc.stderr || "remux_failed"}`);
        }
        continue;
      }
      const inDir = unitFramesDir(tmpRoot, videoKey);
      const indices = [];
      for (let i = 0; i < frameMap.length; i += 1) indices.push(i);
      // eslint-disable-next-line no-await-in-loop
      const enc = await encodeFramesToMp4(inDir, indices, dest, { workDir: `${dest}.chunks` });
      muxResults.push({ videoKey, mode: "encode", ...enc, elapsedMs: Date.now() - started });
      if (!enc.ok) {
        throw new Error(`unit mux failed ${videoKey}: ${enc.error || "encode_failed"}`);
      }
    }
  }

  try {
    fs.rmSync(h264StreamsDir, { recursive: true, force: true });
  } catch {
    /* ignore */
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

  const muxMode = mcapVideoCodec === "h264" ? "remux" : "encode";
  const probeOptions = fastPath
    ? fastMp4FrameProbeOptions({ muxMode })
    : mp4FrameProbeOptions({ muxMode });
  let effectiveFrames = frameMap.length;
  let reconcileWarning = null;
  if (muxMode === "remux") {
    const remuxSlack = Math.max(5, Math.ceil(frameMap.length * 0.1));
    const reconciled = reconcileUnitTableToRemuxedMp4(tmpRoot, stationId, root, probeOptions);
    if (reconciled !== null) {
      effectiveFrames = reconciled;
      const trimmed = frameMap.length - reconciled;
      if (trimmed > 0) {
        reconcileWarning = {
          declared: frameMap.length,
          effective: reconciled,
          trimmed,
          slack: remuxSlack,
          level: trimmed > remuxSlack ? "warn" : "info",
        };
      }
    }
  }
  if (commercialGateEnabled() && reconcileExceedsSlack(reconcileWarning)) {
    reconcileWarning.level = "block";
    const reason = {
      code: "RECONCILE_EXCEEDED",
      message: `reconcile trimmed ${reconcileWarning.trimmed} > slack ${reconcileWarning.slack}`,
      category: "mux",
      checkId: "reconcile",
    };
    appendJournalEvent(root, {
      event: "derive_failed",
      session_id: sessionId,
      attempt,
      reason,
    });
    return { ok: false, gate: { ok: false, reason, checksPassed: 0, checksTotal: UNIT_GATE_CHECKS_COMMERCIAL, failedCheckId: "reconcile" }, tmpRoot, unitJson: null };
  }
  const gate = runUnitReadyGate(tmpRoot, stationId, effectiveFrames, {
    muxMode,
    probeOptions,
    fastPath,
  });
  const deriveMeta = {
    attempt,
    pipeline_version: UNIT_PIPELINE_VERSION,
    started_at: startedAt,
    elapsed_ms: Date.now() - new Date(startedAt).getTime(),
    video_codec: mcapVideoCodec,
    mux_mode: mcapVideoCodec === "h264" ? "remux" : "encode",
    mcap_single_fast: fastPath,
    encode: {
      preset: process.env.DERIVE_MUX_X264_PRESET || "veryfast",
      chunk_frames: Number(process.env.DERIVE_MUX_CHUNK_FRAMES || 256),
      parallel_jobs: muxResults[0]?.parallelJobs || 1,
    },
    frames: effectiveFrames,
    rows: effectiveFrames,
    frames_declared: frameMap.length,
    reconcile_warning: reconcileWarning,
    source_format: segments.some((s) => s.sourceFormat === "mcap") ? "mcap" : "tarzst",
  };
  const unitJson = buildUnitJson(tmpRoot, stationId, sessionId, segments, gate, deriveMeta);
  fs.writeFileSync(path.join(tmpRoot, "unit.json"), `${JSON.stringify(unitJson, null, 2)}\n`);

  if (!gate.ok) {
    const reason = gate.reason || {
      code: gate.failedCheckId || "DERIVE_GATE_FAILED",
      message: gate.checks?.find((c) => !c.ok)?.reason || "derive_gate_failed",
      category: gate.failedCheckId === "G2" ? "mux" : "derive",
      checkId: gate.failedCheckId,
    };
    appendJournalEvent(root, {
      event: "derive_failed",
      session_id: sessionId,
      attempt,
      reason,
    });
    return { ok: false, gate: { ...gate, reason }, tmpRoot, unitJson: null };
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
  return { ok: true, gate, unitDir: finalDir, unitJson, muxResults, frames: effectiveFrames };
}
