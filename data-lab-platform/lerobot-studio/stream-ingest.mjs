/**
 * Live LeRobot v3 stream ingest for collection stations (meta + data + videos only).
 */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { createWriteStream } from "node:fs";
import { fileURLToPath } from "node:url";
import { unpackFrameBin } from "./frame_bin_codec.mjs";
import { decompress as fzstdDecompress } from "./vendor/fzstd.mjs";
import {
  formatEpisodeDisplayTask,
  isLegacyPlaceholderTask,
  resolveTaskName,
  stationIdFromRoot,
} from "./task-naming.mjs";
import {
  concatMp4Files,
  encodeFromConcatList,
  isFullMuxMode,
  probeMp4FrameCount as execProbeMp4FrameCount,
} from "./mux-exec.mjs";
import { hasSessionMarker, SESSION_MARKERS, markSessionMcapFailed } from "./session-markers.mjs";
import {
  attachEpisodeMetaToIndexEntry,
  bootstrapDatasetSchema,
  buildCanonicalFrameRow,
  mergeEpisodeMeta,
  parseManifestToEpisodeMeta,
  prepareSegmentEpisodeMeta,
} from "./lerobot-converter.mjs";
import {
  archiveSegmentTarZst,
  assertSegmentMp4Archive,
  ingestSegmentMp4Shards,
  isSegmentMp4PrimaryPath,
  isStreamFramePushEnabled,
  segmentHasStreamMp4,
} from "./segment-mp4-ingest.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const STREAM_ROOT = process.env.STREAM_DATA_ROOT || "/srv/stream";

const VIDEO_KEYS_V0 = [
  "observation.images.camera_front_left",
  "observation.images.camera_front_right",
  "observation.images.camera_rear_left",
  "observation.images.camera_rear_right",
];

/** ego-standard fleet topology (see config/camera_topology_standard.yaml). */
const VIDEO_KEYS_V1 = [
  "observation.images.camera_front_left",
  "observation.images.camera_rear_right",
  "observation.images.camera_depth_left",
  "observation.images.camera_front_right",
];

const VIDEO_KEYS = VIDEO_KEYS_V0;

const LEGACY_VIDEO_KEYS = [
  "observation.images.camera_head_left",
  "observation.images.camera_head_right",
  "observation.images.camera_depth_head",
  "observation.images.camera_02",
];

/** Canonical (new) -> legacy multipart / on-disk names for transitional uploads. */
const LEGACY_VIDEO_KEY_ALIASES = {
  "observation.images.camera_front_left": ["observation.images.camera_head_left"],
  "observation.images.camera_front_right": ["observation.images.camera_head_right"],
  "observation.images.camera_rear_left": ["observation.images.camera_depth_head"],
  "observation.images.camera_depth_left": ["observation.images.camera_depth_head", "observation.images.camera_rear_left"],
  "observation.images.camera_rear_right": ["observation.images.camera_02"],
};

const STATION_TOPOLOGY_PATH = path.join(__dirname, "config", "station-topology.json");
let _stationTopologyCache = null;

function loadStationTopology() {
  try {
    const stat = fs.statSync(STATION_TOPOLOGY_PATH);
    if (
      _stationTopologyCache &&
      _stationTopologyCache.mtimeMs === stat.mtimeMs &&
      _stationTopologyCache.data
    ) {
      return _stationTopologyCache.data;
    }
    const data = readJson(STATION_TOPOLOGY_PATH, {});
    _stationTopologyCache = { mtimeMs: stat.mtimeMs, data };
    return data;
  } catch {
    _stationTopologyCache = { mtimeMs: 0, data: readJson(STATION_TOPOLOGY_PATH, {}) };
    return _stationTopologyCache.data;
  }
}

function topologyVideoKeys(topologyId) {
  const registry = loadStationTopology();
  const spec = registry[topologyId];
  if (spec?.video_keys?.length) return spec.video_keys;
  if (topologyId === "ego-standard") return VIDEO_KEYS_V1;
  return VIDEO_KEYS_V0;
}

function defaultVideoKeysForStation(stationId, { intrinsics = null } = {}) {
  const topoId = intrinsics?.topology?.topology_id;
  if (topoId) return topologyVideoKeys(topoId);
  const registry = loadStationTopology();
  const entry = registry.stations?.[stationId];
  if (entry?.topology_id) return topologyVideoKeys(entry.topology_id);
  return VIDEO_KEYS_V1;
}

function resolveVideoKeysForStation(stationId, { intrinsics = null, shapes = null } = {}) {
  const shapeKeys = Object.keys(shapes || {}).filter((k) => k.startsWith("observation.images."));
  if (shapeKeys.length >= 4) return shapeKeys;
  return defaultVideoKeysForStation(stationId, { intrinsics });
}

function infoVideoKeysMismatch(info, expectedKeys) {
  const feats = info?.features || {};
  const current = Object.keys(feats).filter((k) => k.startsWith("observation.images."));
  if (!expectedKeys?.length || !current.length) return Boolean(expectedKeys?.length);
  const expected = new Set(expectedKeys);
  return current.length !== expected.size || current.some((k) => !expected.has(k));
}

function syncInfoVideoFeatures(info, stationId, shapes, { intrinsics = null, episodeMeta = null } = {}) {
  const keys = resolveVideoKeysForStation(stationId, { intrinsics, shapes });
  const next = { ...(info || {}) };
  next.features = { ...(next.features || {}) };
  for (const key of Object.keys(next.features)) {
    if (key.startsWith("observation.images.")) delete next.features[key];
  }
  for (const key of keys) {
    const [h, w] = shapes?.[key] || next.features[key]?.shape?.slice(0, 2) || [800, 1280];
    next.features[key] = {
      dtype: "video",
      shape: [h, w, 3],
      names: ["height", "width", "channels"],
      info: {
        "video.height": h,
        "video.width": w,
        "video.codec": "h264",
        "video.pix_fmt": "yuv420p",
        "video.is_depth_map": false,
        "video.fps": DEFAULT_FPS,
        "video.channels": 3,
        has_audio: false,
      },
    };
  }
  return bootstrapDatasetSchema(next, episodeMeta);
}

function videoKeysFromIntrinsics(root) {
  const intr = readJson(path.join(root, "meta", "camera_intrinsics.json"), {});
  const topoId = intr?.topology?.topology_id;
  if (topoId) return topologyVideoKeys(topoId);
  return null;
}

const LEGACY_TO_CANONICAL = Object.fromEntries(
  Object.entries(LEGACY_VIDEO_KEY_ALIASES).map(([canonical, legacyList]) => [
    legacyList[0],
    canonical,
  ]),
);

/** New sessions use station topology; resumed sessions keep keys from existing meta/info.json. */
function ingestVideoKeys(root) {
  const info = readJson(path.join(root, "meta", "info.json"), {});
  const feats = info?.features || {};
  if (feats["observation.images.camera_head_left"]) {
    return LEGACY_VIDEO_KEYS;
  }
  if (feats["observation.images.camera_depth_left"]) {
    return VIDEO_KEYS_V1;
  }
  if (feats["observation.images.camera_rear_left"]) {
    return VIDEO_KEYS_V0;
  }
  const fromIntrinsics = videoKeysFromIntrinsics(root);
  if (fromIntrinsics) return fromIntrinsics;
  return defaultVideoKeysForStation(stationIdFromRoot(root));
}

const DEFAULT_FPS = Number(process.env.STREAM_MUX_FPS || 30);
/** Scheme A: background mux/parquet/viewer — never on ingest hot path. */
const STREAM_BACKGROUND_BATCH_MS = Number(process.env.STREAM_BACKGROUND_BATCH_MS || 2000);
const MUX_DEBOUNCE_MS = STREAM_BACKGROUND_BATCH_MS;
const PARQUET_DEBOUNCE_MS = STREAM_BACKGROUND_BATCH_MS;
const VIEWER_PUBLISH_DEBOUNCE_MS = STREAM_BACKGROUND_BATCH_MS;
const SEGMENT_INGEST_BATCH_SIZE = Number(process.env.STREAM_SEGMENT_INGEST_BATCH_SIZE || 2);
/** Heartbeat interval on edge is ~15s; TTL must survive slow ingest (segment upload). */
const HEARTBEAT_TTL_MS = Number(process.env.STREAM_HEARTBEAT_TTL_MS || 120_000);
const SYNC_SCRIPT = path.join(__dirname, "scripts", "sync-stream-parquet.py");
const APPEND_PARQUET_SCRIPT = path.join(__dirname, "scripts", "append-segment-parquet.py");

function resolveAppendParquetScript() {
  if (!fs.existsSync(APPEND_PARQUET_SCRIPT)) {
    throw new Error("append-segment-parquet.py missing (official LeRobot derive required)");
  }
  return APPEND_PARQUET_SCRIPT;
}
const EXTRACT_TAR_ZST_SCRIPT = path.join(__dirname, "scripts", "extract-tar-zst.py");
/** One LeRobot sidebar row per capture session (not per 300f segment). */
const SESSION_EPISODE_SEGMENT_ID = "__session__";

export function useSessionSingleEpisode(stationId) {
  if (String(process.env.STREAM_SESSION_SINGLE_EPISODE || "1").trim() === "0") {
    return false;
  }
  return true;
}

/** True when the station already has committed multi-session dataset rows on disk. */
function stationHasDerivedData(root) {
  const index = loadEpisodesIndex(root);
  if (
    (index.episodes || []).some(
      (ep) => ep.segment_id !== "legacy" && ep.session_id && Number(ep.length) > 0,
    )
  ) {
    return true;
  }
  if (countJsonlRows(root) > 0) return true;
  return fs.existsSync(path.join(root, "data", "chunk-000", "file-000.parquet"));
}

/** Next global dataset frame index when appending a new capture session. */
function nextGlobalDatasetFrameIndex(root) {
  const index = loadEpisodesIndex(root);
  let hi = 0;
  for (const ep of index.episodes || []) {
    if (ep.segment_id === "legacy") continue;
    hi = Math.max(hi, Number(ep.dataset_to_index) || 0);
  }
  const metrics = resolveDeriveFrameMetrics(root);
  if (metrics.rowCount > 0) {
    hi = Math.max(hi, metrics.rowCount, metrics.frameIndexMax + 1);
  }
  return hi;
}

/** Map session-local frame indices (0..N-1 per episode) to global LeRobot dataset indices. */
function sessionGlobalFrameOffset(root, sessionId) {
  const index = loadEpisodesIndex(root);
  const existing = (index.episodes || []).find(
    (ep) => ep.session_id === sessionId && ep.segment_id === SESSION_EPISODE_SEGMENT_ID,
  );
  if (existing) return Math.max(0, Number(existing.dataset_from_index) || 0);
  return nextGlobalDatasetFrameIndex(root);
}

function remapSegmentFramesForGlobalDataset(frames, offset) {
  const off = Math.max(0, Number(offset) || 0);
  if (!off || !Array.isArray(frames)) return frames;
  return frames.map((f) => {
    const local = Number(f.frameIndex ?? f.frame_index ?? 0);
    const global = local + off;
    return { ...f, frameIndex: global, frame_index: global };
  });
}

function remapSegmentImagesForGlobalDataset(images, offset) {
  const off = Math.max(0, Number(offset) || 0);
  if (!off || !images || typeof images !== "object") return images;
  const out = {};
  for (const [key, val] of Object.entries(images)) {
    const m = /^(\d+)__(.+)$/.exec(String(key));
    out[m ? `${Number(m[1]) + off}__${m[2]}` : key] = val;
  }
  return out;
}

function remapRowsJsonlForGlobalOffset(srcPath, destPath, offset) {
  const off = Math.max(0, Number(offset) || 0);
  if (!off) {
    fs.copyFileSync(srcPath, destPath);
    return;
  }
  const lines = fs.readFileSync(srcPath, "utf8").split("\n").filter(Boolean);
  const out = lines.map((line) => {
    const row = JSON.parse(line);
    const local = Number(row.frame_index ?? row.frameIndex ?? 0);
    row.frame_index = local + off;
    if (row.frameIndex !== undefined) row.frameIndex = row.frame_index;
    return JSON.stringify(row);
  });
  writeFileAtomic(destPath, `${out.join("\n")}\n`);
}

function episodeDisplayTask(root, stationId, sessionId, createdAt) {
  return resolveSessionTask(root, stationId, { sessionId, createdAt });
}
const DEFAULT_QUOTA_GB = Number(process.env.STREAM_QUOTA_GB || 100);
const RETENTION_DAYS = Number(process.env.STREAM_RETENTION_DAYS || 7);
const DISK_HIGH_WATER_RATIO = Number(process.env.STREAM_DISK_HIGH_WATER || 0.85);
const DISK_TARGET_RATIO = Number(process.env.STREAM_DISK_TARGET || 0.7);
const CLEANUP_DEBOUNCE_MS = Number(process.env.STREAM_CLEANUP_DEBOUNCE_MS || 60_000);
const CLEANUP_INTERVAL_MS = Number(process.env.STREAM_CLEANUP_INTERVAL_MS || 300_000);
const DISK_USAGE_CACHE_MAX_AGE_MS = Number(process.env.STREAM_DISK_USAGE_CACHE_MS || 120_000);
/** P1: rolling replay window / segment length (minutes). */
export const STREAM_WINDOW_MINUTES = Number(process.env.STREAM_WINDOW_MINUTES || 30);
export const STREAM_SEGMENT_MINUTES = Number(process.env.STREAM_SEGMENT_MINUTES || 5);
const STAGING_EMERGENCY_PURGE_BYTES = Number(
  process.env.STREAM_STAGING_EMERGENCY_BYTES || 512 * 1024 * 1024,
);
const STATION_TOKEN_HEADER = "x-station-token";
const stationTokensPath =
  process.env.COLLECTION_STATION_TOKENS_JSON ||
  path.join(__dirname, "config", "collection-station-tokens.json");

/** @type {Record<string, string>} */
let stationUploadTokens = {};
try {
  stationUploadTokens = JSON.parse(fs.readFileSync(stationTokensPath, "utf8"));
} catch {
  stationUploadTokens = {};
}

/** @type {Map<string, { timer: NodeJS.Timeout | null, running: boolean }>} */
const muxQueue = new Map();
/** @type {Map<string, { stagingFrames: number, metrics: object, fullDatasetMux: boolean }>} */
const muxRunPlan = new Map();
/** @type {Map<string, NodeJS.Timeout | null>} */
const muxRetryTimers = new Map();
/** Test hook: DERIVE_MUX_INJECT_FAIL_CAMERA + DERIVE_MUX_INJECT_FAIL_ONCE (default 1). */
const muxInjectFailedCameras = new Set();
/** Async derive: wait for one-shot session mux (no per-segment parquet rebuild). */
const muxOnceWaiters = new Map();
/** @type {Map<string, NodeJS.Timeout | null>} */
const parquetQueue = new Map();
/** @type {Map<string, NodeJS.Timeout | null>} */
const viewerPublishQueue = new Map();
/** @type {Map<string, NodeJS.Timeout | null>} */
const cleanupQueue = new Map();
/** @type {Map<string, { queue: object[], workerRunning: boolean }>} */
const segmentIngestQueues = new Map();
/** @type {Map<string, { timer: NodeJS.Timeout | null, pending: boolean }>} */
const backgroundBatchState = new Map();
/** Active browser-import depth per station (defers live frames + parquet). */
/** @type {Map<string, number>} */
const stationImportDepth = new Map();
/** Live frame uploads held during import — flushed when import depth returns to 0. */
/** @type {Map<string, object[]>} */
const deferredStreamFrames = new Map();
const ORPHAN_INCOMING_MAX_AGE_MS = Number(process.env.STREAM_ORPHAN_INCOMING_MAX_AGE_MS || 3_600_000);

function stationTokenEnvKey(stationId) {
  return `STREAM_STATION_TOKEN_${stationId.replace(/[^a-zA-Z0-9]/g, "_").toUpperCase()}`;
}

function getStationUploadToken(stationId) {
  const envKey = stationTokenEnvKey(stationId);
  const fromEnv = process.env[envKey] || process.env.STATION_UPLOAD_TOKEN;
  if (fromEnv) return fromEnv;
  const fromFile = stationUploadTokens[stationId];
  return typeof fromFile === "string" && fromFile.length > 0 ? fromFile : null;
}

