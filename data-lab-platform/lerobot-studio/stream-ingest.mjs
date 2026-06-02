/**
 * Live LeRobot v3 stream ingest for collection stations (meta + data + videos only).
 */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const STREAM_ROOT = process.env.STREAM_DATA_ROOT || "/srv/stream";

const VIDEO_KEYS = [
  "observation.images.camera_head_left",
  "observation.images.camera_head_right",
  "observation.images.camera_depth_head",
  "observation.images.camera_02",
];

const DEFAULT_FPS = 15;
const MUX_DEBOUNCE_MS = 400;
const PARQUET_DEBOUNCE_MS = 600;
const VIEWER_PUBLISH_DEBOUNCE_MS = 800;
const HEARTBEAT_TTL_MS = 45_000;
const SYNC_SCRIPT = path.join(__dirname, "scripts", "sync-stream-parquet.py");
const DEFAULT_QUOTA_GB = Number(process.env.STREAM_QUOTA_GB || 100);
const RETENTION_DAYS = Number(process.env.STREAM_RETENTION_DAYS || 7);
const DISK_HIGH_WATER_RATIO = Number(process.env.STREAM_DISK_HIGH_WATER || 0.85);
const DISK_TARGET_RATIO = Number(process.env.STREAM_DISK_TARGET || 0.7);
const CLEANUP_DEBOUNCE_MS = Number(process.env.STREAM_CLEANUP_DEBOUNCE_MS || 300_000);
const CLEANUP_INTERVAL_MS = Number(process.env.STREAM_CLEANUP_INTERVAL_MS || 3_600_000);
const DISK_USAGE_CACHE_MAX_AGE_MS = Number(process.env.STREAM_DISK_USAGE_CACHE_MS || 120_000);
const STATION_TOKEN_HEADER = "x-station-token";
/**
 * Natural-language task description (shown in LeRobot episode list, like sample datasets).
 * Dataset title stays "EGO 采集站 · 214" via stream-http-source.js.
 */
export const DEFAULT_STREAM_TASK =
  "Perform egocentric manipulation tasks at the laboratory workbench";
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

