/**
 * Live LeRobot v3 stream ingest for collection stations (meta + data + videos only).
 */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
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
  attachEpisodeMetaToIndexEntry,
  bootstrapDatasetSchema,
  buildCanonicalFrameRow,
  mergeEpisodeMeta,
  parseManifestToEpisodeMeta,
  prepareSegmentEpisodeMeta,
} from "./lerobot-converter.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const STREAM_ROOT = process.env.STREAM_DATA_ROOT || "/srv/stream";

const VIDEO_KEYS = [
  "observation.images.camera_front_left",
  "observation.images.camera_front_right",
  "observation.images.camera_rear_left",
  "observation.images.camera_rear_right",
];

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
  "observation.images.camera_rear_right": ["observation.images.camera_02"],
};

const LEGACY_TO_CANONICAL = Object.fromEntries(
  Object.entries(LEGACY_VIDEO_KEY_ALIASES).map(([canonical, legacyList]) => [
    legacyList[0],
    canonical,
  ]),
);

/** New sessions use VIDEO_KEYS; resumed sessions keep keys from existing meta/info.json. */
function ingestVideoKeys(root) {
  const info = readJson(path.join(root, "meta", "info.json"), {});
  const feats = info?.features || {};
  if (feats["observation.images.camera_head_left"]) {
    return LEGACY_VIDEO_KEYS;
  }
  return VIDEO_KEYS;
}

const DEFAULT_FPS = Number(process.env.STREAM_MUX_FPS || 20);
/** Scheme A: background mux/parquet/viewer — never on ingest hot path. */
const STREAM_BACKGROUND_BATCH_MS = Number(process.env.STREAM_BACKGROUND_BATCH_MS || 2000);
const MUX_DEBOUNCE_MS = STREAM_BACKGROUND_BATCH_MS;
const PARQUET_DEBOUNCE_MS = STREAM_BACKGROUND_BATCH_MS;
const VIEWER_PUBLISH_DEBOUNCE_MS = STREAM_BACKGROUND_BATCH_MS;
const SEGMENT_INGEST_BATCH_SIZE = Number(process.env.STREAM_SEGMENT_INGEST_BATCH_SIZE || 2);
/** Heartbeat interval on edge is ~15s; TTL must survive slow ingest (segment upload). */
const HEARTBEAT_TTL_MS = Number(process.env.STREAM_HEARTBEAT_TTL_MS || 120_000);
const SYNC_SCRIPT = path.join(__dirname, "scripts", "sync-stream-parquet.py");
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