function timingSafeTokenEqual(expected, provided) {
  const a = Buffer.from(String(expected));
  const b = Buffer.from(String(provided));
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

export function verifyStationUploadToken(stationId, req) {
  const expected = getStationUploadToken(stationId);
  if (!expected) return { ok: true };
  const provided = req.headers[STATION_TOKEN_HEADER];
  if (typeof provided !== "string" || !timingSafeTokenEqual(expected, provided)) {
    const reason = provided ? "invalid_token" : "missing_token";
    streamLog(stationId, "auth_reject", { reason });
    return { ok: false, reason };
  }
  return { ok: true };
}

export function streamLog(stationId, event, fields = {}) {
  const parts = ["[stream-ingest]", `station=${stationId}`];
  if (fields.sessionId) parts.push(`session=${fields.sessionId}`);
  if (fields.frameIndex !== undefined && fields.frameIndex !== null) {
    parts.push(`frame=${fields.frameIndex}`);
  }
  parts.push(`event=${event}`);
  for (const [key, value] of Object.entries(fields)) {
    if (key === "sessionId" || key === "frameIndex") continue;
    if (value === undefined || value === null) continue;
    parts.push(`${key}=${value}`);
  }
  console.log(parts.join(" "));
}

export function streamDatasetUrl(stationId) {
  return `stream://${stationId}`;
}

export function streamHttpDatasetUrl(stationId) {
  return `/lerobot/api/stream/${encodeURIComponent(stationId)}/`;
}

export function stationRoot(stationId) {
  return path.join(STREAM_ROOT, stationId);
}

export function beginStationImport(stationId) {
  stationImportDepth.set(stationId, (stationImportDepth.get(stationId) || 0) + 1);
}

export async function endStationImport(stationId) {
  const next = (stationImportDepth.get(stationId) || 1) - 1;
  if (next <= 0) {
    stationImportDepth.delete(stationId);
    await flushDeferredStreamFrames(stationId);
    scheduleBackgroundTasks(stationId);
  } else {
    stationImportDepth.set(stationId, next);
  }
}

function isStationImportActive(stationId) {
  return (stationImportDepth.get(stationId) || 0) > 0;
}

export function isStationSegmentCommitted(stationId, sessionId, segmentId) {
  return isSegmentCommitted(stationRoot(stationId), sessionId, segmentId);
}

/** READY gate: segment .done marker + optional expected frame count. */
export function validateSegmentDerived(stationId, sessionId, segmentId, expectedFrames = 0) {
  const root = stationRoot(stationId);
  if (!isSegmentCommitted(root, sessionId, segmentId)) {
    return { ok: false, reason: "not_committed", framesCommitted: 0 };
  }
  const scratch = segmentScratchJsonlPath(root, sessionId, segmentId);
  if (fs.existsSync(scratch)) {
    return { ok: false, reason: "scratch_pending", framesCommitted: 0 };
  }
  let framesCommitted = 0;
  const metaPath = segmentMetaPath(root, sessionId, segmentId);
  if (fs.existsSync(metaPath)) {
    try {
      const meta = JSON.parse(fs.readFileSync(metaPath, "utf8"));
      framesCommitted = Number(meta.framesCommitted ?? meta.frame_count ?? 0);
    } catch {
      /* ignore */
    }
  }
  if (framesCommitted <= 0) {
    const doneJson = segmentCommittedMarker(root, sessionId, segmentId).replace(
      ".done",
      ".json",
    );
    if (fs.existsSync(doneJson)) {
      try {
        const row = JSON.parse(fs.readFileSync(doneJson, "utf8"));
        framesCommitted = Number(row.framesCommitted ?? 0);
      } catch {
        /* ignore */
      }
    }
  }
  const exp = Number(expectedFrames) || 0;
  if (exp > 0 && framesCommitted > 0 && framesCommitted < exp) {
    return { ok: false, reason: "frame_count_low", framesCommitted, expectedFrames: exp };
  }
  return {
    ok: true,
    framesCommitted: framesCommitted || exp,
    expectedFrames: exp,
  };
}

export function countRawTarZstForSession(root, sessionId) {
  if (!sessionId) return 0;
  const dir = path.join(root, "raw", "segments", sessionId);
  if (!fs.existsSync(dir)) return 0;
  return fs.readdirSync(dir).filter((f) => f.endsWith(".tar.zst")).length;
}

function segmentDeriveMarkerPath(root, sessionId, segmentId) {
  return path.join(root, "live", "derive", "markers", sessionId, `${segmentId}.ok.json`);
}

export function isSegmentParquetDerivedOnDisk(stationId, sessionId, segmentId) {
  const root = stationRoot(stationId);
  const marker = segmentDeriveMarkerPath(root, sessionId, segmentId);
  if (!fs.existsSync(marker)) return false;
  return isSegmentCommitted(root, sessionId, segmentId);
}

export function countDeriveMarkersForSession(root, sessionId) {
  const dir = path.join(root, "live", "derive", "markers", sessionId);
  if (!fs.existsSync(dir)) return 0;
  return fs.readdirSync(dir).filter((f) => f.endsWith(".ok.json")).length;
}

export function listRawSessionIds(root) {
  const rawRoot = path.join(root, "raw", "segments");
  if (!fs.existsSync(rawRoot)) return [];
  return fs
    .readdirSync(rawRoot)
    .filter((d) => !d.startsWith(".") && fs.statSync(path.join(rawRoot, d)).isDirectory());
}

function countCommittedSegmentsForSession(root, sessionId) {
  if (!sessionId) return 0;
  const dir = path.join(root, "live", "sessions", sessionId, "segments");
  if (!fs.existsSync(dir)) return 0;
  return fs.readdirSync(dir).filter((f) => f.endsWith(".done")).length;
}

function countStationCommittedSegments(root) {
  const base = path.join(root, "live", "sessions");
  if (!fs.existsSync(base)) return 0;
  let total = 0;
  for (const ent of fs.readdirSync(base, { withFileTypes: true })) {
    if (!ent.isDirectory()) continue;
    total += countCommittedSegmentsForSession(root, ent.name);
  }
  return total;
}

/** Async derive uses raw tar.zst markers; linear ingest uses jsonl↔parquet row parity. */
export function isParquetReadyOnDisk(root, { rawTotal = 0, markers = 0, parquetRows = 0 } = {}) {
  if (parquetRows <= 0) return false;
  if (rawTotal > 0) {
    return markers >= rawTotal;
  }
  const jsonlRows = readJsonlFrameMetrics(root).count;
  if (jsonlRows > 0) {
    return parquetRows >= jsonlRows - 1 && parquetRows <= jsonlRows + 1;
  }
  return parquetRows > 0;
}

function muxRetryStatePath(root) {
  return path.join(root, "live", "derive", "mux_retry_state.json");
}

function muxLastFailurePath(root) {
  return path.join(root, "live", "derive", "mux_last_failure.json");
}

function shouldMuxAutoRetry() {
  const v = String(process.env.DERIVE_MUX_AUTO_RETRY ?? "1").trim().toLowerCase();
  return v !== "0" && v !== "false" && v !== "no";
}

function muxMaxRetries() {
  return Math.max(0, Number(process.env.DERIVE_MUX_MAX_RETRIES || 3));
}

function muxRetryBaseMs() {
  return Math.max(1000, Number(process.env.DERIVE_MUX_RETRY_BASE_MS || 10_000));
}

function readMuxRetryAttempt(root) {
  return Number(readJson(muxRetryStatePath(root), {}).attempt || 0);
}

function clearMuxRetryState(root) {
  try {
    fs.rmSync(muxRetryStatePath(root), { force: true });
  } catch {
    /* ignore */
  }
  try {
    fs.rmSync(muxLastFailurePath(root), { force: true });
  } catch {
    /* ignore */
  }
}

function maybeScheduleMuxRetry(stationId, sessionId) {
  const root = stationRoot(stationId);
  if (!shouldMuxAutoRetry()) return false;
  if (stagingJpegCount(root) <= 0 && !hasUnreleasedStaging(root)) return false;
  const attempt = readMuxRetryAttempt(root);
  if (attempt >= muxMaxRetries()) {
    writeJsonAtomic(muxLastFailurePath(root), {
      sessionId,
      attempt,
      message: "mux_validation_failed",
      at: new Date().toISOString(),
    });
    streamLog(stationId, "mux_retry_exhausted", {
      sessionId,
      attempt,
      maxRetries: muxMaxRetries(),
    });
    return false;
  }
  const delayMs = muxRetryBaseMs() * 2 ** attempt;
  writeJsonAtomic(muxRetryStatePath(root), {
    sessionId,
    attempt: attempt + 1,
    nextRetryAt: new Date(Date.now() + delayMs).toISOString(),
    lastError: "mux_validation_failed",
  });
  const prev = muxRetryTimers.get(stationId);
  if (prev) clearTimeout(prev);
  const timer = setTimeout(() => {
    muxRetryTimers.delete(stationId);
    scheduleMux(stationId);
    streamLog(stationId, "mux_retry_scheduled", {
      sessionId,
      attempt: attempt + 1,
      delayMs,
      mode: "linear",
    });
  }, delayMs);
  muxRetryTimers.set(stationId, timer);
  return true;
}

/** Station-wide derive status (all sessions / episodes on disk). */
export function computeStationDeriveStatusFromDisk(stationId) {
  const root = stationRoot(stationId);
  const sessionIds = listRawSessionIds(root);
  const rawTotal = sessionIds.reduce((n, sid) => n + countRawTarZstForSession(root, sid), 0);
  const markers = sessionIds.reduce((n, sid) => n + countDeriveMarkersForSession(root, sid), 0);
  const metrics = resolveDeriveFrameMetrics(root);
  const parquetRows = metrics.rowCount;
  const expectedMp4 = metrics.expectedMp4Frames;
  const muxVal = readJson(path.join(root, "live", "derive", "mux_validated.json"), {});
  let mp4Ok = evaluateMp4Readiness(root, metrics).ok;
  if (!mp4Ok && muxVal.ok) {
    mp4Ok = true;
  }
  const parquetReady = isParquetReadyOnDisk(root, { rawTotal, markers, parquetRows });
  const fullyReady = parquetReady && mp4Ok;
  const committedTotal = countStationCommittedSegments(root);
  const uploadTotal = rawTotal > 0 ? rawTotal : committedTotal;
  let phase = "IDLE";
  if (fullyReady) phase = "READY";
  else if (uploadTotal > 0 || countJsonlRows(root) > 0) phase = "UPLOADED";
  return {
    stationId,
    sessionId: null,
    phase,
    total: uploadTotal,
    markers,
    parquetRows,
    frameIndexMin: metrics.frameIndexMin,
    frameIndexMax: metrics.frameIndexMax,
    expectedMp4Frames: expectedMp4,
    parquetReady,
    mp4Ok,
    fullyReady,
    sessionCount: sessionIds.length,
  };
}

function writeSegmentDeriveMarker(root, sessionId, segmentId, meta) {
  writeJsonAtomic(segmentDeriveMarkerPath(root, sessionId, segmentId), {
    sessionId,
    segmentId,
    ...meta,
    markedAt: new Date().toISOString(),
  });
}

function pendingSessionEpisodePath(root, sessionId) {
  return path.join(root, "live", "derive", "pending_episode", `${sessionId}.json`);
}

export function hasPendingSessionEpisode(root, sessionId) {
  return fs.existsSync(pendingSessionEpisodePath(root, sessionId));
}

export function spawnAppendSegmentParquetSync(root, extractDir, { deferSave = false } = {}) {
  const script = resolveAppendParquetScript();
  const py = resolveParquetPython();
  if (!py) throw new Error("no_python_for_parquet");
  const args = [script, root, extractDir];
  if (deferSave) args.push("--defer-save");
  const res = spawnSync(py, args, {
    ...parquetSpawnOptions(),
    encoding: "utf8",
  });
  if (res.status !== 0) {
    throw new Error(String(res.stderr || res.stdout || "append parquet failed").slice(0, 500));
  }
  try {
    return JSON.parse(String(res.stdout || "{}").trim() || "{}");
  } catch {
    return { ok: true };
  }
}

/** Rebuild data/chunk jsonl from raw tar.zst rows (segment_mp4 path; no frames/*.bin). */
export async function rebuildSessionJsonlFromRawIfEmpty(stationId, sessionId) {
  const root = stationRoot(stationId);
  const metrics = readJsonlFrameMetrics(root);
  if (metrics.count > 0) return metrics.count;
  if (!isSegmentMp4PrimaryPath()) return 0;

  const rawDir = path.join(root, "raw", "segments", sessionId);
  if (!fs.existsSync(rawDir)) return 0;
  const archives = fs
    .readdirSync(rawDir)
    .filter((f) => f.endsWith(".tar.zst"))
    .sort();
  if (!archives.length) return 0;

  streamLog(stationId, "derive_jsonl_rebuild_start", { sessionId, segments: archives.length });
  let info = readJson(path.join(root, "meta", "info.json"));
  if (!info) info = defaultInfo(stationId, {});
  const jsonl = dataJsonlPath(root);
  ensureDir(path.dirname(jsonl));
  let rowsWritten = 0;

  for (const archive of archives) {
    const segmentId = archive.replace(/\.tar\.zst$/, "");
    const archivePath = path.join(rawDir, archive);
    const extractDir = path.join(
      root,
      ".upload",
      "extract",
      `jsonl_rebuild_${segmentId}_${Date.now()}_${crypto.randomBytes(3).toString("hex")}`,
    );
    try {
      await extractTarZstArchive(archivePath, extractDir);
      const rowsPath = path.join(extractDir, "rows.jsonl");
      if (!fs.existsSync(rowsPath)) continue;
      const lines = fs
        .readFileSync(rowsPath, "utf8")
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
      if (!lines.length) continue;
      withFileLock(root, "jsonl", () => {
        for (const line of lines) {
          const row = JSON.parse(line);
          const frameIndex = Number(row.frame_index ?? row.frameIndex ?? -1);
          if (!Number.isInteger(frameIndex) || frameIndex < 0) continue;
          const canonical = buildCanonicalFrameRow(
            {
              frame_index: frameIndex,
              timestamp_ns: row.timestamp_ns ?? row.timestampNs ?? 0,
              task: frameTaskValue(root, stationId, row.task),
              "observation.state": row["observation.state"] || row.observationState || [0, 0, 0, 0, 0, 0],
              "observation.pose": row["observation.pose"] || row.observationPose || [0, 0, 0, 0, 0, 0, 1],
              "observation.hands": row["observation.hands"] || row.observationHands || new Array(63).fill(0),
              action: row.action || row.actionVector || [0],
            },
            info,
          );
          fs.appendFileSync(jsonl, `${JSON.stringify(canonical)}\n`);
          rowsWritten += 1;
        }
      });
    } finally {
      try {
        fs.rmSync(extractDir, { recursive: true, force: true });
      } catch {
        /* ignore */
      }
    }
  }

  const total = readJsonlFrameMetrics(root).count;
  streamLog(stationId, "derive_jsonl_rebuild_done", { sessionId, rowsWritten, total });
  return total;
}

/** Scalar parquet + meta from committed jsonl (segment_mp4; videos already at ingest). */
export function spawnParquetSyncFromJsonlSync(root) {
  if (!fs.existsSync(SYNC_SCRIPT)) throw new Error("parquet sync script missing");
  const py = resolveParquetPython();
  if (!py) throw new Error("no_python_for_parquet");
  markParquetArtifactsWriting(root);
  const res = spawnSync(py, [SYNC_SCRIPT, root], parquetSpawnOptions());
  if (res.status !== 0) {
    throw new Error(String(res.stderr || res.stdout || "parquet sync from jsonl failed").slice(0, 500));
  }
}

async function processSegmentMp4DeriveFromExtract(
  root,
  stationId,
  body,
  { actualSha, ingestSource, t0 },
) {
  const { sessionId, segmentId } = body;
  if (isSegmentParquetDerivedOnDisk(stationId, sessionId, segmentId)) {
    return { body, skipped: true, duplicate: true };
  }

  ensureSessionForImport(stationId, body);
  let info = readJson(path.join(root, "meta", "info.json"));
  if (!info) info = defaultInfo(stationId, {});

  let rowsMerged = 0;
  for (const f of body.frames || []) {
    const frameIndex = Number(f.frameIndex ?? f.frame_index ?? -1);
    if (!Number.isInteger(frameIndex) || frameIndex < 0) continue;
    if (isFrameCommitted(root, sessionId, frameIndex)) continue;
    const row = buildCanonicalFrameRow(
      {
        frame_index: frameIndex,
        timestamp_ns: f.timestampNs ?? f.timestamp_ns ?? 0,
        task: frameTaskValue(root, stationId, f.task),
        "observation.state": f.observationState || f["observation.state"] || [0, 0, 0, 0, 0, 0],
        "observation.pose": f.observationPose || f["observation.pose"] || [0, 0, 0, 0, 0, 0, 1],
        "observation.hands": f.observationHands || f.observationHands || new Array(63).fill(0),
        action: f.actionVector || f.action || [0],
      },
      info,
    );
    appendSegmentScratchRow(root, sessionId, segmentId, row);
    markFrameCommitted(root, sessionId, frameIndex);
    rowsMerged += 1;
  }
  if (rowsMerged > 0) {
    mergeSegmentScratchToJsonl(root, sessionId, segmentId);
  }

  const framesCommitted = Number(body.frames?.length ?? 0);
  if (!isSegmentCommitted(root, sessionId, segmentId)) {
    markSegmentCommitted(root, sessionId, segmentId);
  }
  writeSegmentDeriveMarker(root, sessionId, segmentId, {
    sha256: actualSha,
    framesCommitted,
    backend: "segment_mp4_jsonl",
    segmentMp4: true,
    rowsMerged,
  });

  streamLog(stationId, "derive_segment_mp4_ok", {
    sessionId,
    segmentId,
    elapsedMs: Date.now() - t0,
    frames: framesCommitted,
    rowsMerged,
  });
  return { body, skipped: false, segmentMp4: true, framesCommitted, rowsMerged };
}

/** Flush deferred session episode buffer to LeRobot parquet (one save_episode per session). */
export function spawnFinalizeSessionEpisodeSync(root, sessionId) {
  const script = resolveAppendParquetScript();
  const py = resolveParquetPython();
  if (!py) throw new Error("no_python_for_parquet");
  const res = spawnSync(py, [script, root, "--finalize-session", sessionId], {
    ...parquetSpawnOptions(),
    encoding: "utf8",
  });
  if (res.status !== 0) {
    throw new Error(String(res.stderr || res.stdout || "finalize session episode failed").slice(0, 500));
  }
  try {
    return JSON.parse(String(res.stdout || "{}").trim() || "{}");
  } catch {
    return { ok: true };
  }
}

const EXPORT_MCAP_SCRIPT = path.join(__dirname, "scripts", "export_segment_to_mcap.py");

export function mcapExportEnabled() {
  return String(process.env.DERIVE_MCAP_EXPORT || "0").trim() === "1";
}

export function mcapValidateEnabled() {
  const v = process.env.DERIVE_MCAP_VALIDATE;
  if (v === undefined || v === null || String(v).trim() === "") return true;
  const s = String(v).trim().toLowerCase();
  return s !== "0" && s !== "false" && s !== "no";
}

export function spawnExportSegmentMcapSync(root, extractDir, sessionId, segmentId) {
  const py = resolveParquetPython();
  if (!py) throw new Error("no_python_for_mcap");
  const outDir = path.join(root, "raw", "mcap", sessionId);
  fs.mkdirSync(outDir, { recursive: true });
  const outPath = path.join(outDir, `${segmentId}.mcap`);
  const intrinsicsPath = path.join(root, "meta", "camera_intrinsics.json");
  const args = [EXPORT_MCAP_SCRIPT, extractDir, "-o", outPath];
  if (fs.existsSync(intrinsicsPath)) {
    args.push("--intrinsics", intrinsicsPath);
  }
  if (!mcapValidateEnabled()) {
    args.push("--no-validate-imu");
  }
  const res = spawnSync(py, args, {
    encoding: "utf8",
    env: {
      ...process.env,
      PYTHONPATH: path.join(__dirname, "scripts"),
    },
  });
  if (res.status !== 0) {
    throw new Error(String(res.stderr || res.stdout || "mcap export failed").slice(0, 500));
  }
  try {
    return JSON.parse(String(res.stdout || "{}").trim() || "{}");
  } catch {
    return { ok: true, out_path: outPath };
  }
}

const EXPORT_VIDEOS_SCRIPT = path.join(__dirname, "scripts", "export-videos-from-parquet.py");

/** Official LeRobot only — videos encoded by LeRobotDataset.save_episode(). */
export function deriveVideoExportBackend() {
  return "lerobot";
}

export function usesParquetVideoExport() {
  const backend = deriveVideoExportBackend();
  return backend !== "staging" && backend !== "incremental";
}

export function exportVideosFromParquetSync(stationId) {
  const root = stationRoot(stationId);
  if (!fs.existsSync(EXPORT_VIDEOS_SCRIPT)) {
    throw new Error("export-videos-from-parquet.py missing");
  }
  const py = resolveParquetPython();
  if (!py) throw new Error("no_python_for_video_export");
  const backend = deriveVideoExportBackend();
  streamLog(stationId, "video_export_start", { backend, root });
  const res = spawnSync(py, [EXPORT_VIDEOS_SCRIPT, root], {
    ...parquetSpawnOptions(),
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    maxBuffer: 4 * 1024 * 1024,
    timeout: 3_600_000,
  });
  let payload = {};
  try {
    payload = JSON.parse(String(res.stdout || "{}").trim() || "{}");
  } catch {
    payload = { ok: false, raw: String(res.stdout || "").slice(0, 500) };
  }
  if (res.status !== 0 || payload.ok === false) {
    const err = String(payload.error || res.stderr || res.stdout || "video export failed").slice(0, 500);
    streamLog(stationId, "video_export_fail", { backend, message: err });
    throw new Error(err);
  }
  streamLog(stationId, "video_export_done", { backend, frames: payload.frames });
  return payload;
}

/** Scan committed jsonl for row count and global frame_index bounds (authoritative for derive). */
function readJsonlFrameMetrics(root) {
  const jsonl = path.join(root, "data", "chunk-000", "file-000.jsonl");
  if (!fs.existsSync(jsonl)) {
    return { count: 0, min: 0, max: -1, span: 0 };
  }
  let count = 0;
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  try {
    const raw = fs.readFileSync(jsonl, "utf8");
    for (const line of raw.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const row = JSON.parse(trimmed);
      const frame = Number(row.frame_index ?? row.frameIndex ?? -1);
      if (!Number.isInteger(frame) || frame < 0) continue;
      count += 1;
      min = Math.min(min, frame);
      max = Math.max(max, frame);
    }
  } catch {
    return { count: 0, min: 0, max: -1, span: 0 };
  }
  if (count <= 0) return { count: 0, min: 0, max: -1, span: 0 };
  return { count, min, max, span: max - min + 1 };
}

/** Sum row counts across all official LeRobot data parquet shards (file-000, file-001, …). */
function readLeRobotDataParquetRows(root) {
  const py = resolveParquetPython();
  if (!py) return 0;
  const res = spawnSync(
    py,
    [
      "-c",
      "import sys, pyarrow.parquet as pq; from pathlib import Path; r=Path(sys.argv[1]); print(sum(pq.read_metadata(p).num_rows for p in sorted(r.glob('data/**/*.parquet'))))",
      root,
    ],
    { ...parquetSpawnOptions(), encoding: "utf8" },
  );
  if (res.status !== 0) return 0;
  const n = Number(String(res.stdout || "").trim());
  return Number.isFinite(n) && n > 0 ? n : 0;
}

/**
 * Derive metrics for READY gate. Jsonl is source of truth; MP4 covers [min..max] global indices.
 * Incremental parquet append can drift from jsonl when frame_index is non-zero-based.
 */
export function resolveDeriveFrameMetrics(root) {
  const info = readJson(path.join(root, "meta", "info.json"), {});
  const marker = readJson(path.join(root, "live", "parquet_sync.json"), {});
  const parquetPath = path.join(root, "data", "chunk-000", "file-000.parquet");
  const jsonlPath = path.join(root, "data", "chunk-000", "file-000.jsonl");
  const jsonlRows = fs.existsSync(jsonlPath) ? readJsonlFrameMetrics(root).count : 0;
  const lerobotOwned =
    marker.lerobot_data_owned === true &&
    jsonlRows === 0 &&
    (fs.existsSync(parquetPath) && !fs.existsSync(jsonlPath));
  if (lerobotOwned) {
    const rowCount =
      readLeRobotDataParquetRows(root) ||
      Number(info.ingest_row_count || info.total_frames || 0);
    if (rowCount > 0) {
      const frameMin = Number(info.frame_index_min ?? 0);
      const frameMax = Number(info.frame_index_max ?? Math.max(frameMin, rowCount - 1));
      return {
        rowCount,
        frameIndexMin: frameMin,
        frameIndexMax: frameMax,
        expectedMp4Frames: rowCount,
        source: "lerobot",
      };
    }
  }
  const jsonl = readJsonlFrameMetrics(root);
  if (jsonl.count > 0) {
    return {
      rowCount: jsonl.count,
      frameIndexMin: jsonl.min,
      frameIndexMax: jsonl.max,
      expectedMp4Frames: jsonl.span,
      source: "jsonl",
    };
  }
  const rowCount = Number(info.ingest_row_count || info.total_frames || 0);
  const frameMin = Number(info.frame_index_min ?? 0);
  const frameMax = Number(info.frame_index_max ?? Math.max(frameMin, rowCount - 1));
  const span = rowCount > 0 ? Math.max(rowCount, frameMax - frameMin + 1) : 0;
  return {
    rowCount,
    frameIndexMin: frameMin,
    frameIndexMax: frameMax,
    expectedMp4Frames: span,
    source: "info",
  };
}

/** LeRobot owns data parquet — refresh meta/episodes only (no jsonl rebuild). */
export function syncDataParquetFromJsonl(stationId) {
  const root = stationRoot(stationId);
  syncInfoFramesFromDataset(root);
  syncEpisodesMetaOnly(stationId);
  return true;
}

function listVideoMp4Shards(root, videoKey) {
  const base = path.join(root, "videos", videoKey);
  if (!fs.existsSync(base)) return [];
  const shards = [];
  const stack = [base];
  while (stack.length) {
    const dir = stack.pop();
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) stack.push(full);
      else if (ent.isFile() && /^file-\d+\.mp4$/.test(ent.name)) shards.push(full);
    }
  }
  return shards.sort();
}

/** Sum frames across all LeRobot per-episode MP4 shards (file-000, file-001, …). */
function probeVideoKeyTotalMp4Frames(root, videoKey) {
  return listVideoMp4Shards(root, videoKey).reduce(
    (sum, abs) => sum + execProbeMp4FrameCount(abs, { defaultFps: DEFAULT_FPS }),
    0,
  );
}

export async function writeMuxValidatedSnapshot(stationId, sessionId) {
  const root = stationRoot(stationId);
  const metrics = resolveDeriveFrameMetrics(root);
  const { ok, frames, targetFrames, minFrames } = evaluateMp4Readiness(root, metrics, {
    forceProbe: true,
  });
  writeJsonAtomic(path.join(root, "live", "derive", "mux_validated.json"), {
    sessionId,
    expectedFrames: targetFrames,
    expectedRowCount: metrics.rowCount,
    frameIndexMin: metrics.frameIndexMin,
    frameIndexMax: metrics.frameIndexMax,
    frames,
    minFrames,
    ok,
    validatedAt: new Date().toISOString(),
  });
  return { ok, expected: targetFrames, frames, metrics };
}

/** Disk-only derive status (UPLOADED vs READY). No memory flags. */
export function computeDeriveStatusFromDisk(stationId, sessionId) {
  const root = stationRoot(stationId);
  const rawTotal = sessionId ? countRawTarZstForSession(root, sessionId) : 0;
  const markers = sessionId ? countDeriveMarkersForSession(root, sessionId) : 0;
  const metrics = resolveDeriveFrameMetrics(root);
  const parquetRows = metrics.rowCount;
  const muxVal = readJson(path.join(root, "live", "derive", "mux_validated.json"), {});
  const expectedMp4 = metrics.expectedMp4Frames;
  let mp4Ok = evaluateMp4Readiness(root, metrics).ok;
  if (!mp4Ok && muxVal.ok && muxVal.sessionId === sessionId) {
    mp4Ok = true;
  }
  const parquetReady = isParquetReadyOnDisk(root, { rawTotal, markers, parquetRows });
  const fullyReady = parquetReady && mp4Ok;
  const committed = sessionId ? countCommittedSegmentsForSession(root, sessionId) : 0;
  const uploadTotal = rawTotal > 0 ? rawTotal : committed;
  let phase = "IDLE";
  if (fullyReady) phase = "READY";
  else if (uploadTotal > 0) phase = "UPLOADED";
  return {
    stationId,
    sessionId,
    phase,
    total: uploadTotal,
    markers,
    parquetRows,
    frameIndexMin: metrics.frameIndexMin,
    frameIndexMax: metrics.frameIndexMax,
    expectedMp4Frames: expectedMp4,
    parquetReady,
    mp4Ok,
    fullyReady,
  };
}

export function runSessionMuxOnceSync(stationId) {
  return new Promise((resolve, reject) => {
    if (muxOnceWaiters.has(stationId)) {
      reject(new Error("mux_once_already_running"));
      return;
    }
    muxOnceWaiters.set(stationId, { resolve, reject });
    const state = muxQueue.get(stationId) || { timer: null, running: false };
    if (state.timer) clearTimeout(state.timer);
    state.timer = null;
    muxQueue.set(stationId, state);
    runMux(stationId);
  });
}

/** True while session mux or mux-once derive is in flight (for derive sub-status). */
export function isMuxPipelineActive(stationId) {
  const state = muxQueue.get(stationId);
  return Boolean(state?.running) || muxOnceWaiters.has(stationId);
}

/**
 * Linear derive segment: jsonl/staging commit + incremental parquet from rows.jsonl.
 */