function allChunkArtifactRels() {
  return [
    "meta/info.json",
    "data/chunk-000/file-000.parquet",
    "meta/episodes/chunk-000/file-000.parquet",
    ...VIDEO_KEYS.map(videoArtifactRel),
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
  for (const videoKey of VIDEO_KEYS) {
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
  for (const rel of allChunkArtifactRels()) {
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
  const viewerInfo = {
    ...info,
    total_frames: viewerFrames,
    total_episodes: 1,
    splits: info.splits || { train: "0:1" },
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
  for (const rel of allChunkArtifactRels()) {
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
  for (const videoKey of VIDEO_KEYS) {
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

function writeTasksJsonl(root, task) {
  ensureDir(path.join(root, "meta"));
  const line = JSON.stringify({ task_index: 0, task: task || DEFAULT_STREAM_TASK });
  fs.writeFileSync(path.join(root, "meta", "tasks.jsonl"), `${line}\n`);
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

const STATION_LIVE_CACHE_MS = Number(process.env.STATION_LIVE_CACHE_MS || 30_000);
/** @type {Map<string, { online: boolean, at: number }>} */
const stationLiveCache = new Map();

/** Cached isStationLive for list API — avoids disk churn under ingest load. */
export function isStationLiveCached(stationId) {
  const now = Date.now();
  const hit = stationLiveCache.get(stationId);
  if (hit && now - hit.at < STATION_LIVE_CACHE_MS) {
    return hit.online;
  }
  const online = isStationLive(stationId);
  stationLiveCache.set(stationId, { online, at: now });
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
}

function defaultInfo(stationId, shapes) {
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

  return {
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
      camera_layout: "sensexperience_ego_four_cam",
      stream_station: stationId,
      stream_mode: "frame_push",
    },
  };
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

function runDiskCleanup(stationId) {
  const root = stationRoot(stationId);
  if (!fs.existsSync(root)) return;
  withCleanupLock(root, () => {
    const activeSessionId = getActiveSessionId(root);
    const quotaBytes = stationQuotaBytes(stationId);
    purgeExpiredArchives(root, activeSessionId);
    purgeExpiredSessionMarkers(root, activeSessionId);
    purgeArchivesUntilUnderQuota(root, activeSessionId, quotaBytes);
    const usageBytes = dirSizeBytes(root);
    writeJsonAtomic(path.join(root, "live", "disk-housekeeping.json"), {
      stationId,
      at: new Date().toISOString(),
      activeSessionId,
      usageBytes,
      quotaBytes,
      retentionDays: RETENTION_DAYS,
    });
    streamLog(stationId, "disk_cleanup", {
      sessionId: activeSessionId,
      usageBytes,
      quotaBytes,
      retentionDays: RETENTION_DAYS,
    });
  });
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

export async function handleStreamUploadRequest(stationId, req) {
  const auth = verifyStationUploadToken(stationId, req);
  if (!auth.ok) {
    const err = new Error("unauthorized");
    err.statusCode = 401;
    err.reason = auth.reason;
    throw err;
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

  const jobs = VIDEO_KEYS.map((videoKey) => {
    const inDir = stagingDir(root, videoKey);
    const outFile = videoOutPath(root, videoKey);
    const tmpOut = `${outFile}.muxing.tmp`;
    if (!fs.existsSync(inDir)) return Promise.resolve();
    if (countStagingFramesFast(root) <= 0) return Promise.resolve();
    ensureDir(path.dirname(outFile));
    try {
      if (fs.existsSync(tmpOut)) fs.unlinkSync(tmpOut);
    } catch {
      /* ignore */
    }
    const inputPattern = path.join(inDir, "frame_%06d.jpg");
    return new Promise((resolve) => {
      const ff = spawn(
        "ffmpeg",
        [
          "-y",
          "-hide_banner",
          "-loglevel",
          "error",
          "-framerate",
          String(DEFAULT_FPS),
          "-i",
          inputPattern,
          "-vf",
          "scale='max(2,trunc(iw/2)*2)':'max(2,trunc(ih/2)*2)'",
          "-c:v",
          "libx264",
          "-pix_fmt",
          "yuv420p",
          "-movflags",
          "+faststart",
          "-f",
          "mp4",
          tmpOut,
        ],
        { stdio: "ignore" },
      );
      ff.on("close", (code) => {
        try {
          if (code !== 0) {
            streamLog(stationId, "mux_fail", { sessionId: muxSessionId, videoKey, exitCode: code });
          }
          if (code === 0 && fs.existsSync(tmpOut)) {
            fs.renameSync(tmpOut, outFile);
            setChunkArtifactStatus(
              root,
              videoArtifactRel(videoKey),
              "finished",
              countStagingFrames(root),
            );
          } else if (fs.existsSync(tmpOut)) {
            fs.unlinkSync(tmpOut);
          }
        } catch {
          /* ignore */
        }
        resolve();
      });
      ff.on("error", () => resolve());
    });
  });

  Promise.all(jobs).finally(() => {
    releaseFileLock(lockPath, lockFd);
    streamLog(stationId, "mux_done", { sessionId: muxSessionId, frames: countStagingFrames(root) });
    const s = muxQueue.get(stationId) || { timer: null, running: false };
    s.running = false;
    s.timer = null;
    muxQueue.set(stationId, s);
    scheduleViewerPublish(stationId);
    scheduleParquetSync(stationId);
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
  const usageBytes =
    cachedDiskUsageBytes(root) ?? (fs.existsSync(root) ? dirSizeBytes(root) : 0);
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
    if (!isResume) {
      writeJson(path.join(root, "meta", "info.json"), defaultInfo(stationId, shapes));
      writeTasksJsonl(root, body.task || DEFAULT_STREAM_TASK);
      initChunksManifest(root, { resetViewer: true });
      writeViewerInfoSnapshot(root, 0);
      setChunkArtifactStatus(root, "meta/info.json", "finished", 0);
    } else if (!fs.existsSync(chunksManifestPath(root))) {
      initChunksManifest(root, { resetViewer: false });
    }
    const info = readJson(path.join(root, "meta", "info.json"), defaultInfo(stationId, shapes));
    writeJson(livePath, {
      sessionId,
      startedAt: prevLive.startedAt || new Date().toISOString(),
      resumedAt: isResume ? new Date().toISOString() : null,
      task: body.task || prevLive.task || DEFAULT_STREAM_TASK,
      episodeIndex: 0,
      lastFrameIndex: prevLive.lastFrameIndex ?? null,
    });
    ensureDir(sessionDir);
    ensureDir(path.join(sessionDir, "frames"));
    ensureDir(stagingTmpRoot(root));
    ensureDir(path.join(stagingTmpRoot(root), "inflight"));
    ensureDir(locksDir(root));
    for (const key of VIDEO_KEYS) {
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
      task: body.task || DEFAULT_STREAM_TASK,
      "observation.state": body.observationState || [0, 0, 0, 0, 0, 0],
      "observation.pose": body.observationPose || [0, 0, 0, 0, 0, 0, 1],
      "observation.hands": body.observationHands || new Array(63).fill(0),
      action: body.actionVector || [0],
    };
    commitFrameAtomically(root, frameIndex, images, row);
    if (frameIndex % 30 === 0) {
      streamLog(stationId, "frame_committed", {
        sessionId,
        frameIndex,
        totalFrames: Math.max(info?.total_frames || 0, frameIndex + 1),
      });
    }

    info.total_frames = Math.max(info.total_frames || 0, frameIndex + 1);
    info.total_episodes = 1;
    writeJsonAtomic(path.join(root, "meta", "info.json"), info);

    live.sessionId = sessionId;
    live.updatedAt = new Date().toISOString();
    live.lastFrameIndex = Math.max(live.lastFrameIndex ?? -1, frameIndex);
    writeJson(livePath, live);

    markFrameCommitted(root, sessionId, frameIndex);
    touchHeartbeat(root, stationId, live.host || null);

    if (frameIndex % 30 === 0) {
      streamLog(stationId, "frame_received", { sessionId, frameIndex });
    }

    scheduleMux(stationId);
    scheduleViewerPublish(stationId);
    scheduleParquetSync(stationId);
    return { ok: true, action: "frame", frameIndex, totalFrames: info.total_frames, duplicate: false };
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
    const viewer = viewerInfoPath(root);
    if (!canServeChunkArtifact(root, "meta/info.json")) return null;
    if (!fs.existsSync(viewer) || !fs.statSync(viewer).isFile()) return null;
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