function streamLog(stationId, event, fields = {}) {
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

function runViewerPublish(stationId) {
  const root = stationRoot(stationId);
  if (!fs.existsSync(root)) return;
  if (countStagingFrames(root) <= 0 && !fs.existsSync(viewerInfoPath(root))) return;
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

function finalizePublishCycle(root, stationId) {
  const info = readJson(path.join(root, "meta", "info.json"), {});
  const staged = countStagingFrames(root);
  const viewerFrames = Math.max(0, Math.min(staged, info.total_frames || staged));
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
    if (fs.existsSync(disk) && fs.statSync(disk).isFile()) {
      manifest.publish[rel] = { status: "finished", frames: viewerFrames, updatedAt: now };
    }
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

function commitFrameAtomically(root, frameIndex, images, row) {
  const inflight = frameInflightDir(root, frameIndex);
  ensureDir(inflight);

  const stagedImages = [];
  for (const videoKey of VIDEO_KEYS) {
    const buf = imageBuffer(images, videoKey);
    if (!buf) continue;
    const safe = videoKey.replace(/\./g, "_");
    const tmpImage = path.join(inflight, `${safe}.jpg`);
    writeFileAtomic(tmpImage, buf);
    stagedImages.push({ videoKey, tmpImage });
  }

  const rowTmp = path.join(inflight, "row.jsonl");
  writeFileAtomic(rowTmp, `${JSON.stringify(row)}\n`);

  for (const { videoKey, tmpImage } of stagedImages) {
    const finalPath = path.join(
      stagingDir(root, videoKey),
      `frame_${String(frameIndex).padStart(6, "0")}.jpg`,
    );
    ensureDir(path.dirname(finalPath));
    fs.renameSync(tmpImage, finalPath);
  }

  withFileLock(root, "jsonl", () => {
    const jsonl = dataJsonlPath(root);
    ensureDir(path.dirname(jsonl));
    fs.appendFileSync(jsonl, fs.readFileSync(rowTmp));
  });

  fs.rmSync(inflight, { recursive: true, force: true });
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
  let explicit = opts.explicit;
  if (explicit === undefined && live.task && !isLegacyPlaceholderTask(live.task)) {
    explicit = live.task;
  }
  return resolveTaskName({
    explicit,
    stationId: sid,
    sessionId: opts.sessionId || live.sessionId,
    createdAt: opts.createdAt || live.startedAt,
  });
}

/** Base task label from session metadata (auto-generated when unset). */
function getStationTask(root, stationId) {
  return resolveSessionTask(root, stationId);
}

/** LeRobot sidebar line 3: tasks[0] (lines 1–2 are #index and duration). */
function formatEpisodeListTask(ep, fullTask) {
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

function registerSegmentEpisode(root, { sessionId, segmentId, fromFrame, toFrame, source }) {
  const fps = Number(readJson(path.join(root, "meta", "info.json"), {}).fps || DEFAULT_FPS);
  const fromIdx = Math.max(0, Number(fromFrame));
  const toIdx = Math.max(fromIdx + 1, Number(toFrame));
  const length = toIdx - fromIdx;
  if (!Number.isFinite(length) || length <= 0) return null;

  const index = loadEpisodesIndex(root);
  if (index.episodes.some((ep) => ep.segment_id === segmentId)) {
    return index.episodes.find((ep) => ep.segment_id === segmentId);
  }

  ensureLegacyEpisodeSlot(root, index);
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
  ensureLegacyEpisodeSlot(root, index);
  saveEpisodesIndex(root, index);
  const infoPath = path.join(root, "meta", "info.json");
  const info = readJson(infoPath, null);
  if (!info) return;
  info.total_episodes = Math.max(1, index.episodes.length);
  writeJsonAtomic(infoPath, info);
}

function segmentImageKey(frameIndex, videoKey) {
  return `${frameIndex}__${videoKey.replace(/\./g, "_")}`;
}

function scheduleBackgroundTasks(stationId) {
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
    scheduleMux(stationId);
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

function processSegmentIngestJob(stationId, job) {
  const root = stationRoot(stationId);
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

  let committed = 0;
  let maxFrame = info.total_frames || 0;
  let minFrame = null;
  let maxCommittedFrame = null;
  for (const f of frames) {
    const frameIndex = Number(f.frameIndex ?? f.frame_index ?? -1);
    if (!Number.isInteger(frameIndex) || frameIndex < 0) continue;
    if (isFrameCommitted(root, sessionId, frameIndex)) continue;

    const frameImages = {};
    const keys = ingestVideoKeys(root);
    for (const videoKey of keys) {
      const buf = resolveFrameImage(images, frameIndex, videoKey);
      if (buf) frameImages[videoKey] = buf;
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
    commitFrameAtomically(root, frameIndex, frameImages, row);
    markFrameCommitted(root, sessionId, frameIndex);
    committed += 1;
    maxFrame = Math.max(maxFrame, frameIndex + 1);
    minFrame = minFrame === null ? frameIndex : Math.min(minFrame, frameIndex);
    maxCommittedFrame = maxCommittedFrame === null ? frameIndex : Math.max(maxCommittedFrame, frameIndex);
  }

  info.total_frames = maxFrame;
  syncInfoEpisodeCount(root);
  writeJsonAtomic(path.join(root, "meta", "info.json"), info);

  live.sessionId = sessionId;
  live.updatedAt = new Date().toISOString();
  live.lastFrameIndex = Math.max(live.lastFrameIndex ?? -1, maxFrame - 1);
  writeJson(livePath, live);

  markSegmentCommitted(root, sessionId, segmentId);
  if (committed > 0 && minFrame !== null && maxCommittedFrame !== null) {
    registerSegmentEpisode(root, {
      sessionId,
      segmentId,
      fromFrame: minFrame,
      toFrame: maxCommittedFrame + 1,
      source: job.ingestSource || "stream",
    });
    syncInfoEpisodeCount(root);
    scheduleParquetSync(stationId);
  }
  touchHeartbeat(root, stationId, host || live.host || null);

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
  setImmediate(() => {
    let n = 0;
    while (state.queue.length > 0 && n < SEGMENT_INGEST_BATCH_SIZE) {
      const job = state.queue.shift();
      const t0 = Date.now();
      try {
        processSegmentIngestJob(stationId, job);
        const ms = Date.now() - t0;
        streamLog(stationId, "segment_commit_ms", {
          sessionId: job.sessionId,
          segmentId: job.segmentId,
          commitMs: ms,
          ingestDepth: state.queue.length,
        });
      } catch (err) {
        streamLog(stationId, "segment_commit_error", {
          segmentId: job.segmentId,
          message: String(err?.message || err),
        });
      }
      n += 1;
    }
    state.workerRunning = false;
    if (state.queue.length > 0) {
      pumpSegmentIngestQueue(stationId);
    } else {
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

function touchHeartbeat(root, stationId, host) {
  const liveDir = path.join(root, "live");
  ensureDir(liveDir);
  const prev = readJson(path.join(liveDir, "heartbeat.json"), {});
  writeJson(path.join(liveDir, "heartbeat.json"), {
    stationId,
    at: new Date().toISOString(),
    host: host ?? prev.host ?? null,
  });
  markStationLiveCache(stationId, true);
}

function defaultInfo(stationId, shapes, episodeMeta = null) {
  const features = {};
  for (const key of VIDEO_KEYS) {
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

function archiveLiveDataForSession(root, sessionId) {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const dest = path.join(archiveRoot(root), `${sessionId}_${stamp}`);
  ensureDir(dest);
  const moveNames = ["_staging", "data", "videos", "meta"];
  for (const name of moveNames) {
    const src = path.join(root, name);
    if (!fs.existsSync(src)) continue;
    try {
      fs.renameSync(src, path.join(dest, name));
    } catch {
      /* ignore */
    }
  }
  const sessSrc = sessionPath(root, sessionId);
  if (fs.existsSync(sessSrc)) {
    ensureDir(path.join(dest, "sessions"));
    try {
      fs.renameSync(sessSrc, path.join(dest, "sessions", sessionId));
    } catch {
      /* ignore */
    }
  }
  const readyDir = path.join(dest, "meta");
  ensureDir(readyDir);
  const readyPath = path.join(readyDir, ".ready");
  try {
    fs.writeFileSync(
      readyPath,
      JSON.stringify(
        {
          sessionId,
          archivedAt: new Date().toISOString(),
          schema: "ego_archive_ready_v1",
        },
        null,
        2,
      ) + "\n",
      "utf8",
    );
  } catch {
    /* ignore */
  }
  return dest;
}

function registerStreamSession(root, sessionId, { isResume, previousSessionId }) {
  const registry = readSessionRegistry(root);
  if (
    previousSessionId &&
    previousSessionId !== sessionId &&
    !isResume
  ) {
    const prev = registry.sessions[previousSessionId] || {};
    prev.endedAt = new Date().toISOString();
    try {
      prev.archiveDir = archiveLiveDataForSession(root, previousSessionId);
    } catch {
      prev.archiveDir = prev.archiveDir || null;
    }
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

function readMuxState(root) {
  return readJson(muxStatePath(root), { cameras: {} });
}

function writeMuxState(root, state) {
  writeJsonAtomic(muxStatePath(root), state);
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

function purgeAllStagingJpgs(root) {
  let removed = 0;
  const keys = new Set([...ingestVideoKeys(root), ...VIDEO_KEYS, ...LEGACY_VIDEO_KEYS]);
  for (const videoKey of keys) {
    removed += purgeStagingJpgsInDir(stagingDir(root, videoKey));
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
      const n = purgeAllStagingJpgs(root);
      totalActions += n;
      streamLog(stationId, "staging_purge", { removedJpgs: n, stagingBytes });
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
      const removed = purgeAllStagingJpgs(root);
      streamLog(stationId, "staging_emergency_purge", {
        removedJpgs: removed,
        stagingBytes,
        thresholdBytes: STAGING_EMERGENCY_PURGE_BYTES,
      });
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

async function extractTarZstArchive(archivePath, destDir) {
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
  for (const row of rows) {
    const frameIndex = Number(row.frame_index ?? row.frameIndex ?? -1);
    if (!Number.isInteger(frameIndex) || frameIndex < 0) continue;
    const binPath = path.join(extractDir, "frames", `${String(frameIndex).padStart(8, "0")}.bin`);
    if (!fs.existsSync(binPath)) {
      throw new Error(`missing frame bin: ${binPath}`);
    }
    const cameraJpegs = unpackFrameBin(fs.readFileSync(binPath));
    for (const [videoKey, jpeg] of Object.entries(cameraJpegs)) {
      images[segmentImageKey(frameIndex, videoKey)] = jpeg;
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
  };
}

function ensureSessionForImport(stationId, body) {
  const root = stationRoot(stationId);
  const livePath = path.join(root, "live", "session.json");
  const live = readJson(livePath, {});
  const sessionId = body.sessionId;
  const sessionDir = sessionPath(root, sessionId);
  if (live.sessionId === sessionId && fs.existsSync(sessionDir)) {
    return;
  }
  const task = body.frames[0]?.task;
  const videoShapes = {};
  for (const key of VIDEO_KEYS) {
    videoShapes[key] = [800, 1280];
  }
  handleStreamUpload(stationId, {
    action: "session_start",
    sessionId,
    task,
    videoShapes,
    episodeMeta: body.episodeMeta,
  });
}

/**
 * Process a local tar.zst segment archive (same path as online tarzst upload).
 * @param {string} filePath absolute path to .tar.zst
 * @param {string} stationId
 * @param {{ expectedSha?: string, sessionId?: string, segmentId?: string, onStatus?: (s: string) => void }} [options]
 */
export async function processTarZstFromFile(filePath, stationId, options = {}) {
  const { expectedSha, sessionId: expectSessionId, segmentId: expectSegmentId, onStatus } = options;
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
    await extractTarZstArchive(archivePath, extractDir);
    body = buildSegmentBodyFromExtractedDir(extractDir, stationId);
    if (expectSessionId && body.sessionId !== expectSessionId) {
      throw new Error("manifest session/segment mismatch with upload headers");
    }
    if (expectSegmentId && body.segmentId !== expectSegmentId) {
      throw new Error("manifest session/segment mismatch with upload headers");
    }
    ensureSessionForImport(stationId, body);
    onStatus?.("committing");
    body.ingestSource = expectSessionId ? "edge" : "import";
    const out = handleStreamUpload(stationId, body);
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
  if (!sessionId || !segmentId) {
    throw new Error("X-Session-Id and X-Segment-Id required for tarzst upload");
  }
  const root = stationRoot(stationId);
  const incomingDir = path.join(root, ".upload", "incoming");
  ensureDir(incomingDir);
  const archivePath = path.join(incomingDir, `${segmentId}_${Date.now()}.tar.zst`);
  try {
    await streamRequestToFile(req, archivePath);
    const { out } = await processTarZstFromFile(archivePath, stationId, {
      expectedSha,
      sessionId,
      segmentId,
    });
    return out;
  } finally {
    try {
      fs.rmSync(archivePath, { force: true });
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
  const state = muxQueue.get(stationId) || { timer: null, running: false };
  if (state.timer) clearTimeout(state.timer);
  state.timer = setTimeout(() => runMux(stationId), MUX_DEBOUNCE_MS);
  muxQueue.set(stationId, state);
}

function escapeConcatPath(p) {
  return String(p).replace(/'/g, "'\\''");
}

/** Build ffconcat list aligned to dataset frame indices (hold last frame across gaps). */
function buildStagingConcatList(inDir, fps, fromIdx, toIdx) {
  if (!fs.existsSync(inDir) || toIdx < fromIdx) return null;
  const byIdx = new Map();
  for (const f of fs.readdirSync(inDir)) {
    const m = /^frame_(\d+)\.jpg$/.exec(f);
    if (!m) continue;
    byIdx.set(Number.parseInt(m[1], 10), f);
  }
  if (byIdx.size === 0) return null;

  const dt = 1 / fps;
  const lines = ["ffconcat version 1.0"];
  let holdPath = null;
  for (let i = fromIdx; i <= toIdx; i += 1) {
    const name = byIdx.get(i);
    if (name) {
      holdPath = path.join(inDir, name);
    }
    if (!holdPath) continue;
    lines.push(`file '${escapeConcatPath(holdPath)}'`);
    lines.push(`duration ${dt}`);
  }
  if (!holdPath) return null;
  lines.push(`file '${escapeConcatPath(holdPath)}'`);
  return lines.join("\n");
}

function encodeStagingToMp4(inDir, fromIdx, toIdx, fps, destPath) {
  const listContent = buildStagingConcatList(inDir, fps, fromIdx, toIdx);
  if (!listContent) return Promise.resolve(false);
  const listPath = `${destPath}.concat.txt`;
  writeFileAtomic(listPath, listContent);
  return new Promise((resolve) => {
    const ff = spawn(
      "ffmpeg",
      [
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
        "-vf",
        "scale='max(2,trunc(iw/2)*2)':'max(2,trunc(ih/2)*2)'",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        destPath,
      ],
      { stdio: "ignore" },
    );
    ff.on("close", (code) => {
      try {
        fs.unlinkSync(listPath);
      } catch {
        /* ignore */
      }
      resolve(code === 0 && fs.existsSync(destPath));
    });
    ff.on("error", () => resolve(false));
  });
}

function concatMp4Files(firstPath, secondPath, destPath) {
  const listPath = `${destPath}.concat.txt`;
  const esc = (p) => p.replace(/'/g, "'\\''");
  writeFileAtomic(
    listPath,
    `file '${esc(firstPath)}'\nfile '${esc(secondPath)}'\n`,
  );
  return new Promise((resolve) => {
    const ff = spawn(
      "ffmpeg",
      [
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
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        destPath,
      ],
      { stdio: "ignore" },
    );
    ff.on("close", (code) => {
      try {
        fs.unlinkSync(listPath);
      } catch {
        /* ignore */
      }
      resolve(code === 0 && fs.existsSync(destPath));
    });
    ff.on("error", () => resolve(false));
  });
}

function muxOneCamera(stationId, root, videoKey, muxSessionId) {
  const inDir = stagingDir(root, videoKey);
  const outFile = videoOutPath(root, videoKey);
  const range = stagingFrameRange(inDir);
  if (!range) return Promise.resolve({ ok: true, skipped: true });

  const muxState = readMuxState(root);
  const camState = muxState.cameras[videoKey] || {};
  const lastMuxed = typeof camState.lastMuxedFrame === "number" ? camState.lastMuxedFrame : -1;
  if (range.max <= lastMuxed) return Promise.resolve({ ok: true, skipped: true });

  const info = readJson(path.join(root, "meta", "info.json"), {});
  const totalFrames = Math.max(0, Number(info.total_frames || 0));
  const incremental = fs.existsSync(outFile) && lastMuxed >= 0;
  const muxFrom = incremental ? lastMuxed + 1 : 0;
  const muxTo = incremental ? range.max : Math.max(range.max, totalFrames > 0 ? totalFrames - 1 : range.max);
  if (muxTo < muxFrom) return Promise.resolve({ ok: true, skipped: true });

  ensureDir(path.dirname(outFile));
  const segmentPath = `${outFile}.segment.tmp.mp4`;
  const tmpOut = `${outFile}.muxing.tmp`;
  for (const p of [segmentPath, tmpOut]) {
    try {
      if (fs.existsSync(p)) fs.unlinkSync(p);
    } catch {
      /* ignore */
    }
  }

  return encodeStagingToMp4(inDir, muxFrom, muxTo, DEFAULT_FPS, segmentPath).then(async (segmentOk) => {
    if (!segmentOk) {
      streamLog(stationId, "mux_fail", { sessionId: muxSessionId, videoKey, stage: "segment" });
      try {
        if (fs.existsSync(segmentPath)) fs.unlinkSync(segmentPath);
      } catch {
        /* ignore */
      }
      return { ok: false };
    }

    let finalOk = false;
    if (fs.existsSync(outFile) && lastMuxed >= 0 && range.min > lastMuxed) {
      finalOk = await concatMp4Files(outFile, segmentPath, tmpOut);
      if (finalOk) {
        fs.renameSync(tmpOut, outFile);
      }
    } else {
      try {
        fs.renameSync(segmentPath, outFile);
        finalOk = true;
      } catch {
        finalOk = false;
      }
    }

    try {
      if (fs.existsSync(segmentPath)) fs.unlinkSync(segmentPath);
      if (fs.existsSync(tmpOut)) fs.unlinkSync(tmpOut);
    } catch {
      /* ignore */
    }

    if (!finalOk) {
      streamLog(stationId, "mux_fail", { sessionId: muxSessionId, videoKey, stage: "finalize" });
      return { ok: false };
    }

    const removed = purgeStagingJpgsInDir(inDir, range.max);
    muxState.cameras[videoKey] = {
      lastMuxedFrame: range.max,
      lastMuxedAt: new Date().toISOString(),
    };
    writeMuxState(root, muxState);
    setChunkArtifactStatus(root, videoArtifactRel(videoKey), "finished", countStagingFrames(root));
    streamLog(stationId, "mux_camera_done", {
      sessionId: muxSessionId,
      videoKey,
      frameMin: range.min,
      frameMax: range.max,
      removedJpgs: removed,
    });
    return { ok: true, removed };
  });
}

function runMux(stationId) {
  const state = muxQueue.get(stationId) || { timer: null, running: false };
  if (state.running) {
    state.timer = setTimeout(() => runMux(stationId), MUX_DEBOUNCE_MS);
    muxQueue.set(stationId, state);
    return;
  }
  state.running = true;
  muxQueue.set(stationId, state);

  const root = stationRoot(stationId);
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

  markMuxArtifactsWriting(root);
  const muxSessionId = getActiveSessionId(root);
  streamLog(stationId, "mux_start", { sessionId: muxSessionId, frames: countStagingFrames(root) });

  const jobs = ingestVideoKeys(root).map((videoKey) =>
    muxOneCamera(stationId, root, videoKey, muxSessionId),
  );

  Promise.all(jobs).finally(() => {
    releaseFileLock(lockPath, lockFd);
    streamLog(stationId, "mux_done", { sessionId: muxSessionId, frames: countStagingFrames(root) });
    const s = muxQueue.get(stationId) || { timer: null, running: false };
    s.running = false;
    s.timer = null;
    muxQueue.set(stationId, s);
    scheduleViewerPublish(stationId);
    scheduleParquetSync(stationId);
    scheduleDiskCleanup(stationId);
  });
}

function runParquetSync(stationId) {
  const root = stationRoot(stationId);
  scheduleViewerPublish(stationId);

  if (!fs.existsSync(SYNC_SCRIPT)) {
    streamLog(stationId, "parquet_skip", { reason: "no_sync_script" });
    return;
  }

  const py = resolveParquetPython();
  if (!py) {
    streamLog(stationId, "parquet_skip", { reason: "no_python" });
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

  let jsonlFd = null;
  const jsonlLock = path.join(locksDir(root), "jsonl.lock");
  try {
    jsonlFd = acquireFileLock(jsonlLock, 120_000);
  } catch {
    releaseFileLock(parquetLock, parquetFd);
    scheduleParquetSync(stationId);
    return;
  }

  markParquetArtifactsWriting(root);
  const parquetSessionId = getActiveSessionId(root);
  streamLog(stationId, "parquet_start", { sessionId: parquetSessionId });

  const child = spawn(py, [SYNC_SCRIPT, root], { stdio: "ignore" });
  child.on("error", () => {
    releaseFileLock(jsonlLock, jsonlFd);
    releaseFileLock(parquetLock, parquetFd);
    runViewerPublish(stationId);
  });
  child.on("close", (code) => {
    runViewerPublish(stationId);
    streamLog(stationId, "parquet_done", { sessionId: parquetSessionId, exitCode: code });
    releaseFileLock(jsonlLock, jsonlFd);
    releaseFileLock(parquetLock, parquetFd);
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
  const quotaBytes = stationQuotaBytes(stationId);
  return {
    stationId,
    totalFrames: chunks.viewerTotalFrames ?? 0,
    ingestFrames: info.total_frames || 0,
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

export function handleStreamUpload(stationId, body) {
  const root = stationRoot(stationId);
  const action = body?.action;

  if (action === "heartbeat") {
    touchHeartbeat(root, stationId, body.host || null);
    const liveHb = readJson(path.join(root, "live", "session.json"), {});
    streamLog(stationId, "heartbeat", { sessionId: liveHb.sessionId, host: body.host || null });
    return { ok: true, action: "heartbeat" };
  }

  if (action === "session_start") {
    const sessionId = body.sessionId || `sess_${Date.now()}`;
    const shapes = body.videoShapes || {};
    const livePath = path.join(root, "live", "session.json");
    const prevLive = readJson(livePath, {});
    const sessionDir = sessionPath(root, sessionId);
    const isResume = prevLive.sessionId === sessionId && fs.existsSync(sessionDir);
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
      writeJson(path.join(root, "meta", "info.json"), defaultInfo(stationId, shapes, episodeMeta));
      writeTasksJsonl(root, task);
      initChunksManifest(root, { resetViewer: true });
      writeViewerInfoSnapshot(root, 0);
      setChunkArtifactStatus(root, "meta/info.json", "finished", 0);
    }
    const intrinsics = body.cameraIntrinsics;
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
    const info = readJson(path.join(root, "meta", "info.json"), defaultInfo(stationId, shapes));
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
    const stagingKeys = isResume ? ingestVideoKeys(root) : VIDEO_KEYS;
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

    enqueueSegmentIngest(stationId, {
      sessionId,
      segmentId,
      frames,
      images,
      shapes,
      host: live.host || null,
      ingestSource: body.ingestSource || "stream",
      episodeMeta: body.episodeMeta || null,
    });

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

export function resolveStreamFile(stationId, urlPath) {
  const root = stationRoot(stationId);
  const rel = urlPath.replace(/^\/+/, "");
  const norm = rel.replace(/\\/g, "/");
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
  scheduleMux(stationId);
  runParquetSync(stationId);
}

/** Resume video mux for stations that still have staging jpgs or unfinished mp4 publish flags. */
export function resumePendingStreamMuxForAllStations() {
  if (!fs.existsSync(STREAM_ROOT)) return;
  for (const stationId of fs.readdirSync(STREAM_ROOT)) {
    const root = stationRoot(stationId);
    if (!fs.existsSync(path.join(root, "meta", "info.json"))) continue;
    const manifest = readChunksManifest(root);
    const videosPending = Object.entries(manifest.publish || {}).some(
      ([rel, st]) => rel.startsWith("videos/") && st?.status !== "finished",
    );
    const hasStaging = countStagingFramesScan(root) > 0;
    if (videosPending || hasStaging) {
      scheduleMux(stationId);
    }
  }
}