export async function processTarZstDeriveSegment(archivePath, stationId, options = {}) {
  const {
    expectedSha,
    sessionId: expectSessionId,
    segmentId: expectSegmentId,
    ingestSource = "edge",
  } = options;
  const root = stationRoot(stationId);
  const extractDir = path.join(
    root,
    ".upload",
    "extract",
    `derive_${Date.now()}_${crypto.randomBytes(4).toString("hex")}`,
  );
  const t0 = Date.now();
  let body = null;
  try {
    const actualSha = await sha256File(archivePath);
    if (expectedSha && actualSha !== String(expectedSha).toLowerCase()) {
      throw new Error(`sha256 mismatch expected=${String(expectedSha).slice(0, 12)} actual=${actualSha.slice(0, 12)}`);
    }
    await extractTarZstArchive(archivePath, extractDir, { expectedSha256: actualSha });
    const rowsPath = path.join(extractDir, "rows.jsonl");
    if (!fs.existsSync(rowsPath)) throw new Error("rows.jsonl missing in tar.zst");
    body = buildSegmentBodyFromExtractedDir(extractDir, stationId);
    if (expectSessionId && body.sessionId !== expectSessionId) {
      throw new Error("manifest session mismatch");
    }
    if (expectSegmentId && body.segmentId !== expectSegmentId) {
      throw new Error("manifest segment mismatch");
    }
    if (isSegmentParquetDerivedOnDisk(stationId, body.sessionId, body.segmentId)) {
      return { body, skipped: true, duplicate: true };
    }
    const framesDir = path.join(extractDir, "frames");
    const hasFrameBins =
      fs.existsSync(framesDir) &&
      fs.readdirSync(framesDir).some((f) => f.endsWith(".bin"));
    if (body.segmentMp4 && !hasFrameBins) {
      return processSegmentMp4DeriveFromExtract(root, stationId, body, {
        actualSha,
        ingestSource,
        t0,
      });
    }
    ensureSessionForImport(stationId, body);
    body.ingestSource = ingestSource;
    if (isSegmentCommitted(root, body.sessionId, body.segmentId)) {
      const precheck = validateSegmentDerived(
        stationId,
        body.sessionId,
        body.segmentId,
        body.frames?.length ?? 0,
      );
      if (!precheck.ok && precheck.reason !== "not_committed") {
        resetSegmentCommitState(
          root,
          body.sessionId,
          body.segmentId,
          (body.frames || [])
            .map((f) => Number(f.frameIndex ?? f.frame_index))
            .filter((n) => Number.isInteger(n) && n >= 0),
        );
      }
    }
    const deferEpisodeSave = useSessionSingleEpisode(stationId);
    const append = spawnAppendSegmentParquetSync(root, extractDir, {
      deferSave: deferEpisodeSave,
    });
    const framesCommitted = Number(
      append.frames_committed || append.rows_added || body.frames?.length || 0,
    );
    const fromIdx = Number(append.dataset_from_index ?? 0);
    const toIdx = Number(append.dataset_to_index ?? fromIdx + framesCommitted);
    registerSessionEpisode(root, {
      sessionId: body.sessionId,
      segmentId: body.segmentId,
      fromFrame: fromIdx,
      toFrame: toIdx,
      source: ingestSource || "edge",
      task: append.task || null,
    });
    markSegmentCommitted(root, body.sessionId, body.segmentId);
    writeJsonAtomic(segmentMetaPath(root, body.sessionId, body.segmentId), {
      sessionId: body.sessionId,
      segmentId: body.segmentId,
      fromFrame: fromIdx,
      toFrame: toIdx,
      framesCommitted,
      episodeIndex: append.episode_index,
      committedAt: new Date().toISOString(),
    });
    writeSegmentDeriveMarker(root, body.sessionId, body.segmentId, {
      sha256: actualSha,
      framesCommitted,
      parquetRows: Number(append.total_rows || append.parquet_rows || 0),
      backend: "lerobot",
      deferSave: Boolean(append.defer_save),
      episodeSaved: append.episode_saved !== false,
    });
    if (mcapExportEnabled()) {
      try {
        const mcapReport = spawnExportSegmentMcapSync(
          root,
          extractDir,
          body.sessionId,
          body.segmentId,
        );
        streamLog(stationId, "mcap_export_ok", {
          sessionId: body.sessionId,
          segmentId: body.segmentId,
          outPath: mcapReport.out_path || null,
          degradedImu: Boolean(mcapReport.degraded_imu),
        });
      } catch (mcapErr) {
        const message = String(mcapErr?.message || mcapErr).slice(0, 300);
        streamLog(stationId, "mcap_export_fail", {
          sessionId: body.sessionId,
          segmentId: body.segmentId,
          message,
        });
        markSessionMcapFailed(root, body.sessionId, message, {
          segmentId: body.segmentId,
        });
      }
    }
    syncEpisodesMetaOnly(stationId);
    streamLog(stationId, "derive_segment_ok", {
      sessionId: body.sessionId,
      segmentId: body.segmentId,
      elapsedMs: Date.now() - t0,
      frames: framesCommitted,
      parquetRows: append.total_rows,
      episodeIndex: append.episode_index,
    });
    return { body, skipped: false, append, framesCommitted };
  } catch (err) {
    streamLog(stationId, "derive_segment_fail", {
      sessionId: body?.sessionId || expectSessionId,
      segmentId: body?.segmentId || expectSegmentId,
      message: String(err?.message || err).slice(0, 300),
    });
    throw err;
  } finally {
    try {
      fs.rmSync(extractDir, { recursive: true, force: true });
    } catch {
      /* ignore */
    }
  }
}

export function getSegmentIngestQueueDepth(stationId) {
  const state = getSegmentIngestState(stationId);
  return {
    queued: state.queue.length,
    workerRunning: state.workerRunning,
  };
}

function enqueueDeferredStreamFrame(stationId, body) {
  if (!deferredStreamFrames.has(stationId)) {
    deferredStreamFrames.set(stationId, []);
  }
  const queue = deferredStreamFrames.get(stationId);
  queue.push(body);
  streamLog(stationId, "frame_deferred", {
    sessionId: body.sessionId,
    frameIndex: body.frameIndex,
    depth: queue.length,
  });
}

async function flushDeferredStreamFrames(stationId) {
  const pending = deferredStreamFrames.get(stationId);
  if (!pending?.length) return;
  deferredStreamFrames.delete(stationId);
  streamLog(stationId, "frame_flush_start", { count: pending.length });
  for (const body of pending) {
    try {
      await handleStreamUpload(stationId, body);
    } catch (err) {
      streamLog(stationId, "frame_flush_error", {
        frameIndex: body.frameIndex,
        message: String(err?.message || err),
      });
    }
  }
  streamLog(stationId, "frame_flush_done", { count: pending.length });
}

/** Remove stale browser upload archives left from interrupted imports. */
export function cleanupOrphanIncomingArchives(maxAgeMs = ORPHAN_INCOMING_MAX_AGE_MS) {
  let entries;
  try {
    entries = fs.readdirSync(STREAM_ROOT, { withFileTypes: true });
  } catch {
    return 0;
  }
  const cutoff = Date.now() - maxAgeMs;
  let removed = 0;
  for (const ent of entries) {
    if (!ent.isDirectory() || ent.name.startsWith(".")) continue;
    const incoming = path.join(STREAM_ROOT, ent.name, ".upload", "incoming");
    let files;
    try {
      files = fs.readdirSync(incoming);
    } catch {
      continue;
    }
    for (const name of files) {
      if (!name.startsWith("browser_import_") || !name.endsWith(".tar.zst")) continue;
      const filePath = path.join(incoming, name);
      try {
        const st = fs.statSync(filePath);
        if (st.mtimeMs < cutoff) {
          fs.rmSync(filePath, { force: true });
          removed += 1;
        }
      } catch {
        /* ignore */
      }
    }
  }
  if (removed > 0) {
    console.log(`[stream-ingest] cleanup orphan incoming archives removed=${removed}`);
  }
  return removed;
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function readJson(p, fallback = null) {
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return fallback;
  }
}

function writeJson(p, obj) {
  writeJsonAtomic(p, obj);
}

function writeJsonAtomic(p, obj) {
  ensureDir(path.dirname(p));
  const tmp = `${p}.tmp.${process.pid}.${Date.now()}`;
  fs.writeFileSync(tmp, `${JSON.stringify(obj, null, 2)}\n`);
  fs.renameSync(tmp, p);
}

function locksDir(root) {
  return path.join(root, ".locks");
}

function stagingTmpRoot(root) {
  return path.join(root, "_staging_tmp");
}

function frameInflightDir(root, frameIndex) {
  return path.join(stagingTmpRoot(root), "inflight", `frame_${String(frameIndex).padStart(6, "0")}`);
}

function sleepMs(ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    /* spin */
  }
}

function acquireFileLock(lockPath, timeoutMs = 60_000) {
  ensureDir(path.dirname(lockPath));
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const fd = fs.openSync(lockPath, "wx");
      fs.writeFileSync(fd, `${process.pid}\n${Date.now()}\n`);
      return fd;
    } catch (e) {
      if (/** @type {NodeJS.ErrnoException} */ (e).code !== "EEXIST") throw e;
      try {
        const st = fs.statSync(lockPath);
        if (Date.now() - st.mtimeMs > Math.max(timeoutMs, 120_000)) {
          fs.unlinkSync(lockPath);
          continue;
        }
      } catch {
        /* ignore */
      }
    }
    sleepMs(25);
  }
  throw new Error(`lock timeout: ${lockPath}`);
}

function releaseFileLock(lockPath, fd) {
  try {
    if (typeof fd === "number") fs.closeSync(fd);
  } catch {
    /* ignore */
  }
  try {
    fs.unlinkSync(lockPath);
  } catch {
    /* ignore */
  }
}

function withFileLock(root, name, fn) {
  const lockPath = path.join(locksDir(root), `${name}.lock`);
  const fd = acquireFileLock(lockPath);
  try {
    return fn();
  } finally {
    releaseFileLock(lockPath, fd);
  }
}

function writeFileAtomic(targetPath, data) {
  ensureDir(path.dirname(targetPath));
  const part = `${targetPath}.part`;
  fs.writeFileSync(part, data);
  fs.renameSync(part, targetPath);
}

function isPublishedPath(rel) {
  const norm = rel.replace(/\\/g, "/");
  if (norm.includes("_staging_tmp") || norm.includes("/inflight/")) return false;
  if (norm.includes("/.locks/") || norm.endsWith(".lock")) return false;
  if (norm.includes(".muxing.tmp") || norm.endsWith(".part")) return false;
  if (/\/[^/]+\.tmp$/.test(norm)) return false;
  if (norm.endsWith(".jsonl") && norm !== "meta/tasks.jsonl") return false;
  if (norm === "meta/info.viewer.json" || norm.includes("chunks.json")) return false;
  if (norm.includes("session-registry.json") || norm.includes("disk-housekeeping.json")) return false;
  if (norm.startsWith("archive/")) return false;
  return true;
}

function chunksManifestPath(root) {
  return path.join(root, "live", "chunks.json");
}

function viewerInfoPath(root) {
  return path.join(root, "meta", "info.viewer.json");
}

function videoArtifactRel(videoKey) {
  return `videos/${videoKey}/chunk-000/file-000.mp4`;
}

function allChunkArtifactRels(root) {
  const keys = root ? ingestVideoKeys(root) : VIDEO_KEYS;
  return [
    "meta/info.json",
    "data/chunk-000/file-000.parquet",
    "meta/episodes/chunk-000/file-000.parquet",
    ...keys.map(videoArtifactRel),
  ];
}

function readChunksManifest(root) {
  return readJson(chunksManifestPath(root), {
    revision: 0,
    viewerTotalFrames: 0,
    publish: {},
  });
}

function writeChunksManifest(root, manifest) {
  writeJsonAtomic(chunksManifestPath(root), manifest);
}

function finalizeViewerScaffoldArtifacts(root) {
  const frames = 1;
  const manifest = readChunksManifest(root);
  const now = new Date().toISOString();
  manifest.viewerTotalFrames = frames;
  manifest.publish = manifest.publish || {};
  for (const rel of allChunkArtifactRels(root)) {
    const disk = artifactDiskPath(root, rel);
    if (rel === "meta/info.json" || (fs.existsSync(disk) && fs.statSync(disk).isFile())) {
      manifest.publish[rel] = { status: "finished", frames, updatedAt: now };
    }
  }
  writeChunksManifest(root, manifest);
}

function parquetSyncMarkerPath(root) {
  return path.join(root, "live", "parquet_sync.json");
}

function isViewerScaffold(root) {
  return readJson(parquetSyncMarkerPath(root), {}).viewer_scaffold === true;
}

function writeViewerScaffoldSnapshot(root, viewerFrames = 1) {
  const info = readJson(path.join(root, "meta", "info.json"), {});
  saveEpisodesIndex(root, { version: 1, episodes: [] });
  const viewerInfo = {
    ...info,
    total_frames: viewerFrames,
    total_episodes: 1,
    splits: { train: "0:1" },
  };
  writeJsonAtomic(viewerInfoPath(root), viewerInfo);
  return viewerInfo;
}

function isPlaceholderMp4(filePath) {
  if (!filePath || !fs.existsSync(filePath)) return false;
  return probeMp4FrameCount(filePath) <= 1;
}

function removePlaceholderMp4s(root, stationId, reason) {
  let removed = 0;
  for (const videoKey of ingestVideoKeys(root)) {
    const outFile = videoOutPath(root, videoKey);
    if (!isPlaceholderMp4(outFile)) continue;
    try {
      fs.unlinkSync(outFile);
      removed += 1;
    } catch {
      /* ignore */
    }
  }
  if (removed > 0) {
    streamLog(stationId, "placeholder_mp4_removed", { reason, count: removed });
  }
  return removed;
}

function clearViewerScaffold(root, stationId) {
  if (!isViewerScaffold(root)) return false;
  const index = loadEpisodesIndex(root);
  index.episodes = index.episodes.filter((ep) => ep.segment_id !== "legacy");
  saveEpisodesIndex(root, index);
  removePlaceholderMp4s(root, stationId, "viewer_scaffold_clear");
  writeJsonAtomic(parquetSyncMarkerPath(root), {
    viewer_scaffold: false,
    clearedAt: new Date().toISOString(),
  });
  streamLog(stationId, "viewer_scaffold_cleared", {});
  return true;
}

function countJsonlRows(root) {
  const jsonl = dataJsonlPath(root);
  if (!fs.existsSync(jsonl)) return 0;
  try {
    const raw = fs.readFileSync(jsonl, "utf8");
    if (!raw.trim()) return 0;
    return raw.split("\n").filter((line) => line.trim()).length;
  } catch {
    return 0;
  }
}

/** Align meta/info.json total_frames with official LeRobot data parquet (not jsonl). */
function syncInfoFramesFromDataset(root) {
  const metrics = resolveDeriveFrameMetrics(root);
  if (metrics.rowCount <= 0) return 0;
  const infoPath = path.join(root, "meta", "info.json");
  const info = readJson(infoPath, {});
  info.total_frames = metrics.rowCount;
  info.ingest_row_count = metrics.rowCount;
  if (metrics.frameIndexMin != null) info.frame_index_min = metrics.frameIndexMin;
  if (metrics.frameIndexMax != null) info.frame_index_max = metrics.frameIndexMax;
  writeJsonAtomic(infoPath, info);
  return metrics.rowCount;
}

function shouldRepairViewerScaffold(root) {
  if (!fs.existsSync(path.join(root, "meta", "info.json"))) return false;
  const index = loadEpisodesIndex(root);
  const realSegments = index.episodes.filter((ep) => ep.segment_id !== "legacy");
  if (realSegments.length > 0) return false;
  if (index.episodes.some((ep) => ep.segment_id === "legacy")) return true;
  if (!viewerScaffoldComplete(root)) return true;
  if (isViewerScaffold(root) && (readJson(viewerInfoPath(root), {}).total_episodes || 0) < 1) {
    return true;
  }
  return false;
}

function viewerScaffoldComplete(root) {
  const episodes = path.join(root, "meta", "episodes", "chunk-000", "file-000.parquet");
  const data = path.join(root, "data", "chunk-000", "file-000.parquet");
  const viewer = viewerInfoPath(root);
  if (!fs.existsSync(episodes) || !fs.existsSync(data) || !fs.existsSync(viewer)) return false;
  const viewerFrames = readJson(viewer, {}).total_frames || 0;
  if (viewerFrames < 1) return false;
  for (const key of ingestVideoKeys(root)) {
    const mp4 = path.join(root, videoArtifactRel(key));
    if (!fs.existsSync(mp4) || !fs.statSync(mp4).isFile()) return false;
  }
  return true;
}

function spawnScriptAsync(command, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { ...opts, stdio: ["ignore", "ignore", "pipe"] });
    let stderr = "";
    child.stderr?.on("data", (chunk) => {
      stderr += String(chunk);
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) {
        resolve({ ok: true });
        return;
      }
      reject(new Error(String(stderr || `exit ${code}`).slice(0, 500)));
    });
  });
}

async function runViewerScaffoldPythonAsync(root) {
  const py = resolveParquetPython();
  if (!py || !fs.existsSync(SYNC_SCRIPT)) return;
  await spawnScriptAsync(py, [SYNC_SCRIPT, "--viewer-scaffold", root], parquetSpawnOptions());
}

export function listStreamStationIds() {
  if (!fs.existsSync(STREAM_ROOT)) return [];
  try {
    return fs
      .readdirSync(STREAM_ROOT, { withFileTypes: true })
      .filter((ent) => ent.isDirectory() && !ent.name.startsWith("."))
      .map((ent) => ent.name);
  } catch {
    return [];
  }
}

/** Stagger disk cleanup so startup never blocks HTTP on a full-tree scan. */
export function scheduleDiskCleanupForAllStationsStaggered({
  intervalMs = Number(process.env.STREAM_STARTUP_CLEANUP_STAGGER_MS || 12_000),
} = {}) {
  const stationIds = listStreamStationIds();
  stationIds.forEach((stationId, idx) => {
    setTimeout(() => {
      try {
        runDiskCleanup(stationId);
      } catch (err) {
        console.warn(`[stream-ingest] cleanup failed station=${stationId}:`, err?.message || err);
      }
    }, idx * intervalMs);
  });
  if (stationIds.length > 0) {
    streamLog("system", "disk_cleanup_stagger_scheduled", {
      stations: stationIds.length,
      intervalMs,
    });
  }
}

function repairStreamViewerScaffold(stationId) {
  const root = stationRoot(stationId);
  if (!fs.existsSync(root)) return false;
  if (!shouldRepairViewerScaffold(root)) return false;
  saveEpisodesIndex(root, { version: 1, episodes: [] });
  const py = resolveParquetPython();
  if (py && fs.existsSync(SYNC_SCRIPT)) {
    spawnSync(py, [SYNC_SCRIPT, "--viewer-scaffold", root], parquetSpawnOptions());
  }
  writeViewerScaffoldSnapshot(root, 1);
  finalizeViewerScaffoldArtifacts(root);
  streamLog(stationId, "viewer_scaffold_repair", { total_frames: 1 });
  return true;
}

export async function repairStreamViewerScaffoldAsync(stationId) {
  const root = stationRoot(stationId);
  if (!fs.existsSync(root)) return false;
  if (!shouldRepairViewerScaffold(root)) return false;
  saveEpisodesIndex(root, { version: 1, episodes: [] });
  await runViewerScaffoldPythonAsync(root);
  writeViewerScaffoldSnapshot(root, 1);
  finalizeViewerScaffoldArtifacts(root);
  streamLog(stationId, "viewer_scaffold_repair", { total_frames: 1, async: true });
  return true;
}

function setChunkArtifactStatus(root, relPath, status, frames = null) {
  const manifest = readChunksManifest(root);
  const prev = manifest.publish?.[relPath];
  manifest.publish = manifest.publish || {};
  manifest.publish[relPath] = {
    status,
    frames: frames ?? prev?.frames ?? 0,
    updatedAt: new Date().toISOString(),
  };
  writeChunksManifest(root, manifest);
}

/** O(1) frame estimate — avoids readdir on 4×N staging jpgs (blocks HTTP for seconds). */
function countStagingFramesFast(root) {
  const info = readJson(path.join(root, "meta", "info.json"), {});
  const total = info?.total_frames;
  if (typeof total === "number" && total > 0) return total;
  const live = readJson(path.join(root, "live", "session.json"), {});
  const last = live?.lastFrameIndex;
  if (typeof last === "number" && last >= 0) return last + 1;
  return 0;
}

function countStagingFramesScan(root) {
  let maxIdx = -1;
  for (const videoKey of ingestVideoKeys(root)) {
    const dir = stagingDir(root, videoKey);
    if (!fs.existsSync(dir)) continue;
    for (const f of fs.readdirSync(dir)) {
      const m = /^frame_(\d+)\.jpg$/.exec(f);
      if (m) maxIdx = Math.max(maxIdx, Number.parseInt(m[1], 10));
    }
  }
  return Math.max(0, maxIdx + 1);
}

function countStagingFrames(root) {
  const fast = countStagingFramesFast(root);
  if (fast > 0) return fast;
  return countStagingFramesScan(root);
}

/** Physical JPG count in staging (min across cameras that have staging; inactive cameras skipped). */
export function stagingActiveVideoKeys(root) {
  const keys = ingestVideoKeys(root);
  const withStaging = keys.filter((videoKey) => stagingFrameRange(stagingDir(root, videoKey)));
  return withStaging.length > 0 ? withStaging : keys;
}

/** Cameras that participate in mux/readiness (staging, MP4 shards, or prior validation). */
export function activeMuxVideoKeys(root, { probeMp4 = false } = {}) {
  const keys = ingestVideoKeys(root);
  const muxVal = readJson(path.join(root, "live", "derive", "mux_validated.json"), {});
  const validated =
    muxVal.frames && typeof muxVal.frames === "object" ? muxVal.frames : {};
  const active = keys.filter((videoKey) => {
    if (stagingFrameRange(stagingDir(root, videoKey))) return true;
    if (Number(validated[videoKey] || 0) > 0) return true;
    if (probeMp4 && probeVideoKeyTotalMp4Frames(root, videoKey) > 0) return true;
    return false;
  });
  return active.length > 0 ? active : keys;
}

export function stagingJpegCount(root) {
  const keys = stagingActiveVideoKeys(root);
  if (!keys.length) return 0;
  let minCount = Infinity;
  for (const videoKey of keys) {
    const range = stagingFrameRange(stagingDir(root, videoKey));
    if (!range) return 0;
    minCount = Math.min(minCount, range.count);
  }
  return minCount === Infinity ? 0 : minCount;
}

/** @deprecated use stagingJpegCount */
export function countStagingJpgsOnDisk(root) {
  return stagingJpegCount(root);
}

export function stagingNeedsRehydrate(root) {
  const metrics = resolveDeriveFrameMetrics(root);
  const expected = metrics.expectedMp4Frames;
  if (expected <= 0) return false;
  return stagingJpegCount(root) < expected;
}

function isStreamChunkArtifact(rel) {
  const norm = rel.replace(/\\/g, "/");
  if (norm === "meta/info.json") return true;
  if (/^data\/chunk-\d+\/file-\d+\.parquet$/.test(norm)) return true;
  if (/^meta\/episodes\/chunk-\d+\/file-\d+\.parquet$/.test(norm)) return true;
  if (/^videos\/.+\/chunk-\d+\/file-\d+\.mp4$/.test(norm)) return true;
  return false;
}

function artifactDiskPath(root, rel) {
  return path.normalize(path.join(root, rel));
}

function canServeChunkArtifact(root, rel) {
  const manifest = readChunksManifest(root);
  const st = manifest.publish?.[rel];
  if (!st) return true;
  if (st.status === "finished") return true;
  if (st.status === "writing") {
    if (rel === "meta/info.json") {
      const viewer = viewerInfoPath(root);
      return fs.existsSync(viewer) && fs.statSync(viewer).isFile();
    }
    const disk = artifactDiskPath(root, rel);
    const tmpSibling = `${disk}.muxing.tmp`;
    const parquetTmp = `${disk}.tmp`;
    if (fs.existsSync(disk) && !fs.existsSync(tmpSibling) && !fs.existsSync(parquetTmp)) {
      return true;
    }
    return false;
  }
  return false;
}

function initChunksManifest(root, { resetViewer = false } = {}) {
  const publish = {};
  for (const rel of allChunkArtifactRels(root)) {
    publish[rel] = { status: "writing", frames: 0, updatedAt: new Date().toISOString() };
  }
  writeChunksManifest(root, { revision: 0, viewerTotalFrames: 0, publish });
  if (resetViewer) {
    try {
      fs.unlinkSync(viewerInfoPath(root));
    } catch {
      /* ignore */
    }
  }
}

function writeViewerInfoSnapshot(root, viewerFrames) {
  const info = readJson(path.join(root, "meta", "info.json"), {});
  const index = loadEpisodesIndex(root);
  ensureLegacyEpisodeSlot(root, index);
  saveEpisodesIndex(root, index);
  const viewerInfo = {
    ...info,
    total_frames: viewerFrames,
    total_episodes: Math.max(1, index.episodes.length || 1),
    splits: info.splits || { train: `0:${Math.max(1, index.episodes.length || 1)}` },
  };
  writeJsonAtomic(viewerInfoPath(root), viewerInfo);
  return viewerInfo;
}

function resolveParquetPython() {
  if (process.env.STREAM_PARQUET_PYTHON) return process.env.STREAM_PARQUET_PYTHON;
  for (const cand of ["/usr/bin/python3", "/usr/local/bin/python3"]) {
    if (fs.existsSync(cand)) return cand;
  }
  return null;
}

/** Drop to stream-ingest uid when lerobot (root) triggers parquet sync via shared module. */
function parquetSpawnOptions(extra = {}) {
  const opts = { stdio: "ignore", ...extra };
  const uid = Number(process.env.STREAM_RUN_UID || 1000);
  const gid = Number(process.env.STREAM_RUN_GID || 1000);
  if (typeof process.getuid === "function" && process.getuid() === 0 && uid > 0) {
    opts.uid = uid;
    opts.gid = gid;
  }
  return opts;
}

/** Only stream-ingest (uid 1000) may create or repair files under STREAM_ROOT. */
function mayMutateStreamFs() {
  const writerUid = Number(process.env.STREAM_RUN_UID || 1000);
  if (typeof process.getuid !== "function") return true;
  return process.getuid() === writerUid;
}

/** When true, segment commits skip per-segment mux/parquet; finalize once at session end. */
export function shouldDeferSessionPublish(stationId) {
  if (String(process.env.DERIVE_DEFER_PUBLISH || "1").trim() === "0") return false;
  const envKey = `DERIVE_ASYNC_${String(stationId).toUpperCase().replace(/-/g, "_")}`;
  const station = String(process.env[envKey] ?? "").trim().toLowerCase();
  const global = String(process.env.DERIVE_ASYNC || "0").trim().toLowerCase();
  const asyncOn =
    station === "1" || station === "true" || station === "yes" ||
    global === "1" || global === "true" || global === "yes";
  return asyncOn;
}

const sessionFinalizePending = new Map();

function sessionPublishStatePath(root) {
  return path.join(root, "state", "session_publish.json");
}

export function readSessionPublishState(stationId, sessionId) {
  const root = stationRoot(stationId);
  const st = readJson(sessionPublishStatePath(root), {});
  if (sessionId && st.sessionId && st.sessionId !== sessionId) return {};
  return st;
}

/** Progressive: jsonl frame count + viewer snapshot only (no mux/parquet). */
export function publishSessionProgressive(stationId) {
  const root = stationRoot(stationId);
  if (!fs.existsSync(root)) return;
  syncInfoFramesFromDataset(root);
  runViewerPublish(stationId);
}

/** One-shot session mux → parquet after all segments derived (async path). */
export function finalizeSessionPublish(stationId, sessionId) {
  const root = stationRoot(stationId);
  sessionFinalizePending.set(stationId, sessionId);
  writeJsonAtomic(sessionPublishStatePath(root), {
    sessionId,
    phase: "finalizing",
    startedAt: new Date().toISOString(),
  });
  syncInfoEpisodeCount(root);
  streamLog(stationId, "session_finalize_start", { sessionId });
  scheduleMux(stationId);
}

function onSessionFinalizeParquetDone(stationId) {
  const sessionId = sessionFinalizePending.get(stationId);
  if (!sessionId) return;
  sessionFinalizePending.delete(stationId);
  const root = stationRoot(stationId);
  writeJsonAtomic(sessionPublishStatePath(root), {
    sessionId,
    phase: "ready",
    readyAt: new Date().toISOString(),
  });
  streamLog(stationId, "session_finalize_ready", { sessionId });
  import("./derive-async.mjs")
    .then((mod) => mod.markSessionSegmentsReady(stationId, sessionId))
    .catch(() => {});
}

function hasCommittedStreamData(root) {
  const jsonl = path.join(root, "data", "chunk-000", "file-000.jsonl");
  if (fs.existsSync(jsonl) && fs.statSync(jsonl).size > 0) return true;
  return fs.existsSync(path.join(root, "meta", "info.json"));
}

function runViewerPublish(stationId) {
  const root = stationRoot(stationId);
  if (!fs.existsSync(root)) return;
  if (
    countStagingFrames(root) <= 0 &&
    !fs.existsSync(viewerInfoPath(root)) &&
    !hasCommittedStreamData(root)
  ) {
    return;
  }
  try {
    finalizePublishCycle(root, stationId);
  } catch (err) {
    streamLog(stationId, "viewer_publish_error", { message: String(err?.message || err) });
  }
}

function scheduleViewerPublish(stationId) {
  const prev = viewerPublishQueue.get(stationId);
  if (prev) clearTimeout(prev);
  viewerPublishQueue.set(
    stationId,
    setTimeout(() => {
      viewerPublishQueue.set(stationId, null);
      runViewerPublish(stationId);
    }, VIEWER_PUBLISH_DEBOUNCE_MS),
  );
}

function videoKeyFromArtifactRel(rel) {
  const m = /^videos\/(.+)\/chunk-\d+\/file-\d+\.mp4$/.exec(String(rel || "").replace(/\\/g, "/"));
  return m ? m[1] : null;
}

function mp4MeetsPublishTarget(frames, infoTotalFrames) {
  const target = Math.max(0, Number(infoTotalFrames || 0));
  if (target > 0) return frames >= target - 1;
  return frames > 0;
}

function finalizePublishCycle(root, stationId) {
  const info = readJson(path.join(root, "meta", "info.json"), {});
  const jsonlRows = countJsonlRows(root);
  const infoTotal = Math.max(0, jsonlRows || Number(info.total_frames || 0));
  const viewerFrames = infoTotal;
  if (viewerFrames > 0) {
    writeViewerInfoSnapshot(root, viewerFrames);
  }

  const manifest = readChunksManifest(root);
  manifest.revision = (manifest.revision || 0) + 1;
  manifest.viewerTotalFrames = viewerFrames;
  manifest.publish = manifest.publish || {};
  const now = new Date().toISOString();
  for (const rel of allChunkArtifactRels(root)) {
    if (rel === "meta/info.json") {
      if (viewerFrames > 0 && fs.existsSync(viewerInfoPath(root))) {
        manifest.publish[rel] = { status: "finished", frames: viewerFrames, updatedAt: now };
      }
      continue;
    }
    const disk = artifactDiskPath(root, rel);
    if (!fs.existsSync(disk) || !fs.statSync(disk).isFile()) continue;

    if (/^videos\/.+\/chunk-\d+\/file-\d+\.mp4$/.test(rel)) {
      const frames = probeMp4FrameCount(disk);
      const ready = mp4MeetsPublishTarget(frames, infoTotal);
      manifest.publish[rel] = {
        status: ready ? "finished" : "writing",
        frames,
        updatedAt: now,
      };
      continue;
    }

    manifest.publish[rel] = { status: "finished", frames: viewerFrames, updatedAt: now };
  }
  writeChunksManifest(root, manifest);
  streamLog(stationId, "publish_done", {
    sessionId: getActiveSessionId(root),
    viewerFrames,
    revision: manifest.revision,
  });
}

function markMuxArtifactsWriting(root) {
  for (const videoKey of ingestVideoKeys(root)) {
    setChunkArtifactStatus(root, videoArtifactRel(videoKey), "writing");
  }
}

function markParquetArtifactsWriting(root) {
  setChunkArtifactStatus(root, "data/chunk-000/file-000.parquet", "writing");
  setChunkArtifactStatus(root, "meta/episodes/chunk-000/file-000.parquet", "writing");
  setChunkArtifactStatus(root, "meta/info.json", "writing");
}

function segmentScratchJsonlPath(root, sessionId, segmentId) {
  return path.join(root, "data", "scratch", sessionId, `${segmentId}.jsonl`);
}

function stageFrameImages(root, frameIndex, images) {
  const inflight = frameInflightDir(root, frameIndex);
  ensureDir(inflight);
  for (const videoKey of ingestVideoKeys(root)) {
    const buf = imageBuffer(images, videoKey);
    if (!buf) continue;
    const safe = videoKey.replace(/\./g, "_");
    const tmpImage = path.join(inflight, `${safe}.jpg`);
    writeFileAtomic(tmpImage, buf);
    const finalPath = path.join(
      stagingDir(root, videoKey),
      `frame_${String(frameIndex).padStart(6, "0")}.jpg`,
    );
    ensureDir(path.dirname(finalPath));
    fs.renameSync(tmpImage, finalPath);
  }
  fs.rmSync(inflight, { recursive: true, force: true });
}

function appendSegmentScratchRow(root, sessionId, segmentId, row) {
  const scratch = segmentScratchJsonlPath(root, sessionId, segmentId);
  ensureDir(path.dirname(scratch));
  fs.appendFileSync(scratch, `${JSON.stringify(row)}\n`);
}

/** Merge per-segment scratch jsonl into dataset jsonl (single short lock). */
function mergeSegmentScratchToJsonl(root, sessionId, segmentId) {
  const scratch = segmentScratchJsonlPath(root, sessionId, segmentId);
  if (!fs.existsSync(scratch)) return;
  const payload = fs.readFileSync(scratch);
  if (!payload.length) {
    fs.unlinkSync(scratch);
    return;
  }
  withFileLock(root, "jsonl", () => {
    const jsonl = dataJsonlPath(root);
    ensureDir(path.dirname(jsonl));
    fs.appendFileSync(jsonl, payload);
  });
  fs.unlinkSync(scratch);
  try {
    const scratchDir = path.dirname(scratch);
    if (fs.existsSync(scratchDir) && fs.readdirSync(scratchDir).length === 0) {
      fs.rmdirSync(scratchDir);
    }
  } catch {
    /* ignore */
  }
}

function commitFrameAtomically(root, frameIndex, images, row) {
  stageFrameImages(root, frameIndex, images);
  withFileLock(root, "jsonl", () => {
    const jsonl = dataJsonlPath(root);
    ensureDir(path.dirname(jsonl));
    fs.appendFileSync(jsonl, `${JSON.stringify(row)}\n`);
  });
}

function segmentCommittedMarker(root, sessionId, segmentId) {
  return path.join(root, "live", "sessions", sessionId, "segments", `${segmentId}.done`);
}

function isSegmentCommitted(root, sessionId, segmentId) {
  return fs.existsSync(segmentCommittedMarker(root, sessionId, segmentId));
}

function markSegmentCommitted(root, sessionId, segmentId) {
  const marker = segmentCommittedMarker(root, sessionId, segmentId);
  ensureDir(path.dirname(marker));
  fs.writeFileSync(marker, "");
}

/** Clear partial segment commit so re-derive can retry after concurrent ingest races. */
function resetSegmentCommitState(root, sessionId, segmentId, frameIndices = []) {
  const paths = [
    segmentCommittedMarker(root, sessionId, segmentId),
    segmentMetaPath(root, sessionId, segmentId),
    segmentScratchJsonlPath(root, sessionId, segmentId),
    path.join(root, "live", "derive", "markers", sessionId, `${segmentId}.ok.json`),
    pendingSessionEpisodePath(root, sessionId),
  ];
  for (const p of paths) {
    try {
      fs.rmSync(p, { force: true });
    } catch {
      /* ignore */
    }
  }
  // Deferred session episodes must be re-derived as a whole after any segment reset.
  const markerDir = path.join(root, "live", "derive", "markers", sessionId);
  if (fs.existsSync(markerDir)) {
    for (const name of fs.readdirSync(markerDir)) {
      if (!name.endsWith(".ok.json")) continue;
      try {
        fs.rmSync(path.join(markerDir, name), { force: true });
      } catch {
        /* ignore */
      }
    }
  }
  for (const idx of frameIndices) {
    const fi = Number(idx);
    if (!Number.isInteger(fi) || fi < 0) continue;
    try {
      fs.unlinkSync(frameCommittedMarker(root, sessionId, fi));
    } catch {
      /* ignore */
    }
  }
}

function episodesIndexPath(root) {
  return path.join(root, "live", "episodes-index.json");
}

function episodeMetaPath(root) {
  return path.join(root, "live", "episode-meta.json");
}

function readEpisodeMeta(root) {
  return readJson(episodeMetaPath(root), null);
}

function writeEpisodeMeta(root, episodeMeta) {
  if (!episodeMeta) return;
  writeJsonAtomic(episodeMetaPath(root), episodeMeta);
}

function resolveEpisodeMeta(root, stationId, incoming) {
  const prev = readEpisodeMeta(root);
  const next = incoming || parseManifestToEpisodeMeta(null, stationId);
  const merged = mergeEpisodeMeta(prev, next);
  writeEpisodeMeta(root, merged);
  return merged;
}

function segmentMetaPath(root, sessionId, segmentId) {
  return path.join(root, "live", "sessions", sessionId, "segments", `${segmentId}.json`);
}

function loadEpisodesIndex(root) {
  const raw = readJson(episodesIndexPath(root), null);
  if (!raw || !Array.isArray(raw.episodes)) {
    return { version: 1, episodes: [] };
  }
  return raw;
}

function saveEpisodesIndex(root, index) {
  writeJsonAtomic(episodesIndexPath(root), index);
}

function frameTaskValue(root, stationId, frameTask) {
  const explicit = String(frameTask || "").trim();
  if (explicit && !isLegacyPlaceholderTask(explicit)) return explicit;
  return resolveSessionTask(root, stationId);
}

function resolveSessionTask(root, stationId, opts = {}) {
  const live = readJson(path.join(root, "live", "session.json"), {});
  const sid = stationId || stationIdFromRoot(root);
  const sessionId = opts.sessionId || live.sessionId;
  let explicit = opts.explicit;
  const mayUseLiveTask =
    explicit === undefined &&
    (!opts.sessionId || opts.sessionId === live.sessionId) &&
    live.task &&
    !isLegacyPlaceholderTask(live.task);
  if (mayUseLiveTask) {
    explicit = live.task;
  }
  return resolveTaskName({
    explicit,
    stationId: sid,
    sessionId,
    createdAt: opts.createdAt || live.startedAt,
  });
}

/** Base task label from session metadata (auto-generated when unset). */
function getStationTask(root, stationId) {
  return resolveSessionTask(root, stationId);
}

/** LeRobot sidebar line 3: prefer per-episode title from episodes-index. */
function formatEpisodeListTask(ep, fullTask) {
  const explicit = String(ep.title || "").trim();
  if (explicit) return explicit;
  let length = Math.max(0, Number(ep.length) || 0);
  if (length <= 0) {
    const fromIdx = Number(ep.dataset_from_index) || 0;
    const toIdx = Number(ep.dataset_to_index) || fromIdx;
    length = Math.max(0, toIdx - fromIdx);
  }
  return formatEpisodeDisplayTask(fullTask, length);
}

function formatEpisodeTitle(root, segmentId, length, source) {
  const fullTask = getStationTask(root, stationIdFromRoot(root));
  return formatEpisodeListTask(
    {
      length,
      source: source || "stream",
      committed_at: new Date().toISOString(),
    },
    fullTask,
  );
}

function ensureLegacyEpisodeSlot(root, index) {
  const info = readJson(path.join(root, "meta", "info.json"), {});
  const total = Number(info.total_frames || 0);
  if (total <= 0) return;
  const legacy = index.episodes.find((ep) => ep.segment_id === "legacy");
  const nonLegacy = index.episodes.filter((ep) => ep.segment_id !== "legacy");
  if (legacy) {
    if (nonLegacy.length > 0) {
      const minOtherStart = Math.min(
        ...nonLegacy.map((ep) => Number(ep.dataset_from_index ?? total)),
      );
      legacy.dataset_to_index = Math.min(Number(legacy.dataset_to_index ?? total), minOtherStart);
    } else {
      legacy.dataset_to_index = total;
    }
    legacy.length = Math.max(0, legacy.dataset_to_index - legacy.dataset_from_index);
    legacy.title = formatEpisodeListTask(legacy, getStationTask(root, stationIdFromRoot(root)));
    return;
  }
  if (index.episodes.length > 0) return;
  index.episodes.push({
    episode_index: 0,
    segment_id: "legacy",
    session_id: getActiveSessionId(root) || "unknown",
    dataset_from_index: 0,
    dataset_to_index: total,
    length: total,
    source: "legacy",
    title: formatEpisodeListTask(
      { length: total, source: "legacy", committed_at: new Date().toISOString() },
      getStationTask(root, stationIdFromRoot(root)),
    ),
    committed_at: new Date().toISOString(),
  });
}

function registerSessionEpisode(root, { sessionId, segmentId, fromFrame, toFrame, source, task }) {
  const fromIdx = Math.max(0, Number(fromFrame));
  const toIdx = Math.max(fromIdx + 1, Number(toFrame));
  if (!Number.isFinite(toIdx - fromIdx) || toIdx <= fromIdx) return null;

  const index = loadEpisodesIndex(root);
  index.episodes = index.episodes.filter((ep) => {
    if (ep.segment_id === "legacy") return false;
    if (ep.session_id === sessionId && ep.segment_id !== SESSION_EPISODE_SEGMENT_ID) {
      return false;
    }
    return true;
  });

  let entry = index.episodes.find(
    (ep) => ep.session_id === sessionId && ep.segment_id === SESSION_EPISODE_SEGMENT_ID,
  );
  const episodeMeta = readEpisodeMeta(root);
  const segments = new Set(entry?.segments || []);
  segments.add(segmentId);

  if (!entry) {
    entry = attachEpisodeMetaToIndexEntry(
      {
        episode_index: 0,
        segment_id: SESSION_EPISODE_SEGMENT_ID,
        session_id: sessionId,
        dataset_from_index: fromIdx,
        dataset_to_index: toIdx,
        length: toIdx - fromIdx,
        source: source || "stream",
        title: "",
        segments: [...segments],
        committed_at: new Date().toISOString(),
      },
      episodeMeta,
    );
    index.episodes.push(entry);
  } else {
    entry.dataset_from_index = Math.min(Number(entry.dataset_from_index ?? fromIdx), fromIdx);
    entry.dataset_to_index = Math.max(Number(entry.dataset_to_index ?? toIdx), toIdx);
    entry.length = entry.dataset_to_index - entry.dataset_from_index;
    entry.segments = [...segments];
    entry.committed_at = new Date().toISOString();
  }

  if (task && String(task).trim()) {
    entry.title = String(task).trim();
  }
  entry.episode_index = 0;
  index.episodes.forEach((ep, i) => {
    ep.episode_index = i;
  });
  entry.title = formatEpisodeListTask(
    entry,
    episodeDisplayTask(root, stationIdFromRoot(root), sessionId, entry.committed_at),
  );

  saveEpisodesIndex(root, index);
  writeJsonAtomic(segmentMetaPath(root, sessionId, segmentId), {
    sessionId,
    segmentId,
    fromFrame: fromIdx,
    toFrame: toIdx,
    framesCommitted: toIdx - fromIdx,
    committedAt: new Date().toISOString(),
  });
  return entry;
}

/** Align session episode span with committed jsonl bounds after all segments derived. */
export function refreshSessionEpisodeFromInfo(stationId, sessionId) {
  if (!useSessionSingleEpisode(stationId)) return null;
  const root = stationRoot(stationId);
  syncInfoFramesFromDataset(root);

  const index = loadEpisodesIndex(root);
  let entry = index.episodes.find(
    (ep) => ep.session_id === sessionId && ep.segment_id === SESSION_EPISODE_SEGMENT_ID,
  );
  if (!entry) return null;
  const fromIdx = Number(entry.dataset_from_index) || 0;
  const toIdx = Number(entry.dataset_to_index) || fromIdx;
  const total = Math.max(0, toIdx - fromIdx);
  if (total <= 0) return null;

  entry.length = total;
  entry.committed_at = new Date().toISOString();
  entry.title = formatEpisodeListTask(
    entry,
    episodeDisplayTask(root, stationId, sessionId, entry.committed_at),
  );
  entry.episode_index = 0;
  index.episodes.forEach((ep, i) => {
    ep.episode_index = i;
  });
  saveEpisodesIndex(root, index);
  writeTasksJsonl(root);
  syncInfoEpisodeCount(root);
  return entry;
}

export function syncEpisodesMetaOnly(stationId) {
  const root = stationRoot(stationId);
  if (!fs.existsSync(SYNC_SCRIPT)) return;
  const py = resolveParquetPython();
  if (!py) return;
  spawnSync(py, [SYNC_SCRIPT, "--meta-only", root], parquetSpawnOptions());
}

function registerSegmentEpisode(root, { sessionId, segmentId, fromFrame, toFrame, source }) {
  const stationId = stationIdFromRoot(root);
  if (useSessionSingleEpisode(stationId)) {
    return registerSessionEpisode(root, { sessionId, segmentId, fromFrame, toFrame, source });
  }
  const fps = Number(readJson(path.join(root, "meta", "info.json"), {}).fps || DEFAULT_FPS);
  const fromIdx = Math.max(0, Number(fromFrame));
  const toIdx = Math.max(fromIdx + 1, Number(toFrame));
  const length = toIdx - fromIdx;
  if (!Number.isFinite(length) || length <= 0) return null;

  const index = loadEpisodesIndex(root);
  if (index.episodes.some((ep) => ep.segment_id === segmentId)) {
    return index.episodes.find((ep) => ep.segment_id === segmentId);
  }

  if (source !== "import") {
    ensureLegacyEpisodeSlot(root, index);
  }
  const legacy = index.episodes.find((ep) => ep.segment_id === "legacy");
  if (legacy && fromIdx > legacy.dataset_from_index && fromIdx < legacy.dataset_to_index) {
    legacy.dataset_to_index = fromIdx;
    legacy.length = Math.max(0, legacy.dataset_to_index - legacy.dataset_from_index);
    legacy.title = formatEpisodeListTask(legacy, getStationTask(root, stationIdFromRoot(root)));
  }

  const episodeIndex = index.episodes.length;
  const title = formatEpisodeTitle(root, segmentId, length, source || "stream");
  const episodeMeta = readEpisodeMeta(root);
  const entry = attachEpisodeMetaToIndexEntry(
    {
      episode_index: episodeIndex,
      segment_id: segmentId,
      session_id: sessionId,
      dataset_from_index: fromIdx,
      dataset_to_index: toIdx,
      length,
      source: source || "stream",
      title,
      committed_at: new Date().toISOString(),
    },
    episodeMeta,
  );
  index.episodes.push(entry);
  saveEpisodesIndex(root, index);
  writeJsonAtomic(segmentMetaPath(root, sessionId, segmentId), entry);
  return entry;
}

function syncInfoEpisodeCount(root) {
  const index = loadEpisodesIndex(root);
  const stationId = stationIdFromRoot(root);
  if (!useSessionSingleEpisode(stationId)) {
    ensureLegacyEpisodeSlot(root, index);
  }
  saveEpisodesIndex(root, index);
  const infoPath = path.join(root, "meta", "info.json");
  const info = readJson(infoPath, null);
  if (!info) return;
  if (useSessionSingleEpisode(stationId)) {
    const sessionEpisodes = index.episodes.filter(
      (ep) => ep.segment_id === SESSION_EPISODE_SEGMENT_ID,
    );
    info.total_episodes = Math.max(1, sessionEpisodes.length || index.episodes.length);
  } else {
    info.total_episodes = Math.max(1, index.episodes.length);
  }
  writeJsonAtomic(infoPath, info);
}

function segmentImageKey(frameIndex, videoKey) {
  return `${frameIndex}__${videoKey.replace(/\./g, "_")}`;
}

function scheduleBackgroundTasks(stationId) {
  if (isStationImportActive(stationId)) return;
  let st = backgroundBatchState.get(stationId);
  if (!st) {
    st = { timer: null, pending: false };
    backgroundBatchState.set(stationId, st);
  }
  st.pending = true;
  if (st.timer) return;
  st.timer = setTimeout(() => {
    st.timer = null;
    if (!st.pending) return;
    st.pending = false;
    const depth = (segmentIngestQueues.get(stationId)?.queue.length) || 0;
    streamLog(stationId, "background_batch", { ingestDepth: depth });
    if (!isSegmentMp4PrimaryPath()) {
      scheduleMux(stationId);
    }
    scheduleViewerPublish(stationId);
    scheduleParquetSync(stationId);
    if (depth > 0) {
      st.pending = true;
      scheduleBackgroundTasks(stationId);
    }
  }, STREAM_BACKGROUND_BATCH_MS);
}

function getSegmentIngestState(stationId) {
  if (!segmentIngestQueues.has(stationId)) {
    segmentIngestQueues.set(stationId, { queue: [], workerRunning: false });
  }
  return segmentIngestQueues.get(stationId);
}

async function processSegmentIngestJob(stationId, job) {
  const root = stationRoot(stationId);
  if (isViewerScaffold(root)) {
    clearViewerScaffold(root, stationId);
  }
  const { sessionId, segmentId, frames, images, shapes, host, episodeMeta: jobEpisodeMeta } = job;
  const livePath = path.join(root, "live", "session.json");
  const live = readJson(livePath, {});

  if (isSegmentCommitted(root, sessionId, segmentId)) {
    touchHeartbeat(root, stationId, host || live.host || null);
    scheduleParquetSync(stationId);
    return { framesCommitted: 0, duplicate: true };
  }

  const episodeMeta = resolveEpisodeMeta(root, stationId, jobEpisodeMeta || null);

  let info = readJson(path.join(root, "meta", "info.json"));
  if (!info) {
    info = defaultInfo(stationId, shapes || {}, episodeMeta);
  }
  bootstrapDatasetSchema(info, episodeMeta);
  for (const [key, shape] of Object.entries(shapes || {})) {
    if (info.features?.[key] && Array.isArray(shape) && shape.length >= 2) {
      info.features[key].shape = [shape[0], shape[1], 3];
      info.features[key].info["video.height"] = shape[0];
      info.features[key].info["video.width"] = shape[1];
    }
  }

  const useSegmentMp4 = Boolean(job.segmentMp4 && job.extractDir);
  if (useSegmentMp4 && job.extractDir) {
    const mp4Res = await ingestSegmentMp4Shards(root, stationId, sessionId, segmentId, job.extractDir, {
      videoArtifactRel,
      setChunkArtifactStatus,
      streamLog,
    });
    if (!mp4Res.ok) {
      streamLog(stationId, "segment_mp4_fail", {
        sessionId,
        segmentId,
        reason: mp4Res.reason || "ingest_failed",
        cameras: mp4Res.cameras ?? 0,
      });
      throw new Error(`segment_mp4_ingest_failed: ${mp4Res.reason || "unknown"}`);
    }
  }

  let committed = 0;
  let maxFrame = info.total_frames || 0;
  let minFrame = null;
  let maxCommittedFrame = null;
  for (const f of frames) {
    const frameIndex = Number(f.frameIndex ?? f.frame_index ?? -1);
    if (!Number.isInteger(frameIndex) || frameIndex < 0) continue;
    if (isFrameCommitted(root, sessionId, frameIndex)) continue;

    const frameImages = {};
    if (!useSegmentMp4) {
      const keys = ingestVideoKeys(root);
      for (const videoKey of keys) {
        const buf = resolveFrameImage(images, frameIndex, videoKey);
        if (buf) frameImages[videoKey] = buf;
      }
    }
    const row = buildCanonicalFrameRow(
      {
        frame_index: frameIndex,
        timestamp_ns: f.timestampNs ?? f.timestamp_ns ?? 0,
        task: frameTaskValue(root, stationId, f.task),
        "observation.state": f.observationState || f["observation.state"] || [0, 0, 0, 0, 0, 0],
        "observation.pose": f.observationPose || f["observation.pose"] || [0, 0, 0, 0, 0, 0, 1],
        "observation.hands": f.observationHands || f["observation.hands"] || new Array(63).fill(0),
        action: f.actionVector || f.action || [0],
      },
      info,
    );
    if (!useSegmentMp4) {
      stageFrameImages(root, frameIndex, frameImages);
    }
    appendSegmentScratchRow(root, sessionId, segmentId, row);
    markFrameCommitted(root, sessionId, frameIndex);
    committed += 1;
    maxFrame = Math.max(maxFrame, frameIndex + 1);
    minFrame = minFrame === null ? frameIndex : Math.min(minFrame, frameIndex);
    maxCommittedFrame = maxCommittedFrame === null ? frameIndex : Math.max(maxCommittedFrame, frameIndex);
  }

  if (committed > 0) {
    mergeSegmentScratchToJsonl(root, sessionId, segmentId);
  } else {
    const scratch = segmentScratchJsonlPath(root, sessionId, segmentId);
    if (fs.existsSync(scratch)) {
      try {
        fs.unlinkSync(scratch);
      } catch {
        /* ignore */
      }
    }
  }

  if (useSegmentMp4) {
    try {
      await writeMuxValidatedSnapshot(stationId, sessionId);
    } catch {
      /* ignore */
    }
  }

  syncInfoEpisodeCount(root);
  if (committed > 0) {
    syncInfoFramesFromDataset(root);
  } else {
    info.total_frames = maxFrame;
    writeJsonAtomic(path.join(root, "meta", "info.json"), info);
  }

  live.sessionId = sessionId;
  live.updatedAt = new Date().toISOString();
  live.lastFrameIndex = Math.max(live.lastFrameIndex ?? -1, maxFrame - 1);
  writeJson(livePath, live);

  markSegmentCommitted(root, sessionId, segmentId);
  const segMetaPath = segmentMetaPath(root, sessionId, segmentId);
  ensureDir(path.dirname(segMetaPath));
  writeJsonAtomic(segMetaPath, {
    sessionId,
    segmentId,
    framesCommitted: committed,
    committedAt: new Date().toISOString(),
  });
  if (committed > 0 && minFrame !== null && maxCommittedFrame !== null) {
    registerSegmentEpisode(root, {
      sessionId,
      segmentId,
      fromFrame: minFrame,
      toFrame: maxCommittedFrame + 1,
      source: job.ingestSource || "stream",
    });
    syncInfoEpisodeCount(root);
    if (useSessionSingleEpisode(stationId)) {
      syncEpisodesMetaOnly(stationId);
    } else if (!job.deferSessionPublish) {
      scheduleParquetSync(stationId);
    }
  }
  touchHeartbeat(root, stationId, host || live.host || null);

  if (!useSegmentMp4 && committed > 0) {
    scheduleMux(stationId);
  }

  streamLog(stationId, "segment_committed", {
    sessionId,
    segmentId,
    framesCommitted: committed,
    totalFrames: maxFrame,
  });
  return { framesCommitted: committed, duplicate: false, totalFrames: maxFrame };
}

function pumpSegmentIngestQueue(stationId) {
  const state = getSegmentIngestState(stationId);
  if (state.workerRunning || state.queue.length === 0) return;
  state.workerRunning = true;
  setImmediate(async () => {
    let n = 0;
    const batchSize = isSegmentMp4PrimaryPath() ? 1 : SEGMENT_INGEST_BATCH_SIZE;
    while (state.queue.length > 0 && n < batchSize) {
      const job = state.queue.shift();
      const t0 = Date.now();
      try {
        const result = await processSegmentIngestJob(stationId, job);
        const ms = Date.now() - t0;
        streamLog(stationId, "segment_commit_ms", {
          sessionId: job.sessionId,
          segmentId: job.segmentId,
          commitMs: ms,
          ingestDepth: state.queue.length,
        });
        job._onComplete?.(null, result);
      } catch (err) {
        streamLog(stationId, "segment_commit_error", {
          sessionId: job.sessionId,
          segmentId: job.segmentId,
          message: String(err?.message || err),
        });
        if (job.sessionId && job.segmentId) {
          markSegmentIngestError(stationRoot(stationId), job.sessionId, job.segmentId, err);
        }
        job._onComplete?.(err);
      }
      n += 1;
    }
    state.workerRunning = false;
    if (state.queue.length > 0) {
      pumpSegmentIngestQueue(stationId);
    } else if (!isStationImportActive(stationId) && !shouldDeferSessionPublish(stationId)) {
      scheduleBackgroundTasks(stationId);
    }
  });
}

function enqueueSegmentIngest(stationId, job) {
  const state = getSegmentIngestState(stationId);
  state.queue.push(job);
  streamLog(stationId, "segment_accepted", {
    sessionId: job.sessionId,
    segmentId: job.segmentId,
    frameCount: job.frames?.length ?? 0,
    ingestDepth: state.queue.length,
  });
  pumpSegmentIngestQueue(stationId);
}

function enqueueSegmentIngestAwait(stationId, job) {
  return new Promise((resolve, reject) => {
    job._onComplete = (err, result) => {
      if (err) reject(err);
      else resolve(result);
    };
    enqueueSegmentIngest(stationId, job);
  });
}

function writeTasksJsonl(root, task) {
  ensureDir(path.join(root, "meta"));
  const explicit = task !== undefined && task !== null ? String(task).trim() : "";
  const fullTask = explicit || getStationTask(root, stationIdFromRoot(root));
  const index = loadEpisodesIndex(root);
  const lines =
    index.episodes.length > 0
      ? index.episodes.map((ep) =>
          JSON.stringify({
            task_index: Number(ep.episode_index) || 0,
            task: formatEpisodeListTask(ep, fullTask),
          }),
        )
      : [JSON.stringify({ task_index: 0, task: formatEpisodeDisplayTask(fullTask, 0) })];
  fs.writeFileSync(path.join(root, "meta", "tasks.jsonl"), `${lines.join("\n")}\n`);
}

function isRecentActivity(iso) {
  if (!iso) return false;
  const age = Date.now() - Date.parse(iso);
  return Number.isFinite(age) && age >= 0 && age < HEARTBEAT_TTL_MS;
}

/** Online when heartbeat OR frame/session activity within TTL. */
export function isStationLive(stationId) {
  const root = stationRoot(stationId);
  const hb = readJson(path.join(root, "live", "heartbeat.json"));
  if (isRecentActivity(hb?.at)) return true;
  const session = readJson(path.join(root, "live", "session.json"));
  if (isRecentActivity(session?.updatedAt)) return true;
  if (isRecentActivity(session?.resumedAt)) return true;
  return false;
}

const STATION_LIVE_CACHE_MS = Number(process.env.STATION_LIVE_CACHE_MS || 5_000);
/** Keep UI "online" briefly after heartbeat gap (upload backlog on ingest). */
const STATION_LIVE_OFFLINE_GRACE_MS = Number(process.env.STATION_LIVE_OFFLINE_GRACE_MS || 90_000);
/** @type {Map<string, { online: boolean, at: number; lastOnlineAt?: number }>} */
const stationLiveCache = new Map();

function markStationLiveCache(stationId, online) {
  const now = Date.now();
  const prev = stationLiveCache.get(stationId);
  stationLiveCache.set(stationId, {
    online,
    at: now,
    lastOnlineAt: online ? now : prev?.lastOnlineAt ?? 0,
  });
}

/** Cached isStationLive for list API — avoids disk churn under ingest load. */
export function isStationLiveCached(stationId) {
  const now = Date.now();
  const hit = stationLiveCache.get(stationId);
  if (hit && now - hit.at < STATION_LIVE_CACHE_MS) {
    return hit.online;
  }
  const fresh = isStationLive(stationId);
  let online = fresh;
  if (fresh) {
    markStationLiveCache(stationId, true);
    return true;
  }
  const lastOnlineAt = hit?.lastOnlineAt ?? 0;
  if (lastOnlineAt > 0 && now - lastOnlineAt < STATION_LIVE_OFFLINE_GRACE_MS) {
    online = true;
  }
  markStationLiveCache(stationId, online);
  return online;
}

function touchHeartbeat(root, stationId, host, extra = {}) {
  const liveDir = path.join(root, "live");
  ensureDir(liveDir);
  const prev = readJson(path.join(liveDir, "heartbeat.json"), {});
  const captureState =
    typeof extra.captureState === "string" && extra.captureState.trim()
      ? extra.captureState.trim()
      : prev.captureState || "unknown";
  writeJson(path.join(liveDir, "heartbeat.json"), {
    stationId,
    at: new Date().toISOString(),
    host: host ?? prev.host ?? null,
    captureState,
  });
  markStationLiveCache(stationId, true);
}

const VALID_CAPTURE_STATES = new Set([
  "offline",
  "idle",
  "starting",
  "warming",
  "recording",
  "stopping",
  "unknown",
]);

export function getStationCaptureState(stationId) {
  const root = stationRoot(stationId);
  const hb = readJson(path.join(root, "live", "heartbeat.json"), {});
  const raw = String(hb.captureState || "unknown");
  return VALID_CAPTURE_STATES.has(raw) ? raw : "unknown";
}

export function isRemotePreviewAllowed(stationId) {
  if (!isStationLiveCached(stationId)) return false;
  return getStationCaptureState(stationId) === "idle";
}

function defaultInfo(stationId, shapes, episodeMeta = null, { intrinsics = null } = {}) {
  const features = {};
  for (const key of resolveVideoKeysForStation(stationId, { intrinsics, shapes })) {
    const [h, w] = shapes[key] || [1200, 1920];
    features[key] = {
      dtype: "video",
      shape: [h, w, 3],
      names: ["height", "width", "channels"],
      info: {
        "video.height": h,
        "video.width": w,
        "video.codec": "h264",
        "video.pix_fmt": "yuv420p",
        "video.is_depth_map": false,
        "video.fps": DEFAULT_FPS,
        "video.channels": 3,
        has_audio: false,
      },
    };
  }
  features["observation.state"] = {
    dtype: "float32",
    shape: [6],
    names: ["gx", "gy", "gz", "ax", "ay", "az"],
  };
  features["observation.pose"] = {
    dtype: "float32",
    shape: [7],
    names: ["x", "y", "z", "qx", "qy", "qz", "qw"],
  };
  features["observation.hands"] = {
    dtype: "float32",
    shape: [63],
    names: null,
  };
  features.action = { dtype: "float32", shape: [1], names: null };

  const info = {
    codebase_version: "v3.0",
    robot_type: "oak_4p_ego",
    total_episodes: 1,
    total_frames: 0,
    total_tasks: 1,
    chunks_size: 1000,
    data_files_size_in_mb: 100,
    video_files_size_in_mb: 200,
    fps: DEFAULT_FPS,
    splits: { train: "0:1" },
    data_path: "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
    video_path: "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
    features,
    ego_capture: {
      capture_device: "OAK-4P-New (DepthAI)",
      camera_layout: "ego_four_cam_front_rear",
      stream_station: stationId,
      stream_mode: "frame_push",
    },
  };
  return bootstrapDatasetSchema(info, episodeMeta);
}

function sessionPath(root, sessionId) {
  return path.join(root, "sessions", sessionId);
}

function archiveRoot(root) {
  return path.join(root, "archive");
}

function sessionRegistryPath(root) {
  return path.join(root, "live", "session-registry.json");
}

function stationQuotaBytes(stationId) {
  const envKey = `STREAM_QUOTA_GB_${stationId.replace(/[^a-zA-Z0-9]/g, "_").toUpperCase()}`;
  const gb = Number(process.env[envKey] || process.env.STREAM_QUOTA_GB || DEFAULT_QUOTA_GB);
  return Math.max(1, gb) * 1024 ** 3;
}

function retentionCutoffMs() {
  return Date.now() - RETENTION_DAYS * 24 * 60 * 60 * 1000;
}

function readSessionRegistry(root) {
  return readJson(sessionRegistryPath(root), { activeSessionId: null, sessions: {} });
}

function writeSessionRegistry(root, registry) {
  writeJsonAtomic(sessionRegistryPath(root), registry);
}

function getActiveSessionId(root) {
  const live = readJson(path.join(root, "live", "session.json"), {});
  return live.sessionId || null;
}

function dirSizeBytes(dir) {
  if (!fs.existsSync(dir)) return 0;
  let total = 0;
  const stack = [dir];
  while (stack.length) {
    const current = stack.pop();
    let entries = [];
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const ent of entries) {
      const full = path.join(current, ent.name);
      try {
        if (ent.isDirectory()) stack.push(full);
        else if (ent.isFile()) total += fs.statSync(full).size;
      } catch {
        /* ignore */
      }
    }
  }
  return total;
}

function readDiskHousekeeping(root) {
  return readJson(path.join(root, "live", "disk-housekeeping.json"), null);
}

/** Avoid full-tree scans on hot HTTP paths; refresh via runDiskCleanup. */
function cachedDiskUsageBytes(root) {
  const hk = readDiskHousekeeping(root);
  if (!hk || typeof hk.usageBytes !== "number") return null;
  const at = hk.at ? Date.parse(hk.at) : NaN;
  if (!Number.isFinite(at) || Date.now() - at > DISK_USAGE_CACHE_MAX_AGE_MS) return null;
  return hk.usageBytes;
}

function rmRfSafe(target) {
  try {
    fs.rmSync(target, { recursive: true, force: true });
  } catch {
    /* ignore */
  }
}

function withCleanupLock(root, fn) {
  const lockPath = path.join(locksDir(root), "cleanup.lock");
  let fd = null;
  try {
    fd = acquireFileLock(lockPath, 8_000);
    fn();
  } catch {
    /* mux/ingest holds lock — skip this cycle */
  } finally {
    if (fd !== null) releaseFileLock(lockPath, fd);
  }
}

function archiveLiveDataForSession(_root, _sessionId) {
  // Disabled: official LeRobot appends episodes in place — never move data/videos/meta.
  return null;
}

function registerStreamSession(root, sessionId, { isResume, previousSessionId }) {
  const registry = readSessionRegistry(root);
  if (previousSessionId && previousSessionId !== sessionId && !isResume) {
    const prev = registry.sessions[previousSessionId] || {};
    prev.endedAt = new Date().toISOString();
    registry.sessions[previousSessionId] = prev;
  }
  if (!registry.sessions[sessionId]) {
    registry.sessions[sessionId] = {
      startedAt: new Date().toISOString(),
      endedAt: null,
      archiveDir: null,
    };
  }
  registry.sessions[sessionId].endedAt = null;
  registry.activeSessionId = sessionId;
  writeSessionRegistry(root, registry);
}

function listArchiveDirs(root) {
  const base = archiveRoot(root);
  if (!fs.existsSync(base)) return [];
  return fs
    .readdirSync(base, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => ({
      name: d.name,
      path: path.join(base, d.name),
      mtimeMs: fs.statSync(path.join(base, d.name)).mtimeMs,
    }))
    .sort((a, b) => a.mtimeMs - b.mtimeMs);
}

function purgeExpiredArchives(root, activeSessionId) {
  const cutoff = retentionCutoffMs();
  let freed = 0;
  for (const entry of listArchiveDirs(root)) {
    if (activeSessionId && entry.name.startsWith(`${activeSessionId}_`)) continue;
    if (entry.mtimeMs >= cutoff) continue;
    rmRfSafe(entry.path);
    freed += 1;
  }
  return freed;
}

function purgeExpiredSessionMarkers(root, activeSessionId) {
  const sessionsDir = path.join(root, "sessions");
  if (!fs.existsSync(sessionsDir)) return 0;
  const cutoff = retentionCutoffMs();
  let freed = 0;
  for (const ent of fs.readdirSync(sessionsDir, { withFileTypes: true })) {
    if (!ent.isDirectory()) continue;
    if (ent.name === activeSessionId) continue;
    const full = path.join(sessionsDir, ent.name);
    const registry = readSessionRegistry(root);
    const meta = registry.sessions?.[ent.name];
    const endedAt = meta?.endedAt ? Date.parse(meta.endedAt) : fs.statSync(full).mtimeMs;
    if (Number.isFinite(endedAt) && endedAt >= cutoff) continue;
    rmRfSafe(full);
    freed += 1;
  }
  return freed;
}

function purgeArchivesUntilUnderQuota(root, activeSessionId, quotaBytes) {
  if (String(process.env.STREAM_RAW_RETAIN_UNTIL_READY || "0").trim() === "1") {
    return 0;
  }
  let usage = cachedDiskUsageBytes(root);
  if (usage == null) usage = dirSizeBytes(root);
  if (usage <= quotaBytes * DISK_HIGH_WATER_RATIO) return 0;
  const archives = listArchiveDirs(root);
  let removed = 0;
  const maxPerRun = Number(process.env.STREAM_ARCHIVE_PURGE_MAX || 3);
  for (const entry of archives) {
    if (removed >= maxPerRun) break;
    if (activeSessionId && entry.name.startsWith(`${activeSessionId}_`)) continue;
    rmRfSafe(entry.path);
    removed += 1;
  }
  return removed;
}

function muxStatePath(root) {
  return path.join(root, "live", "mux-state.json");
}

/** Wipe legacy mux-state and partial MP4s before video export / full staging mux. */
export function resetMuxArtifactsForFullRemux(stationId, root) {
  try {
    fs.unlinkSync(muxStatePath(root));
  } catch {
    /* ignore */
  }
  try {
    fs.unlinkSync(path.join(root, "live", "derive", "mux_validated.json"));
  } catch {
    /* ignore */
  }
  for (const videoKey of ingestVideoKeys(root)) {
    const outFile = videoOutPath(root, videoKey);
    for (const p of [
      outFile,
      `${outFile}.segment.tmp.mp4`,
      `${outFile}.muxing.tmp`,
      `${outFile}.concat.txt`,
    ]) {
      try {
        if (fs.existsSync(p)) fs.unlinkSync(p);
      } catch {
        /* ignore */
      }
    }
    setChunkArtifactStatus(root, videoArtifactRel(videoKey), "writing", 0);
  }
  streamLog(stationId, "mux_full_reset", { mode: "full" });
}

/** Incremental mux: keep existing MP4 shards; only clear transient mux state. */
export function resetMuxArtifactsForIncrementalRemux(stationId, root) {
  try {
    fs.unlinkSync(muxStatePath(root));
  } catch {
    /* ignore */
  }
  streamLog(stationId, "mux_incremental_reset", { mode: "incremental" });
}

export function planMuxRemux(root) {
  const stagingFrames = stagingJpegCount(root);
  const metrics = resolveDeriveFrameMetrics(root);
  const rowTarget = metrics.rowCount;
  const fullDatasetMux =
    rowTarget > 0 &&
    stagingFrames >= rowTarget - 1 &&
    stagingFrames <= rowTarget + 1;
  return { stagingFrames, metrics, fullDatasetMux };
}

function probeStationVideoFrameCounts(root) {
  const frames = {};
  for (const videoKey of ingestVideoKeys(root)) {
    frames[videoKey] = probeVideoKeyTotalMp4Frames(root, videoKey);
  }
  return frames;
}

/** MP4 readiness: jsonl row count (not index span). API path uses cache only — no ffprobe. */
function mp4ReadinessOk(target, minFrames, maxFrames) {
  if (target <= 0 || minFrames <= 0) return false;
  if (isSegmentMp4PrimaryPath()) {
    // Edge H264 segment shards often carry fewer packets than jsonl rows; require cross-camera consistency.
    return minFrames >= Math.floor(target * 0.85) && maxFrames - minFrames <= 120;
  }
  return minFrames >= target - 1 && maxFrames <= target + 1 && maxFrames - minFrames <= 1;
}

function evaluateMp4Readiness(root, metrics, { forceProbe = false } = {}) {
  const target = metrics.rowCount > 0 ? metrics.rowCount : metrics.expectedMp4Frames;
  const muxVal = readJson(path.join(root, "live", "derive", "mux_validated.json"), {});
  const cachedFrames = muxVal.frames && typeof muxVal.frames === "object" ? muxVal.frames : null;
  if (!forceProbe && cachedFrames && Object.keys(cachedFrames).length > 0) {
    const muxKeys = activeMuxVideoKeys(root);
    const vals = muxKeys.map((k) => Number(cachedFrames[k] || 0));
    if (!vals.some((n) => n <= 0)) {
      const minFrames = Math.min(...vals);
      const maxFrames = Math.max(...vals);
      const ok = mp4ReadinessOk(target, minFrames, maxFrames);
      const cacheOk = isSegmentMp4PrimaryPath() ? ok : Boolean(muxVal.ok && ok);
      return { ok: cacheOk, frames: cachedFrames, targetFrames: target, minFrames };
    }
  }
  if (!forceProbe) {
    return { ok: false, frames: cachedFrames || {}, targetFrames: target, minFrames: 0 };
  }
  const frames = probeStationVideoFrameCounts(root);
  const muxKeys = activeMuxVideoKeys(root, { probeMp4: true });
  const vals = muxKeys.map((k) => Number(frames[k] || 0));
  if (vals.some((n) => n <= 0)) {
    return { ok: false, frames, targetFrames: target, minFrames: 0 };
  }
  const minFrames = Math.min(...vals);
  const maxFrames = Math.max(...vals);
  const ok = mp4ReadinessOk(target, minFrames, maxFrames);
  return { ok, frames, targetFrames: target, minFrames };
}

/**
 * Rebuild staging JPEGs from raw tar.zst (parquet/jsonl unchanged).
 * Used when full mux runs after incremental purge left staging empty.
 */
export async function rehydrateStagingFromRawSegments(stationId, sessionId) {
  const root = stationRoot(stationId);
  const rawDir = path.join(root, "raw", "segments", sessionId);
  if (!fs.existsSync(rawDir)) {
    throw new Error(`raw missing: ${rawDir}`);
  }
  const archives = fs
    .readdirSync(rawDir)
    .filter((f) => f.endsWith(".tar.zst"))
    .sort();
  if (!archives.length) {
    throw new Error(`no raw archives for session ${sessionId}`);
  }
  let stagedFrames = 0;
  const keys = ingestVideoKeys(root);
  for (const archive of archives) {
    const segmentId = archive.replace(/\.tar\.zst$/, "");
    const archivePath = path.join(rawDir, archive);
    const extractDir = path.join(
      root,
      ".upload",
      "extract",
      `rehydrate_${segmentId}_${Date.now()}_${crypto.randomBytes(3).toString("hex")}`,
    );
    try {
      await extractTarZstArchive(archivePath, extractDir);
      const body = buildSegmentBodyFromExtractedDir(extractDir, stationId);
      for (const f of body.frames) {
        const frameIndex = Number(f.frameIndex ?? f.frame_index ?? -1);
        if (!Number.isInteger(frameIndex) || frameIndex < 0) continue;
        const frameImages = {};
        for (const videoKey of keys) {
          const buf = resolveFrameImage(body.images, frameIndex, videoKey);
          if (buf) frameImages[videoKey] = buf;
        }
        if (Object.keys(frameImages).length > 0) {
          stageFrameImages(root, frameIndex, frameImages);
          stagedFrames += 1;
        }
      }
    } finally {
      try {
        fs.rmSync(extractDir, { recursive: true, force: true });
      } catch {
        /* ignore */
      }
    }
  }
  streamLog(stationId, "mux_staging_rehydrated", {
    sessionId,
    segments: archives.length,
    stagedFrames,
  });
  return { segments: archives.length, stagedFrames };
}

/** List frame_*.jpg range in a camera staging dir. */
function stagingFrameRange(inDir) {
  if (!fs.existsSync(inDir)) return null;
  let min = Infinity;
  let max = -1;
  let count = 0;
  for (const f of fs.readdirSync(inDir)) {
    const m = /^frame_(\d+)\.jpg$/.exec(f);
    if (!m) continue;
    const n = Number.parseInt(m[1], 10);
    if (!Number.isFinite(n)) continue;
    min = Math.min(min, n);
    max = Math.max(max, n);
    count += 1;
  }
  if (count === 0) return null;
  return { min, max, count };
}

/** Remove frame_*.jpg up to throughFrame (inclusive). */
function purgeStagingJpgsInDir(inDir, throughFrame = null) {
  if (!fs.existsSync(inDir)) return 0;
  let removed = 0;
  for (const f of fs.readdirSync(inDir)) {
    const m = /^frame_(\d+)\.jpg$/.exec(f);
    if (!m) continue;
    const idx = Number.parseInt(m[1], 10);
    if (throughFrame != null && idx > throughFrame) continue;
    try {
      fs.unlinkSync(path.join(inDir, f));
      removed += 1;
    } catch {
      /* ignore */
    }
  }
  return removed;
}

export function purgeAllStagingJpgs(root) {
  let removed = 0;
  const keys = new Set([...ingestVideoKeys(root), ...VIDEO_KEYS, ...LEGACY_VIDEO_KEYS]);
  for (const videoKey of keys) {
    removed += purgeStagingJpgsInDir(stagingDir(root, videoKey));
  }
  return removed;
}

function isMuxActiveForStation(stationId) {
  const muxState = muxQueue.get(stationId);
  if (muxState?.running) return true;
  return muxOnceWaiters.has(stationId);
}

function isDeriverLockActive(root) {
  const lockPath = path.join(root, "state", "deriver.lock");
  if (!fs.existsSync(lockPath)) return false;
  try {
    const lock = JSON.parse(fs.readFileSync(lockPath, "utf8"));
    if (!lock?.pid || lock.pid <= 0) return true;
    process.kill(lock.pid, 0);
    return true;
  } catch (err) {
    if (err?.code === "ESRCH") return false;
    return true;
  }
}

/** Block disk_cleanup staging purge until derive reaches READY (or no uploaded session). */
export function isStagingPurgeBlockedByDerive(root, stationId) {
  if (isDeriverLockActive(root)) return true;
  const sessionId = getActiveSessionId(root);
  if (!sessionId) return false;
  if (hasSessionMarker(root, sessionId, SESSION_MARKERS.DERIVING)) return true;
  const disk = computeDeriveStatusFromDisk(stationId, sessionId);
  if (disk.fullyReady) return false;
  const rawTotal = countRawTarZstForSession(root, sessionId);
  if (rawTotal > 0) return true;
  // Raw tar.zst may be purged after ingest; keep staging until MP4/parquet are READY.
  if (readJsonlFrameMetrics(root).count > 0) return true;
  if (hasUnreleasedStaging(root)) return true;
  return false;
}

/** True when any camera still has staging JPEGs on disk. */
function hasUnreleasedStaging(root) {
  const keys = new Set([...ingestVideoKeys(root), ...VIDEO_KEYS, ...LEGACY_VIDEO_KEYS]);
  for (const videoKey of keys) {
    if (stagingFrameRange(stagingDir(root, videoKey))) return true;
  }
  return false;
}

function purgeStagingWithMuxGuards(stationId, root, { emergency = false } = {}) {
  if (isMuxActiveForStation(stationId)) {
    streamLog(stationId, "staging_purge_skipped", {
      reason: "mux_active",
      emergency,
      stagingBytes: stagingDirSizeBytes(root),
    });
    return 0;
  }
  if (isStagingPurgeBlockedByDerive(root, stationId)) {
    streamLog(stationId, "staging_purge_skipped", {
      reason: "derive_not_ready",
      emergency,
      stagingBytes: stagingDirSizeBytes(root),
    });
    return 0;
  }
  if (hasUnreleasedStaging(root) && !emergency && !usesParquetVideoExport()) {
    streamLog(stationId, "staging_purge_skipped", {
      reason: "staging_present",
      emergency,
      stagingBytes: stagingDirSizeBytes(root),
    });
    return 0;
  }
  const stagingBytes = stagingDirSizeBytes(root);
  const removed = purgeAllStagingJpgs(root);
  if (removed > 0) {
    streamLog(stationId, emergency ? "staging_emergency_purge" : "staging_purge", {
      removedJpgs: removed,
      stagingBytes,
      thresholdBytes: emergency ? STAGING_EMERGENCY_PURGE_BYTES : undefined,
    });
  }
  return removed;
}

function stagingDirSizeBytes(root) {
  let total = 0;
  for (const videoKey of ingestVideoKeys(root)) {
    const inDir = stagingDir(root, videoKey);
    if (!fs.existsSync(inDir)) continue;
    for (const f of fs.readdirSync(inDir)) {
      if (!/^frame_\d+\.jpg$/.test(f)) continue;
      try {
        total += fs.statSync(path.join(inDir, f)).size;
      } catch {
        /* ignore */
      }
    }
  }
  return total;
}

function purgeUntilUnderQuota(stationId, root, activeSessionId, quotaBytes) {
  const targetBytes = Math.floor(quotaBytes * DISK_TARGET_RATIO);
  let passes = 0;
  let totalActions = 0;
  while (passes < 8) {
    passes += 1;
    let usage = cachedDiskUsageBytes(root);
    if (usage == null) {
      const hk = readDiskHousekeeping(root);
      usage =
        hk && typeof hk.usageBytes === "number"
          ? hk.usageBytes
          : stagingDirSizeBytes(root);
    }
    if (usage <= targetBytes) break;

    const stagingBytes = stagingDirSizeBytes(root);
    if (stagingBytes > 0) {
      const n = purgeStagingWithMuxGuards(stationId, root);
      totalActions += n;
      if (n <= 0) break;
      continue;
    }

    const archivesRemoved = purgeArchivesUntilUnderQuota(root, activeSessionId, quotaBytes);
    totalActions += archivesRemoved;
    if (archivesRemoved > 0) continue;

    purgeExpiredArchives(root, activeSessionId);
    purgeExpiredSessionMarkers(root, activeSessionId);
    break;
  }
  return totalActions;
}

function runDiskCleanup(stationId) {
  const root = stationRoot(stationId);
  if (!fs.existsSync(root)) return;
  withCleanupLock(root, () => {
    const activeSessionId = getActiveSessionId(root);
    const quotaBytes = stationQuotaBytes(stationId);
    const stagingBytes = stagingDirSizeBytes(root);
    if (stagingBytes >= STAGING_EMERGENCY_PURGE_BYTES) {
      purgeStagingWithMuxGuards(stationId, root, { emergency: true });
    }
    purgeExpiredArchives(root, activeSessionId);
    purgeExpiredSessionMarkers(root, activeSessionId);
    purgeUntilUnderQuota(stationId, root, activeSessionId, quotaBytes);
    const usageBytes = dirSizeBytes(root);
    writeJsonAtomic(path.join(root, "live", "disk-housekeeping.json"), {
      stationId,
      at: new Date().toISOString(),
      activeSessionId,
      usageBytes,
      quotaBytes,
      retentionDays: RETENTION_DAYS,
      windowMinutes: STREAM_WINDOW_MINUTES,
      segmentMinutes: STREAM_SEGMENT_MINUTES,
    });
    streamLog(stationId, "disk_cleanup", {
      sessionId: activeSessionId,
      usageBytes,
      quotaBytes,
      retentionDays: RETENTION_DAYS,
      windowMinutes: STREAM_WINDOW_MINUTES,
    });
  });
}

/** Startup / ops: purge legacy staging and enforce quota for every station dir. */
export function runDiskCleanupForAllStations() {
  if (!fs.existsSync(STREAM_ROOT)) return;
  for (const ent of fs.readdirSync(STREAM_ROOT, { withFileTypes: true })) {
    if (!ent.isDirectory()) continue;
    try {
      runDiskCleanup(ent.name);
    } catch (err) {
      console.warn(`[stream-ingest] cleanup failed station=${ent.name}:`, err?.message || err);
    }
  }
}

export function ensurePeriodicDiskCleanupForAllStations() {
  if (!fs.existsSync(STREAM_ROOT)) return;
  for (const ent of fs.readdirSync(STREAM_ROOT, { withFileTypes: true })) {
    if (ent.isDirectory()) ensurePeriodicDiskCleanup(ent.name);
  }
}

function scheduleDiskCleanup(stationId) {
  const prev = cleanupQueue.get(stationId);
  if (prev) clearTimeout(prev);
  cleanupQueue.set(
    stationId,
    setTimeout(() => {
      cleanupQueue.set(stationId, null);
      runDiskCleanup(stationId);
    }, CLEANUP_DEBOUNCE_MS),
  );
}

function ensurePeriodicDiskCleanup(stationId) {
  if (cleanupQueue.get(`${stationId}:interval`)) return;
  const timer = setInterval(() => runDiskCleanup(stationId), CLEANUP_INTERVAL_MS);
  cleanupQueue.set(`${stationId}:interval`, timer);
}

function frameCommittedMarker(root, sessionId, frameIndex) {
  return path.join(
    sessionPath(root, sessionId),
    "frames",
    `${String(frameIndex).padStart(8, "0")}.ok`,
  );
}

function isFrameCommitted(root, sessionId, frameIndex) {
  return fs.existsSync(frameCommittedMarker(root, sessionId, frameIndex));
}

function markFrameCommitted(root, sessionId, frameIndex) {
  const marker = frameCommittedMarker(root, sessionId, frameIndex);
  ensureDir(path.dirname(marker));
  fs.writeFileSync(marker, "");
}

function stagingDir(root, videoKey) {
  return path.join(root, "_staging", videoKey.replace(/\./g, "_"));
}

function videoOutPath(root, videoKey) {
  return path.join(root, "videos", videoKey, "chunk-000", "file-000.mp4");
}

function dataJsonlPath(root) {
  return path.join(root, "data", "chunk-000", "file-000.jsonl");
}

function decodeImage(b64) {
  if (!b64 || typeof b64 !== "string") return null;
  const raw = b64.includes(",") ? b64.split(",").pop() : b64;
  return Buffer.from(raw, "base64");
}

/** JPEG bytes from multipart file part or legacy base64 JSON field. */
function imageBuffer(images, videoKey) {
  const value = images?.[videoKey];
  if (!value) return null;
  if (Buffer.isBuffer(value)) return value;
  if (value instanceof Uint8Array) return Buffer.from(value);
  if (typeof value === "string") return decodeImage(value);
  return null;
}

function resolveFrameImage(images, frameIndex, videoKey) {
  const canonical = LEGACY_TO_CANONICAL[videoKey] || videoKey;
  const candidates = new Set([
    videoKey,
    canonical,
    ...(LEGACY_VIDEO_KEY_ALIASES[canonical] || []),
    ...(LEGACY_VIDEO_KEY_ALIASES[videoKey] || []),
  ]);
  for (const key of candidates) {
    const fk = segmentImageKey(frameIndex, key);
    const buf = imageBuffer(images, fk) || imageBuffer(images, key);
    if (buf) return buf;
  }
  return null;
}

function parseMultipartBody(buffer, boundary) {
  const delim = Buffer.from(`--${boundary}`);
  const parts = [];
  let start = buffer.indexOf(delim);
  while (start !== -1) {
    let partStart = start + delim.length;
    if (buffer[partStart] === 45 && buffer[partStart + 1] === 45) break;
    if (buffer[partStart] === 13 && buffer[partStart + 1] === 10) partStart += 2;
    const next = buffer.indexOf(delim, partStart);
    const partEnd = next === -1 ? buffer.length : next - 2;
    if (partEnd > partStart) parts.push(buffer.subarray(partStart, partEnd));
    start = next;
  }

  const fields = {};
  const files = {};
  for (const part of parts) {
    const headerEnd = part.indexOf("\r\n\r\n");
    if (headerEnd === -1) continue;
    const headerText = part.subarray(0, headerEnd).toString("utf8");
    const body = part.subarray(headerEnd + 4);
    const nameMatch = /name="([^"]+)"/i.exec(headerText);
    if (!nameMatch) continue;
    const name = nameMatch[1];
    const filenameMatch = /filename="([^"]*)"/i.exec(headerText);
    if (filenameMatch) {
      files[name] = body;
    } else {
      fields[name] = body.toString("utf8");
    }
  }

  let payload = {};
  if (fields.payload) {
    try {
      payload = JSON.parse(fields.payload);
    } catch {
      throw new Error("invalid multipart payload JSON");
    }
  }
  payload.images = { ...(payload.images || {}), ...files };
  return payload;
}

export function readStreamUploadBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      try {
        const buffer = Buffer.concat(chunks);
        const contentType = req.headers["content-type"] || "";
        if (!contentType.includes("multipart/form-data")) {
          const raw = buffer.toString("utf8");
          resolve(raw ? JSON.parse(raw) : {});
          return;
        }
        const match = /boundary=(?:"([^"]+)"|([^;\s]+))/i.exec(contentType);
        const boundary = match?.[1] || match?.[2];
        if (!boundary) {
          reject(new Error("multipart boundary missing"));
          return;
        }
        resolve(parseMultipartBody(buffer, boundary));
      } catch (e) {
        reject(e);
      }
    });
    req.on("error", reject);
  });
}

function headerValue(req, name) {
  const raw = req.headers[name] ?? req.headers[name.toLowerCase()];
  return typeof raw === "string" ? raw.trim() : "";
}

function isTarZstUploadRequest(req) {
  const contentType = (req.headers["content-type"] || "").toLowerCase();
  const protocol = headerValue(req, "x-upload-protocol").toLowerCase();
  return contentType.includes("application/zstd") || protocol === "tarzst";
}

function streamRequestToFile(req, destPath) {
  return new Promise((resolve, reject) => {
    ensureDir(path.dirname(destPath));
    const ws = createWriteStream(destPath);
    req.pipe(ws);
    ws.on("finish", resolve);
    ws.on("error", reject);
    req.on("error", reject);
  });
}

function sha256File(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash("sha256");
    const rs = fs.createReadStream(filePath);
    rs.on("data", (chunk) => hash.update(chunk));
    rs.on("end", () => resolve(hash.digest("hex")));
    rs.on("error", reject);
  });
}

function zstdCliAvailable() {
  for (const bin of ["/usr/bin/zstd", "/usr/local/bin/zstd"]) {
    try {
      fs.accessSync(bin, fs.constants.X_OK);
      return true;
    } catch {
      /* try next */
    }
  }
  return false;
}

/** Batch-1: legacy | python (tarfile + zstandard). Default legacy for gray rollout. */
export function deriveExtractBackend() {
  return String(process.env.DERIVE_EXTRACT_BACKEND || "legacy").trim().toLowerCase();
}

function shouldVerifyExtractSha256() {
  const v = String(process.env.DERIVE_EXTRACT_VERIFY_SHA256 ?? "1").trim().toLowerCase();
  return v !== "0" && v !== "false" && v !== "no";
}

function spawnExtractTarZstPython(archivePath, destDir, { expectedSha256 = null } = {}) {
  if (!fs.existsSync(EXTRACT_TAR_ZST_SCRIPT)) {
    throw new Error("extract-tar-zst.py missing");
  }
  const py = resolveParquetPython();
  if (!py) throw new Error("no_python_for_extract");
  const args = [EXTRACT_TAR_ZST_SCRIPT, archivePath, destDir];
  if (expectedSha256 && shouldVerifyExtractSha256()) {
    args.push("--expected-sha256", String(expectedSha256).toLowerCase());
  }
  const res = spawnSync(py, args, {
    ...parquetSpawnOptions({ encoding: "utf8" }),
    encoding: "utf8",
  });
  if (res.status !== 0) {
    let msg = String(res.stderr || res.stdout || "extract failed").trim();
    try {
      const lines = msg.split("\n").filter(Boolean);
      const parsed = JSON.parse(lines[lines.length - 1] || "{}");
      if (parsed.error) msg = parsed.error;
    } catch {
      /* keep raw */
    }
    throw new Error(msg.slice(0, 500));
  }
  return res.stdout;
}

/** Peek manifest.json from tar.zst (unified legacy/python backend). */
export function peekManifestFromTarZstArchive(archivePath) {
  if (deriveExtractBackend() === "python") {
    if (!fs.existsSync(EXTRACT_TAR_ZST_SCRIPT)) {
      throw new Error("extract-tar-zst.py missing");
    }
    const py = resolveParquetPython();
    if (!py) throw new Error("no_python_for_extract");
    const res = spawnSync(
      py,
      [EXTRACT_TAR_ZST_SCRIPT, archivePath, "--peek", "manifest.json"],
      {
        ...parquetSpawnOptions({ encoding: "utf8" }),
        encoding: "utf8",
      },
    );
    if (res.status !== 0) {
      throw new Error(String(res.stderr || res.stdout || "peek failed").slice(0, 300));
    }
    return JSON.parse(res.stdout);
  }
  return peekManifestFromTarZstLegacy(archivePath);
}

function peekManifestFromTarZstLegacy(archivePath) {
  return new Promise((resolve, reject) => {
    const zstd = spawn("zstd", ["-d", "-c", archivePath], { stdio: ["ignore", "pipe", "pipe"] });
    const tar = spawn("tar", ["-xO", "manifest.json"], { stdio: ["pipe", "pipe", "pipe"] });
    let err = "";
    let out = "";
    zstd.stderr.on("data", (d) => {
      err += d.toString();
    });
    tar.stderr.on("data", (d) => {
      err += d.toString();
    });
    tar.stdout.on("data", (d) => {
      out += d.toString();
    });
    zstd.stdout.pipe(tar.stdin);
    zstd.on("error", reject);
    tar.on("error", reject);
    tar.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`manifest peek failed: ${err.slice(0, 300)}`));
        return;
      }
      try {
        resolve(JSON.parse(out));
      } catch (e) {
        reject(e);
      }
    });
    zstd.on("close", (code) => {
      if (code !== 0) reject(new Error(`zstd peek failed: ${err.slice(0, 300)}`));
    });
  });
}

function extractTarBufferToDir(tarBuf, destDir) {
  return new Promise((resolve, reject) => {
    const tar = spawn("tar", ["-x", "-C", destDir], { stdio: ["pipe", "ignore", "pipe"] });
    let err = "";
    tar.stderr.on("data", (d) => {
      err += d.toString();
    });
    tar.on("error", reject);
    tar.stdin.write(tarBuf);
    tar.stdin.end();
    tar.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`tar extract failed: ${err.slice(0, 400)}`));
    });
  });
}

async function extractTarZstArchiveCli(archivePath, destDir) {
  const zstd = spawn("zstd", ["-d", "-c", archivePath], { stdio: ["ignore", "pipe", "pipe"] });
  const tar = spawn("tar", ["-x", "-C", destDir], { stdio: ["pipe", "ignore", "pipe"] });
  let err = "";
  zstd.stderr.on("data", (d) => {
    err += d.toString();
  });
  tar.stderr.on("data", (d) => {
    err += d.toString();
  });
  await new Promise((resolve, reject) => {
    zstd.stdout.pipe(tar.stdin);
    zstd.on("error", reject);
    tar.on("error", reject);
    tar.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`tar extract failed: ${err.slice(0, 400)}`));
    });
    zstd.on("close", (code) => {
      if (code !== 0) reject(new Error(`zstd decompress failed: ${err.slice(0, 400)}`));
    });
  });
}

async function extractTarZstArchiveJs(archivePath, destDir) {
  const compressed = await fs.promises.readFile(archivePath);
  const tarBuf = fzstdDecompress(compressed);
  await extractTarBufferToDir(Buffer.from(tarBuf.buffer, tarBuf.byteOffset, tarBuf.byteLength), destDir);
}

async function extractTarZstArchive(archivePath, destDir, { expectedSha256 = null } = {}) {
  if (deriveExtractBackend() === "python") {
    spawnExtractTarZstPython(archivePath, destDir, { expectedSha256 });
    return;
  }
  ensureDir(destDir);
  if (zstdCliAvailable()) {
    try {
      await extractTarZstArchiveCli(archivePath, destDir);
      return;
    } catch (err) {
      const msg = String(err?.message || err);
      if (!msg.includes("ENOENT") && err?.code !== "ENOENT") throw err;
    }
  }
  await extractTarZstArchiveJs(archivePath, destDir);
}

function markSegmentIngestError(root, sessionId, segmentId, error) {
  const errDir = path.join(root, "live", "sessions", sessionId, "ingest_errors");
  ensureDir(errDir);
  const errPath = path.join(errDir, `${segmentId}.json`);
  writeJsonAtomic(errPath, {
    sessionId,
    segmentId,
    error: String(error?.message || error),
    at: new Date().toISOString(),
  });
}

function buildSegmentBodyFromExtractedDir(extractDir, stationId = "unknown") {
  const manifestPath = path.join(extractDir, "manifest.json");
  const rowsPath = path.join(extractDir, "rows.jsonl");
  if (!fs.existsSync(manifestPath) || !fs.existsSync(rowsPath)) {
    throw new Error("extracted segment missing manifest.json or rows.jsonl");
  }
  assertSegmentMp4Archive(extractDir);
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  const episodeMeta = prepareSegmentEpisodeMeta(manifest, stationId);
  const rows = fs
    .readFileSync(rowsPath, "utf8")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
  const images = {};
  const frames = [];
  const hasSegmentMp4 = isSegmentMp4PrimaryPath() && segmentHasStreamMp4(extractDir);
  for (const row of rows) {
    const frameIndex = Number(row.frame_index ?? row.frameIndex ?? -1);
    if (!Number.isInteger(frameIndex) || frameIndex < 0) continue;
    if (!hasSegmentMp4) {
      const binPath = path.join(extractDir, "frames", `${String(frameIndex).padStart(8, "0")}.bin`);
      if (!fs.existsSync(binPath)) {
        throw new Error(`missing frame bin: ${binPath}`);
      }
      const cameraJpegs = unpackFrameBin(fs.readFileSync(binPath));
      for (const [videoKey, jpeg] of Object.entries(cameraJpegs)) {
        images[segmentImageKey(frameIndex, videoKey)] = jpeg;
      }
    }
    frames.push({
      frameIndex,
      timestampNs: row.timestamp_ns ?? row.timestampNs ?? 0,
      task: row.task,
      observationState: row["observation.state"] || row.observationState || [0, 0, 0, 0, 0, 0],
      observationPose: row["observation.pose"] || row.observationPose || [0, 0, 0, 0, 0, 0, 1],
      observationHands: row["observation.hands"] || row.observationHands || new Array(63).fill(0),
      actionVector: row.action || row.actionVector || [0],
    });
  }
  return {
    action: "segment",
    sessionId: manifest.session_id || manifest.sessionId,
    segmentId: manifest.segment_id || manifest.segmentId,
    startFrameIndex: Number(manifest.start_frame_index ?? manifest.startFrameIndex ?? 0),
    endFrameIndex: Number(manifest.end_frame_index ?? manifest.endFrameIndex ?? 0),
    frames,
    images,
    host: null,
    episodeMeta,
    extractDir,
    segmentMp4: hasSegmentMp4,
  };
}

async function ensureSessionForImport(stationId, body) {
  const root = stationRoot(stationId);
  if (isViewerScaffold(root)) {
    clearViewerScaffold(root, stationId);
  }
  const livePath = path.join(root, "live", "session.json");
  const live = readJson(livePath, {});
  const sessionId = body.sessionId;
  const sessionDir = sessionPath(root, sessionId);
  if (live.sessionId === sessionId && fs.existsSync(sessionDir)) {
    return;
  }
  if (fs.existsSync(sessionDir)) {
    return;
  }
  if (useSessionSingleEpisode(stationId) && stationHasDerivedData(root)) {
    ensureDir(sessionDir);
    ensureDir(path.join(sessionDir, "frames"));
    registerStreamSession(root, sessionId, {
      isResume: true,
      previousSessionId: live.sessionId || null,
    });
    writeJsonAtomic(livePath, {
      ...live,
      sessionId,
      task: body.frames[0]?.task || live.task,
      updatedAt: new Date().toISOString(),
    });
    streamLog(stationId, "session_register_append", {
      sessionId,
      previousSessionId: live.sessionId || null,
    });
    return;
  }
  const task = body.frames[0]?.task;
  const intrinsics = body.cameraIntrinsics && typeof body.cameraIntrinsics === "object"
    ? body.cameraIntrinsics
    : null;
  const videoShapes = {};
  for (const key of resolveVideoKeysForStation(stationId, { intrinsics })) {
    videoShapes[key] = [800, 1280];
  }
  await handleStreamUpload(stationId, {
    action: "session_start",
    sessionId,
    task,
    videoShapes,
    episodeMeta: body.episodeMeta,
    cameraIntrinsics: intrinsics,
  });
}

/**
 * Process a local tar.zst segment archive (same path as online tarzst upload).
 * @param {string} filePath absolute path to .tar.zst
 * @param {string} stationId
 * @param {{ expectedSha?: string, sessionId?: string, segmentId?: string, onStatus?: (s: string) => void }} [options]
 */
export async function processTarZstFromFile(filePath, stationId, options = {}) {
  const {
    expectedSha,
    sessionId: expectSessionId,
    segmentId: expectSegmentId,
    onStatus,
    ingestSource,
  } = options;
  const root = stationRoot(stationId);
  const archivePath = path.resolve(filePath);
  const extractRoot = path.join(root, ".upload", "extract");
  const extractDir = path.join(
    extractRoot,
    `import_${Date.now()}_${crypto.randomBytes(4).toString("hex")}`,
  );
  const t0 = Date.now();
  let body = null;
  try {
    onStatus?.("validating");
    const actualSha = await sha256File(archivePath);
    if (expectedSha && actualSha !== expectedSha.toLowerCase()) {
      throw new Error(`sha256 mismatch expected=${expectedSha.slice(0, 12)} actual=${actualSha.slice(0, 12)}`);
    }
    onStatus?.("extracting");
    await extractTarZstArchive(archivePath, extractDir, { expectedSha256: actualSha });
    body = buildSegmentBodyFromExtractedDir(extractDir, stationId);
    archiveSegmentTarZst(root, body.sessionId, body.segmentId, archivePath);
    if (expectSessionId && body.sessionId !== expectSessionId) {
      throw new Error("manifest session/segment mismatch with upload headers");
    }
    if (expectSegmentId && body.segmentId !== expectSegmentId) {
      throw new Error("manifest session/segment mismatch with upload headers");
    }
    ensureSessionForImport(stationId, body);
    onStatus?.("processing");
    body.ingestSource = ingestSource || (expectSessionId ? "edge" : "import");
    const out = await handleStreamUpload(stationId, body);
    streamLog(stationId, "tarzst_segment_ok", {
      sessionId: body.sessionId,
      segmentId: body.segmentId,
      bytes: fs.statSync(archivePath).size,
      elapsedMs: Date.now() - t0,
      frames: body.frames.length,
      source: expectSessionId ? "edge" : "import",
    });
    return { body, out };
  } catch (err) {
    const sid = body?.sessionId || expectSessionId;
    const seg = body?.segmentId || expectSegmentId;
    if (sid && seg) {
      markSegmentIngestError(root, sid, seg, err);
    }
    streamLog(stationId, "tarzst_segment_fail", {
      sessionId: sid,
      segmentId: seg,
      message: err?.message || String(err),
    });
    throw err;
  } finally {
    try {
      fs.rmSync(extractDir, { recursive: true, force: true });
    } catch {
      /* ignore */
    }
  }
}

async function handleTarZstSegmentUpload(stationId, req) {
  const sessionId = headerValue(req, "x-session-id");
  const segmentId = headerValue(req, "x-segment-id");
  const expectedSha = headerValue(req, "x-content-sha256").toLowerCase();
  const expectedSegmentTotal = Number(headerValue(req, "x-session-segment-total") || 0);
  if (!sessionId || !segmentId) {
    throw new Error("X-Session-Id and X-Segment-Id required for tarzst upload");
  }
  const root = stationRoot(stationId);

  if (isStationSegmentCommitted(stationId, sessionId, segmentId)) {
    streamLog(stationId, "tarzst_early_duplicate", { sessionId, segmentId, reason: "committed" });
    return {
      status: "derived",
      sessionId,
      segmentId,
      duplicate: true,
      sha256: expectedSha || null,
      deriveAsync: false,
      message: "segment already committed",
      framesCommitted: 0,
    };
  }

  const deriveAsyncMod = await import("./derive-async.mjs");
  if (deriveAsyncMod.isDeriveAsyncEnabled(stationId)) {
    const st = deriveAsyncMod.getSegmentState(root, sessionId, segmentId);
    const rawPath = path.join(root, "raw", "segments", sessionId, `${segmentId}.tar.zst`);
    if (st && ["verified", "deriving", "derived"].includes(st.status) && fs.existsSync(rawPath)) {
      streamLog(stationId, "tarzst_early_duplicate", {
        sessionId,
        segmentId,
        reason: "raw_present",
        status: st.status,
      });
      return {
        status: st.status,
        sessionId,
        segmentId,
        duplicate: true,
        sha256: st.sha256 || expectedSha || null,
        deriveAsync: true,
        message: "raw already stored",
        framesCommitted: 0,
      };
    }
  }

  const incomingDir = path.join(root, ".upload", "incoming");
  ensureDir(incomingDir);
  const archivePath = path.join(incomingDir, `${segmentId}_${Date.now()}.tar.zst`);
  try {
    await streamRequestToFile(req, archivePath);
    const deriveAsync = deriveAsyncMod;
    if (deriveAsync.isDeriveAsyncEnabled(stationId)) {
      const ack = await deriveAsync.acceptRawTarZstUpload(stationId, {
        archivePath,
        expectedSha,
        sessionId,
        segmentId,
        source: "edge",
        expectedSegmentTotal,
      });
      if (ack.job) deriveAsync.maybeEnqueueDeriveAfterUpload(stationId, ack);
      return {
        status: ack.status,
        sessionId: ack.sessionId,
        segmentId: ack.segmentId,
        duplicate: ack.duplicate,
        sha256: ack.sha256,
        bytes: ack.bytes,
        deriveAsync: true,
        message: ack.message,
        framesCommitted: 0,
      };
    }
    const { out } = await processTarZstFromFile(archivePath, stationId, {
      expectedSha,
      sessionId,
      segmentId,
    });
    return out;
  } finally {
    try {
      if (fs.existsSync(archivePath)) fs.rmSync(archivePath, { force: true });
    } catch {
      /* ignore */
    }
  }
}

export async function handleStreamUploadRequest(stationId, req) {
  const auth = verifyStationUploadToken(stationId, req);
  if (!auth.ok) {
    const err = new Error("unauthorized");
    err.statusCode = 401;
    err.reason = auth.reason;
    throw err;
  }
  if (isTarZstUploadRequest(req)) {
    return handleTarZstSegmentUpload(stationId, req);
  }
  const body = await readStreamUploadBody(req);
  return handleStreamUpload(stationId, body);
}

function scheduleMux(stationId) {
  if (isSegmentMp4PrimaryPath()) return;
  const state = muxQueue.get(stationId) || { timer: null, running: false };
  if (state.timer) clearTimeout(state.timer);
  state.timer = setTimeout(() => runMux(stationId), MUX_DEBOUNCE_MS);
  muxQueue.set(stationId, state);
}

function shouldInjectMuxFailure(videoKey) {
  const target = String(process.env.DERIVE_MUX_INJECT_FAIL_CAMERA || "").trim();
  if (!target || videoKey !== target) return false;
  const onceOn = String(process.env.DERIVE_MUX_INJECT_FAIL_ONCE ?? "1").trim() !== "0";
  if (onceOn && muxInjectFailedCameras.has(videoKey)) return false;
  if (onceOn) muxInjectFailedCameras.add(videoKey);
  return true;
}

function muxExecFailPayload(res) {
  if (!res || typeof res !== "object") return {};
  return {
    exitCode: res.exitCode ?? null,
    stderr: String(res.stderr || "").slice(0, 400) || undefined,
    backend: res.backend,
  };
}

function escapeConcatPath(p) {
  return String(p).replace(/'/g, "'\\''");
}

/** Sorted dataset frame indices present in staging within [fromIdx, toIdx]. */
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

/** Build ffconcat from explicit frame indices only (no gap hold-fill). */
function buildStagingConcatListFromIndices(inDir, fps, frameIndices) {
  if (!frameIndices.length) return null;
  const dt = 1 / fps;
  const lines = ["ffconcat version 1.0"];
  let lastPath = null;
  for (const idx of frameIndices) {
    const name = `frame_${String(idx).padStart(6, "0")}.jpg`;
    const full = path.join(inDir, name);
    if (!fs.existsSync(full)) continue;
    lastPath = full;
    lines.push(`file '${escapeConcatPath(full)}'`);
    lines.push(`duration ${dt}`);
  }
  if (!lastPath) return null;
  lines.push(`file '${escapeConcatPath(lastPath)}'`);
  return lines.join("\n");
}

function encodeStagingToMp4(inDir, frameIndices, fps, destPath) {
  const listContent = buildStagingConcatListFromIndices(inDir, fps, frameIndices);
  if (!listContent) return Promise.resolve({ ok: false, stderr: "empty_concat_list" });
  const listPath = `${destPath}.concat.txt`;
  writeFileAtomic(listPath, listContent);
  return encodeFromConcatList(listPath, destPath, { withScale: true, fps }).then((res) => {
    try {
      fs.unlinkSync(listPath);
    } catch {
      /* ignore */
    }
    return res;
  });
}

function probeMp4FrameCount(filePath) {
  return execProbeMp4FrameCount(filePath, { defaultFps: DEFAULT_FPS });
}

function muxOneCamera(stationId, root, videoKey, muxSessionId, { deferPurge = false } = {}) {
  const inDir = stagingDir(root, videoKey);
  const outFile = videoOutPath(root, videoKey);
  const range = stagingFrameRange(inDir);
  if (!range) return Promise.resolve({ ok: true, skipped: true, purgeThrough: -1 });

  if (shouldInjectMuxFailure(videoKey)) {
    streamLog(stationId, "mux_inject_fail", {
      sessionId: muxSessionId,
      videoKey,
      message: "DERIVE_MUX_INJECT_FAIL_CAMERA",
    });
    return Promise.resolve({ ok: false, purgeThrough: -1, injected: true });
  }

  return muxOneCameraFull(stationId, root, videoKey, muxSessionId, range, inDir, outFile);
}

function muxOneCameraFull(stationId, root, videoKey, muxSessionId, range, inDir, outFile) {
  const frameIndices = stagingFrameIndicesInRange(inDir, range.min, range.max);
  if (!frameIndices.length) {
    return Promise.resolve({ ok: true, skipped: true, purgeThrough: -1 });
  }
  const plan = muxRunPlan.get(stationId) || planMuxRemux(root);
  const rowTarget = plan.metrics?.rowCount || resolveDeriveFrameMetrics(root).rowCount;
  const preserveExisting = !plan.fullDatasetMux && fs.existsSync(outFile);
  for (const p of [
    `${outFile}.segment.tmp.mp4`,
    `${outFile}.muxing.tmp.mp4`,
    `${outFile}.muxing.tmp`,
    `${outFile}.concat.txt`,
    `${outFile}.increment.concat.txt`,
  ]) {
    try {
      if (fs.existsSync(p)) fs.unlinkSync(p);
    } catch {
      /* ignore */
    }
  }
  if (!preserveExisting) {
    try {
      if (fs.existsSync(outFile)) fs.unlinkSync(outFile);
    } catch {
      /* ignore */
    }
  }
  ensureDir(path.dirname(outFile));
  const tmpOut = `${outFile}.muxing.tmp.mp4`;
  return encodeStagingToMp4(inDir, frameIndices, DEFAULT_FPS, tmpOut).then(async (encRes) => {
    if (!encRes?.ok) {
      streamLog(stationId, "mux_fail", {
        sessionId: muxSessionId,
        videoKey,
        stage: "full_encode",
        mode: plan.fullDatasetMux ? "full" : "partial",
        ...muxExecFailPayload(encRes),
      });
      try {
        if (fs.existsSync(tmpOut)) fs.unlinkSync(tmpOut);
      } catch {
        /* ignore */
      }
      return { ok: false, purgeThrough: -1 };
    }
    const verifiedFrames = probeMp4FrameCount(tmpOut);
    const stagingFrames = frameIndices.length;
    const fullDatasetMux =
      plan.fullDatasetMux ||
      (rowTarget > 0 && stagingFrames >= rowTarget - 1 && stagingFrames <= rowTarget + 1);
    const targetFrames = fullDatasetMux ? rowTarget : stagingFrames;
    const minExpected = Math.max(targetFrames, 1) - 1;
    const okFrames =
      verifiedFrames >= minExpected ||
      (targetFrames > 0 &&
        verifiedFrames >= targetFrames - 1 &&
        verifiedFrames <= targetFrames + 1);
    if (!okFrames) {
      setChunkArtifactStatus(root, videoArtifactRel(videoKey), "writing", verifiedFrames);
      streamLog(stationId, "mux_fail", {
        sessionId: muxSessionId,
        videoKey,
        stage: "full_verify",
        mode: fullDatasetMux ? "full" : "partial",
        expectedMinFrames: minExpected,
        verifiedFrames,
        stagingFrames,
        datasetExpectedFrames: rowTarget,
      });
      try {
        if (fs.existsSync(tmpOut)) fs.unlinkSync(tmpOut);
      } catch {
        /* ignore */
      }
      return { ok: false, purgeThrough: -1 };
    }
    let publishedFrames = verifiedFrames;
    const incrementalAppend = !fullDatasetMux && preserveExisting;
    if (incrementalAppend) {
      const listPath = `${outFile}.increment.concat.txt`;
      const concatRes = await concatMp4Files(outFile, tmpOut, outFile, listPath);
      try {
        if (fs.existsSync(tmpOut)) fs.unlinkSync(tmpOut);
      } catch {
        /* ignore */
      }
      if (!concatRes?.ok) {
        streamLog(stationId, "mux_fail", {
          sessionId: muxSessionId,
          videoKey,
          stage: "incremental_concat",
          mode: "partial",
          ...muxExecFailPayload(concatRes),
        });
        return { ok: false, purgeThrough: -1 };
      }
      publishedFrames = probeMp4FrameCount(outFile);
    } else {
      try {
        if (fs.existsSync(outFile)) fs.unlinkSync(outFile);
      } catch {
        /* ignore */
      }
      fs.renameSync(tmpOut, outFile);
    }
    setChunkArtifactStatus(root, videoArtifactRel(videoKey), "finished", publishedFrames);
    streamLog(stationId, "mux_camera_done", {
      sessionId: muxSessionId,
      videoKey,
      frameMin: frameIndices[0],
      frameMax: frameIndices[frameIndices.length - 1],
      verifiedFrames: publishedFrames,
      segmentFrameCount: frameIndices.length,
      mode: fullDatasetMux ? "full" : "partial",
      incrementalAppend,
    });
    return { ok: true, removed: 0, purgeThrough: range.max, fullMux: fullDatasetMux };
  });
}

async function runMuxCamerasSerial(stationId, root, muxSessionId) {
  const videoKeys = ingestVideoKeys(root);
  const results = [];
  let purgeThrough = -1;
  for (const videoKey of videoKeys) {
    // eslint-disable-next-line no-await-in-loop
    const result = await muxOneCamera(stationId, root, videoKey, muxSessionId, { deferPurge: true });
    results.push(result);
    if (result.ok && !result.skipped && typeof result.purgeThrough === "number") {
      purgeThrough = Math.max(purgeThrough, result.purgeThrough);
    }
  }

  const allOk = results.every((r) => r.ok);
  const anyWorked = results.some((r) => r.ok && !r.skipped);
  if (allOk && anyWorked) {
    const removedTotal = purgeAllStagingJpgs(root);
    streamLog(stationId, "mux_staging_purged", {
      sessionId: muxSessionId,
      mode: "full",
      removedJpgs: removedTotal,
    });
  }
  return { allOk, anyWorked, purgeThrough };
}

function runMux(stationId) {
  if (isSegmentMp4PrimaryPath()) return;
  const state = muxQueue.get(stationId) || { timer: null, running: false };
  if (state.running) {
    state.timer = setTimeout(() => runMux(stationId), MUX_DEBOUNCE_MS);
    muxQueue.set(stationId, state);
    return;
  }

  const root = stationRoot(stationId);
  const plan = planMuxRemux(root);
  if (plan.stagingFrames <= 0) {
    streamLog(stationId, "mux_skip", { reason: "no_staging", frames: 0 });
    return;
  }

  state.running = true;
  muxQueue.set(stationId, state);

  let lockFd = null;
  const lockPath = path.join(locksDir(root), "mux.lock");
  try {
    lockFd = acquireFileLock(lockPath);
  } catch {
    state.running = false;
    state.timer = setTimeout(() => runMux(stationId), MUX_DEBOUNCE_MS);
    muxQueue.set(stationId, state);
    return;
  }

  muxRunPlan.set(stationId, plan);
  markMuxArtifactsWriting(root);
  const muxSessionId = getActiveSessionId(root);
  if (plan.fullDatasetMux) {
    resetMuxArtifactsForFullRemux(stationId, root);
  } else {
    resetMuxArtifactsForIncrementalRemux(stationId, root);
  }
  streamLog(stationId, "mux_start", {
    sessionId: muxSessionId,
    frames: plan.stagingFrames,
    mode: plan.fullDatasetMux ? "full" : "partial",
  });

  const jobs = runMuxCamerasSerial(stationId, root, muxSessionId);

  jobs
    .then(async (muxResult) => {
      if (muxResult?.allOk && muxResult?.anyWorked) {
        clearMuxRetryState(root);
        try {
          await writeMuxValidatedSnapshot(stationId, muxSessionId);
        } catch (err) {
          streamLog(stationId, "mux_snapshot_fail", {
            sessionId: muxSessionId,
            message: String(err?.message || err).slice(0, 200),
          });
        }
      } else if (!muxResult?.allOk) {
        maybeScheduleMuxRetry(stationId, muxSessionId);
      }
    })
    .finally(() => {
      muxRunPlan.delete(stationId);
      releaseFileLock(lockPath, lockFd);
      streamLog(stationId, "mux_done", { sessionId: muxSessionId, frames: countStagingFrames(root) });
    const s = muxQueue.get(stationId) || { timer: null, running: false };
    s.running = false;
    s.timer = null;
    muxQueue.set(stationId, s);
    scheduleViewerPublish(stationId);
    const muxWaiter = muxOnceWaiters.get(stationId);
    if (muxWaiter) {
      muxOnceWaiters.delete(stationId);
      writeMuxValidatedSnapshot(stationId, muxSessionId)
        .then((snap) => muxWaiter.resolve(snap))
        .catch((err) => muxWaiter.reject(err));
      scheduleDiskCleanup(stationId);
      return;
    }
    if (!shouldDeferSessionPublish(stationId)) {
      scheduleParquetSync(stationId);
    }
    scheduleDiskCleanup(stationId);
  });
}

function runParquetSync(stationId) {
  const root = stationRoot(stationId);
  if (isStationImportActive(stationId)) {
    scheduleParquetSync(stationId);
    return;
  }
  const ingestDepth = getSegmentIngestQueueDepth(stationId);
  if (ingestDepth.queued > 0 || ingestDepth.workerRunning) {
    scheduleParquetSync(stationId);
    return;
  }
  scheduleViewerPublish(stationId);

  if (!fs.existsSync(SYNC_SCRIPT)) {
    streamLog(stationId, "parquet_skip", { reason: "no_sync_script" });
    onSessionFinalizeParquetDone(stationId);
    return;
  }

  const py = resolveParquetPython();
  if (!py) {
    streamLog(stationId, "parquet_skip", { reason: "no_python" });
    onSessionFinalizeParquetDone(stationId);
    return;
  }

  let parquetFd = null;
  const parquetLock = path.join(locksDir(root), "parquet.lock");
  try {
    parquetFd = acquireFileLock(parquetLock, 120_000);
  } catch {
    scheduleParquetSync(stationId);
    return;
  }

  markParquetArtifactsWriting(root);
  const parquetSessionId = getActiveSessionId(root);
  streamLog(stationId, "parquet_start", { sessionId: parquetSessionId });

  const child = spawn(py, [SYNC_SCRIPT, root], parquetSpawnOptions());
  child.on("error", () => {
    releaseFileLock(parquetLock, parquetFd);
    runViewerPublish(stationId);
    onSessionFinalizeParquetDone(stationId);
  });
  child.on("close", (code) => {
    runViewerPublish(stationId);
    streamLog(stationId, "parquet_done", { sessionId: parquetSessionId, exitCode: code });
    releaseFileLock(parquetLock, parquetFd);
    onSessionFinalizeParquetDone(stationId);
  });
}

function scheduleParquetSync(stationId) {
  const prev = parquetQueue.get(stationId);
  if (prev) clearTimeout(prev);
  parquetQueue.set(
    stationId,
    setTimeout(() => {
      parquetQueue.set(stationId, null);
      runParquetSync(stationId);
    }, PARQUET_DEBOUNCE_MS),
  );
}

export function getStreamStatus(stationId) {
  if (mayMutateStreamFs()) {
    ensureStreamViewerScaffold(stationId);
    repairStreamViewerScaffold(stationId);
  }
  const root = stationRoot(stationId);
  const info = readJson(path.join(root, "meta", "info.json"), { total_frames: 0 });
  const chunks = readChunksManifest(root);
  const sessionFile = path.join(root, "live", "session.json");
  const live = readJson(sessionFile, {});
  // Never walk the full stream tree on HTTP hot paths (128GB+ can block server.mjs for minutes).
  const usageBytes = (() => {
    const fresh = cachedDiskUsageBytes(root);
    if (fresh != null) return fresh;
    const hk = readDiskHousekeeping(root);
    if (hk && typeof hk.usageBytes === "number") return hk.usageBytes;
    return 0;
  })();
  const jsonlRows = countJsonlRows(root);
  const ingestFrames = jsonlRows > 0 ? jsonlRows : Number(info.total_frames || 0);
  const quotaBytes = stationQuotaBytes(stationId);
  return {
    stationId,
    totalFrames: ingestFrames,
    ingestFrames,
    viewerRevision: chunks.revision ?? 0,
    sessionId: live.sessionId || null,
    updatedAt: live.updatedAt || null,
    datasetUrl: streamDatasetUrl(stationId),
    httpDatasetUrl: streamHttpDatasetUrl(stationId),
    diskUsageBytes: usageBytes,
    diskQuotaBytes: quotaBytes,
    diskUsagePercent: quotaBytes > 0 ? Math.round((usageBytes / quotaBytes) * 1000) / 10 : 0,
    retentionDays: RETENTION_DAYS,
  };
}

export async function handleStreamUpload(stationId, body) {
  const root = stationRoot(stationId);
  const action = body?.action;

  if (action === "heartbeat") {
    touchHeartbeat(root, stationId, body.host || null, {
      captureState: body.captureState,
    });
    const liveHb = readJson(path.join(root, "live", "session.json"), {});
    streamLog(stationId, "heartbeat", {
      sessionId: liveHb.sessionId,
      host: body.host || null,
      captureState: body.captureState || null,
    });
    return { ok: true, action: "heartbeat" };
  }

  if (action === "session_start") {
    const sessionId = body.sessionId || `sess_${Date.now()}`;
    const shapes = body.videoShapes || {};
    const intrinsics = body.cameraIntrinsics && typeof body.cameraIntrinsics === "object"
      ? body.cameraIntrinsics
      : null;
    const expectedVideoKeys = resolveVideoKeysForStation(stationId, { intrinsics, shapes });
    const livePath = path.join(root, "live", "session.json");
    const prevLive = readJson(livePath, {});
    const sessionDir = sessionPath(root, sessionId);
    let isResume = prevLive.sessionId === sessionId && fs.existsSync(sessionDir);
    if (
      !isResume &&
      useSessionSingleEpisode(stationId) &&
      stationHasDerivedData(root) &&
      prevLive.sessionId &&
      prevLive.sessionId !== sessionId
    ) {
      isResume = true;
    }
    ensureDir(root);
    registerStreamSession(root, sessionId, {
      isResume,
      previousSessionId: prevLive.sessionId || null,
    });
    ensurePeriodicDiskCleanup(stationId);
    const startedAt = prevLive.startedAt || new Date().toISOString();
    const task = resolveSessionTask(root, stationId, {
      explicit: body.task,
      sessionId,
      createdAt: startedAt,
    });
    let episodeMeta = null;
    if (!isResume) {
      episodeMeta = resolveEpisodeMeta(
        root,
        stationId,
        body.episodeMeta || parseManifestToEpisodeMeta(null, stationId),
      );
      writeJson(path.join(root, "meta", "info.json"), defaultInfo(stationId, shapes, episodeMeta, { intrinsics }));
      writeTasksJsonl(root, task);
      initChunksManifest(root, { resetViewer: true });
      writeViewerInfoSnapshot(root, 0);
      setChunkArtifactStatus(root, "meta/info.json", "finished", 0);
    } else if (infoVideoKeysMismatch(readJson(path.join(root, "meta", "info.json"), {}), expectedVideoKeys)) {
      const info = syncInfoVideoFeatures(
        readJson(path.join(root, "meta", "info.json"), {}),
        stationId,
        shapes,
        { intrinsics, episodeMeta },
      );
      writeJson(path.join(root, "meta", "info.json"), info);
      streamLog(stationId, "session_info_topology_sync", {
        sessionId,
        videoKeys: expectedVideoKeys,
      });
    }
    if (intrinsics && typeof intrinsics === "object") {
      writeJson(path.join(root, "meta", "camera_intrinsics.json"), intrinsics);
      setChunkArtifactStatus(root, "meta/camera_intrinsics.json", "finished", 0);
    } else if (!isResume) {
      const intrinsicsPath = path.join(root, "meta", "camera_intrinsics.json");
      if (fs.existsSync(intrinsicsPath)) {
        try {
          fs.unlinkSync(intrinsicsPath);
        } catch {
          /* ignore */
        }
      }
    }
    if (isResume && !fs.existsSync(chunksManifestPath(root))) {
      initChunksManifest(root, { resetViewer: false });
    }
    const info = readJson(path.join(root, "meta", "info.json"), defaultInfo(stationId, shapes, null, { intrinsics }));
    writeJson(livePath, {
      sessionId,
      startedAt: prevLive.startedAt || new Date().toISOString(),
      resumedAt: isResume ? new Date().toISOString() : null,
      task,
      episodeIndex: 0,
      lastFrameIndex: prevLive.lastFrameIndex ?? null,
    });
    ensureDir(sessionDir);
    ensureDir(path.join(sessionDir, "frames"));
    ensureDir(stagingTmpRoot(root));
    ensureDir(path.join(stagingTmpRoot(root), "inflight"));
    ensureDir(locksDir(root));
    const stagingKeys = isResume ? ingestVideoKeys(root) : expectedVideoKeys;
    for (const key of stagingKeys) {
      ensureDir(stagingDir(root, key));
    }
    ensureDir(path.dirname(dataJsonlPath(root)));
    streamLog(stationId, "session_start", { sessionId, resumed: isResume });
    return {
      ok: true,
      action: "session_start",
      sessionId,
      resumed: isResume,
      totalFrames: info.total_frames || 0,
      datasetUrl: streamDatasetUrl(stationId),
    };
  }

  if (action === "segment") {
    const sessionId = body.sessionId;
    const segmentId = body.segmentId;
    if (!sessionId || !segmentId) {
      throw new Error("sessionId and segmentId required for segment upload");
    }
    const frames = Array.isArray(body.frames) ? body.frames : [];
    const images = body.images || {};
    const shapes = body.videoShapes || {};
    const root = stationRoot(stationId);
    const live = readJson(path.join(root, "live", "session.json"), {});

    if (isSegmentCommitted(root, sessionId, segmentId)) {
      touchHeartbeat(root, stationId, live.host || null);
      const info = readJson(path.join(root, "meta", "info.json"), {});
      return {
        ok: true,
        action: "segment",
        sessionId,
        segmentId,
        framesCommitted: 0,
        duplicate: true,
        totalFrames: info?.total_frames ?? 0,
      };
    }

    const livePath = path.join(root, "live", "session.json");
    live.sessionId = sessionId;
    live.updatedAt = new Date().toISOString();
    writeJson(livePath, live);
    touchHeartbeat(root, stationId, body.host || live.host || null);

    const ingestJob = {
      sessionId,
      segmentId,
      frames,
      images,
      shapes,
      host: live.host || null,
      ingestSource: body.ingestSource || "stream",
      episodeMeta: body.episodeMeta || null,
      deferSessionPublish: shouldDeferSessionPublish(stationId),
      extractDir: body.extractDir || null,
      segmentMp4: Boolean(body.segmentMp4),
    };

    if (body.ingestSource === "import" || body.ingestSource === "edge") {
      const result = await enqueueSegmentIngestAwait(stationId, ingestJob);
      if (!result.duplicate && !isSegmentCommitted(root, sessionId, segmentId)) {
        throw new Error(`segment commit incomplete: ${segmentId}`);
      }
      const validation = validateSegmentDerived(
        stationId,
        sessionId,
        segmentId,
        frames.length,
      );
      if (!result.duplicate && !validation.ok) {
        throw new Error(
          `segment derive validation failed: ${segmentId} (${validation.reason})`,
        );
      }
      return {
        ok: true,
        action: "segment",
        sessionId,
        segmentId,
        framesCommitted: result.framesCommitted,
        duplicate: Boolean(result.duplicate),
        totalFrames:
          result.totalFrames ?? readJson(path.join(root, "meta", "info.json"), {})?.total_frames ?? 0,
        ready: validation.ok,
      };
    }

    enqueueSegmentIngest(stationId, ingestJob);

    const optimisticTotal = Math.max(
      Number(body.endFrameIndex ?? 0) + 1,
      live.lastFrameIndex ?? -1,
    );
    return {
      ok: true,
      action: "segment",
      sessionId,
      segmentId,
      accepted: true,
      framesCommitted: frames.length,
      totalFrames: optimisticTotal,
      duplicate: false,
    };
  }

  if (action === "frame") {
    if (!isStreamFramePushEnabled()) {
      streamLog(stationId, "frame_push_rejected", {
        sessionId: body.sessionId,
        frameIndex: body.frameIndex,
        reason: "STREAM_FRAME_PUSH=0",
      });
      throw new Error("frame_push disabled; use tar.zst segment upload");
    }
    if (isStationImportActive(stationId)) {
      enqueueDeferredStreamFrame(stationId, body);
      const frameIndex = Number(body.frameIndex ?? 0);
      return {
        ok: true,
        action: "frame",
        frameIndex,
        deferred: true,
        accepted: true,
        duplicate: false,
      };
    }
    const sessionId = body.sessionId;
    if (!sessionId) throw new Error("sessionId required");
    const frameIndex = Number(body.frameIndex ?? 0);
    if (!Number.isInteger(frameIndex) || frameIndex < 0) {
      throw new Error("frameIndex must be a non-negative integer");
    }
    const images = body.images || {};
    const shapes = body.videoShapes || {};

    let info = readJson(path.join(root, "meta", "info.json"));
    const livePath = path.join(root, "live", "session.json");
    const live = readJson(livePath, {});

    if (isFrameCommitted(root, sessionId, frameIndex)) {
      touchHeartbeat(root, stationId, live.host || null);
      if (frameIndex % 30 === 0) {
        streamLog(stationId, "frame_duplicate", { sessionId, frameIndex });
      }
      return {
        ok: true,
        action: "frame",
        frameIndex,
        totalFrames: info?.total_frames ?? frameIndex + 1,
        duplicate: true,
      };
    }
    if (!info) {
      info = defaultInfo(stationId, shapes);
    }
    for (const [key, shape] of Object.entries(shapes)) {
      if (info.features?.[key] && Array.isArray(shape) && shape.length >= 2) {
        info.features[key].shape = [shape[0], shape[1], 3];
        info.features[key].info["video.height"] = shape[0];
        info.features[key].info["video.width"] = shape[1];
      }
    }

    const row = {
      frame_index: frameIndex,
      timestamp_ns: body.timestampNs ?? 0,
      task: frameTaskValue(root, stationId, body.task),
      "observation.state": body.observationState || [0, 0, 0, 0, 0, 0],
      "observation.pose": body.observationPose || [0, 0, 0, 0, 0, 0, 1],
      "observation.hands": body.observationHands || new Array(63).fill(0),
      action: body.actionVector || [0],
    };
    const optimisticTotal = Math.max(info?.total_frames || 0, frameIndex + 1, live.lastFrameIndex ?? -1);
    live.sessionId = sessionId;
    live.updatedAt = new Date().toISOString();
    live.lastFrameIndex = Math.max(live.lastFrameIndex ?? -1, frameIndex);
    writeJson(livePath, live);
    touchHeartbeat(root, stationId, live.host || null);

    enqueueSegmentIngest(stationId, {
      sessionId,
      segmentId: `frame_${String(frameIndex).padStart(8, "0")}`,
      frames: [
        {
          frameIndex,
          timestampNs: body.timestampNs ?? 0,
          task: frameTaskValue(root, stationId, body.task),
          observationState: body.observationState || [0, 0, 0, 0, 0, 0],
          observationPose: body.observationPose || [0, 0, 0, 0, 0, 0, 1],
          observationHands: body.observationHands || new Array(63).fill(0),
          actionVector: body.actionVector || [0],
        },
      ],
      images,
      shapes,
      host: live.host || null,
    });

    return {
      ok: true,
      action: "frame",
      frameIndex,
      totalFrames: optimisticTotal,
      duplicate: false,
      accepted: true,
    };
  }

  throw new Error(`unknown action: ${action}`);
}

function hasChunksManifest(root) {
  return fs.existsSync(chunksManifestPath(root));
}

/** Empty LeRobot v3 skeleton so collection embed opens the viewer chrome before first import. */
export function ensureStreamViewerScaffold(stationId) {
  const root = stationRoot(stationId);
  if (!fs.existsSync(root)) return false;
  const infoPath = path.join(root, "meta", "info.json");
  if (fs.existsSync(infoPath)) return false;

  ensureDir(path.join(root, "meta"));
  const info = defaultInfo(stationId, {});
  info.total_frames = 1;
  info.total_episodes = 1;
  info.splits = { train: "0:1" };
  writeJsonAtomic(infoPath, info);
  writeTasksJsonl(root);
  saveEpisodesIndex(root, { version: 1, episodes: [] });
  initChunksManifest(root, { resetViewer: true });

  const py = resolveParquetPython();
  if (py && fs.existsSync(SYNC_SCRIPT)) {
    spawnSync(py, [SYNC_SCRIPT, "--viewer-scaffold", root], parquetSpawnOptions());
  }
  writeViewerScaffoldSnapshot(root, 1);
  finalizeViewerScaffoldArtifacts(root);
  streamLog(stationId, "viewer_scaffold", { total_frames: 1 });
  return true;
}

export async function ensureStreamViewerScaffoldAsync(stationId) {
  const root = stationRoot(stationId);
  if (!fs.existsSync(root)) return false;
  const infoPath = path.join(root, "meta", "info.json");
  if (fs.existsSync(infoPath)) return false;

  ensureDir(path.join(root, "meta"));
  const info = defaultInfo(stationId, {});
  info.total_frames = 1;
  info.total_episodes = 1;
  info.splits = { train: "0:1" };
  writeJsonAtomic(infoPath, info);
  writeTasksJsonl(root);
  saveEpisodesIndex(root, { version: 1, episodes: [] });
  initChunksManifest(root, { resetViewer: true });

  await runViewerScaffoldPythonAsync(root);
  writeViewerScaffoldSnapshot(root, 1);
  finalizeViewerScaffoldArtifacts(root);
  streamLog(stationId, "viewer_scaffold", { total_frames: 1, async: true });
  return true;
}

/** @deprecated Use scheduleStreamIngestBackgroundStartup(); kept for manual/ops calls. */
export function ensureStreamViewerScaffoldForAllStations() {
  if (!mayMutateStreamFs()) return;
  void (async () => {
    for (const stationId of listStreamStationIds()) {
      try {
        await ensureStreamViewerScaffoldAsync(stationId);
        await repairStreamViewerScaffoldAsync(stationId);
      } catch {
        /* ignore per-station scaffold errors */
      }
      await new Promise((resolve) => setImmediate(resolve));
    }
  })();
}

export function handleStreamScaffoldRequest(stationId) {
  if (!mayMutateStreamFs()) {
    return { ok: false, reason: "read_only_process" };
  }
  const root = stationRoot(stationId);
  if (!fs.existsSync(root)) {
    fs.mkdirSync(root, { recursive: true });
  }
  const created = ensureStreamViewerScaffold(stationId);
  repairStreamViewerScaffold(stationId);
  return { ok: true, stationId, created };
}

const STREAM_INGEST_INTERNAL_URL =
  process.env.STREAM_INGEST_INTERNAL_URL || "http://stream-ingest:7862";

/** Ensure meta/info.json exists; lerobot (root) delegates creation to stream-ingest. */
export async function ensureStreamScaffoldAvailable(stationId) {
  const infoPath = path.join(stationRoot(stationId), "meta", "info.json");
  if (fs.existsSync(infoPath)) return true;
  if (mayMutateStreamFs()) {
    ensureStreamViewerScaffold(stationId);
    repairStreamViewerScaffold(stationId);
    return fs.existsSync(infoPath);
  }
  try {
    const url = `${STREAM_INGEST_INTERNAL_URL}/lerobot/api/stream/${encodeURIComponent(stationId)}/scaffold`;
    const res = await fetch(url, { method: "GET", signal: AbortSignal.timeout(60_000) });
    if (!res.ok) return false;
    return fs.existsSync(infoPath);
  } catch {
    return false;
  }
}

export function resolveStreamFile(stationId, urlPath) {
  const root = stationRoot(stationId);
  const rel = urlPath.replace(/^\/+/, "");
  const norm = rel.replace(/\\/g, "/");
  if (!norm || norm === "meta/info.json") {
    if (mayMutateStreamFs()) {
      ensureStreamViewerScaffold(stationId);
      repairStreamViewerScaffold(stationId);
    }
  }
  if (!isPublishedPath(norm)) return null;

  if (!hasChunksManifest(root)) {
    const disk = path.normalize(path.join(root, norm));
    if (!disk.startsWith(root)) return null;
    if (!fs.existsSync(disk) || !fs.statSync(disk).isFile()) return null;
    return disk;
  }

  if (norm === "meta/info.json") {
    const canonicalPath = path.join(root, "meta", "info.json");
    const viewer = viewerInfoPath(root);
    if (fs.existsSync(canonicalPath) && fs.statSync(canonicalPath).isFile()) {
      const canonical = readJson(canonicalPath, {});
      const viewerFrames =
        fs.existsSync(viewer) && fs.statSync(viewer).isFile()
          ? readJson(viewer, {}).total_frames || 0
          : 0;
      if ((canonical.total_frames || 0) > 0 && viewerFrames < 1) {
        if (!canServeChunkArtifact(root, "meta/info.json")) {
          return canonicalPath;
        }
        writeViewerInfoSnapshot(root, canonical.total_frames);
      }
    }
    if (!canServeChunkArtifact(root, "meta/info.json")) return null;
    if (!fs.existsSync(viewer) || !fs.statSync(viewer).isFile()) {
      if (fs.existsSync(canonicalPath) && fs.statSync(canonicalPath).isFile()) {
        return canonicalPath;
      }
      return null;
    }
    return viewer;
  }

  if (isStreamChunkArtifact(norm)) {
    if (!canServeChunkArtifact(root, norm)) return null;
    const disk = artifactDiskPath(root, norm);
    if (!disk.startsWith(root)) return null;
    if (!fs.existsSync(disk) || !fs.statSync(disk).isFile()) return null;
    return disk;
  }

  const disk = path.normalize(path.join(root, norm));
  if (!disk.startsWith(root)) return null;
  if (!fs.existsSync(disk) || !fs.statSync(disk).isFile()) return null;
  return disk;
}

/** Ops hook: refresh viewer snapshot + chunk publish flags without waiting for parquet. */
export function publishStreamViewer(stationId) {
  runViewerPublish(stationId);
}

/** Rebuild episodes parquet + viewer snapshot after import or segment commit. */
export function refreshStreamEpisodesCatalog(stationId) {
  const root = stationRoot(stationId);
  syncInfoEpisodeCount(root);
  if (shouldDeferSessionPublish(stationId)) {
    publishSessionProgressive(stationId);
    return;
  }
  scheduleMux(stationId);
  runParquetSync(stationId);
}

/** Resume video mux for stations that still have staging jpgs or unfinished mp4 publish flags. */
export function resumePendingStreamMuxForAllStations() {
  if (!fs.existsSync(STREAM_ROOT)) return;
  for (const stationId of fs.readdirSync(STREAM_ROOT)) {
    const root = stationRoot(stationId);
    if (!fs.existsSync(path.join(root, "meta", "info.json"))) continue;
    if (shouldDeferSessionPublish(stationId)) continue;
    const manifest = readChunksManifest(root);
    const videosPending = Object.entries(manifest.publish || {}).some(
      ([rel, st]) => rel.startsWith("videos/") && st?.status !== "finished",
    );
    const hasStaging = stagingJpegCount(root) > 0;
    if (videosPending || hasStaging) {
      scheduleMux(stationId);
    }
  }
}
