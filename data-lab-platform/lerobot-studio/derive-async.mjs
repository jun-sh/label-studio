/**
 * Async derive (34): raw upload ACK + linear derive pipeline.
 * External SLA: UPLOADED (verified raw) | READY (disk: parquet + MP4).
 */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import {
  computeDeriveStatusFromDisk,
  computeStationDeriveStatusFromDisk,
  countCommittedSegmentsForSession,
  countRawTarZstForSession,
  isMuxPipelineActive,
  isSegmentParquetDerivedOnDisk,
  peekManifestFromTarZstArchive,
  stationRoot,
  streamLog,
  STREAM_ROOT,
} from "./stream-ingest.mjs";
import {
  getDerivePipelineRunning,
  pickPrimarySessionId,
  resumeDerivePipelinesForAllStations,
  scheduleDerivePipeline,
} from "./derive-pipeline.mjs";
import {
  markSessionUploadDone,
  markSessionReady,
  hasSessionMarker,
  SESSION_MARKERS,
} from "./session-markers.mjs";

/** @type {Map<string, NodeJS.Timeout>} */
const deriveAutoStartTimers = new Map();

export const SEGMENT_STATUS = {
  UPLOADED: "verified",
};

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function writeJsonAtomic(p, obj) {
  ensureDir(path.dirname(p));
  const tmp = `${p}.tmp.${process.pid}.${Date.now()}`;
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2));
  fs.renameSync(tmp, p);
}

function readJson(p, fallback) {
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return fallback;
  }
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

export function isDeriveAsyncEnabled(stationId) {
  const envKey = `DERIVE_ASYNC_${String(stationId).toUpperCase().replace(/-/g, "_")}`;
  if (process.env[envKey] !== undefined) {
    const v = String(process.env[envKey]).trim().toLowerCase();
    return v === "1" || v === "true" || v === "yes";
  }
  const global = String(process.env.DERIVE_ASYNC || "0").trim().toLowerCase();
  return global === "1" || global === "true" || global === "yes";
}

/** When true (default), raw upload ACK does not start derive; use derive-start or idle kick. */
export function shouldDeferDeriveUntilUpload() {
  const v = String(process.env.DERIVE_DEFER_UNTIL_UPLOAD ?? "1").trim().toLowerCase();
  return v !== "0" && v !== "false" && v !== "no";
}

/** Commercial mode: upload HTTP never runs heavy derive; external CLI/worker only. */
export function isDeriveStandalone() {
  const v = String(process.env.DERIVE_STANDALONE ?? "0").trim().toLowerCase();
  return v === "1" || v === "true" || v === "yes";
}

function kickStandaloneDeriveMarker(stationId, sessionId, source, extra = {}) {
  const root = stationRoot(stationId);
  if (hasSessionMarker(root, sessionId, SESSION_MARKERS.READY)) {
    return { marked: false, reason: "already_ready" };
  }
  markSessionUploadDone(root, sessionId, { stationId, source, ...extra });
  streamLog(stationId, "derive_standalone_marker", { sessionId, source, marker: SESSION_MARKERS.DONE_UPLOAD });
  return { marked: true, sessionId, marker: SESSION_MARKERS.DONE_UPLOAD };
}

/** Align session.DONE_UPLOAD / session.READY markers with on-disk derive status. */
export function syncSessionLifecycleMarkers(stationId, sessionId, source = "disk") {
  if (!sessionId) return { synced: false, reason: "no_session" };
  const root = stationRoot(stationId);
  if (hasSessionMarker(root, sessionId, SESSION_MARKERS.READY)) {
    return { synced: true, phase: "READY", sessionId, already: true };
  }
  const disk = computeDeriveStatusFromDisk(stationId, sessionId);
  if (disk.total <= 0 && disk.committedSegments <= 0 && disk.parquetRows <= 0) {
    return { synced: false, reason: "no_data", sessionId };
  }
  if (disk.committedSegments > 0 || disk.rawSegments > 0 || disk.markers > 0 || disk.parquetRows > 0) {
    markSessionUploadDone(root, sessionId, {
      stationId,
      source,
      committed: disk.committedSegments,
      raw: disk.rawSegments,
      total: disk.total,
      parquetRows: disk.parquetRows,
    });
  }
  if (disk.fullyReady || disk.phase === "READY") {
    markSessionReady(root, sessionId, disk);
    streamLog(stationId, "session_lifecycle_ready", { sessionId, source, markers: disk.markers, total: disk.total });
    return { synced: true, phase: "READY", sessionId, disk };
  }
  return { synced: true, phase: disk.phase, sessionId, disk };
}

/** Called after session finalize / parquet sync — commercial READY contract. */
export function markSessionSegmentsReady(stationId, sessionId) {
  return syncSessionLifecycleMarkers(stationId, sessionId, "session_finalize");
}

/** Phase-1: auto-start derive when all session raw segments are on disk (34-side). */
export function shouldAutoStartDeriveOnUploadComplete() {
  const v = String(process.env.DERIVE_AUTO_START_ON_UPLOAD_COMPLETE ?? "1").trim().toLowerCase();
  return v !== "0" && v !== "false" && v !== "no";
}

/** Phase-1: external API exposes UPLOADED | READY only. */
export function shouldExposeTwoPhaseApi() {
  const v = String(process.env.DERIVE_API_TWO_PHASE ?? "1").trim().toLowerCase();
  return v !== "0" && v !== "false" && v !== "no";
}

export function shouldExposeInternalDeriveApi() {
  const v = String(process.env.DERIVE_API_EXPOSE_INTERNAL ?? "0").trim().toLowerCase();
  return v === "1" || v === "true" || v === "yes";
}

function autoStartDebounceMs() {
  return Math.max(500, Number(process.env.DERIVE_AUTO_START_DEBOUNCE_MS || 3000));
}

function idleDeriveFallbackSeconds() {
  return Math.max(60, Number(process.env.DERIVE_IDLE_FALLBACK_SECONDS || 180));
}

function uploadActivityPath(root) {
  return path.join(root, "state", "upload_activity.json");
}

export function touchRawUploadActivity(stationId, meta = {}) {
  const root = stationRoot(stationId);
  const prev = readUploadActivity(root);
  const expected = Number(meta.expectedSegmentTotal || meta.sessionSegmentTotal || 0);
  const patch = {
    stationId,
    lastUploadAt: new Date().toISOString(),
    lastUploadMs: Date.now(),
  };
  if (meta.sessionId) patch.sessionId = meta.sessionId;
  if (expected > 0) {
    patch.expectedSegmentTotal = Math.max(Number(prev.expectedSegmentTotal || 0), expected);
  } else if (prev.expectedSegmentTotal) {
    patch.expectedSegmentTotal = prev.expectedSegmentTotal;
  }
  if (prev.sessionId && !patch.sessionId) patch.sessionId = prev.sessionId;
  writeJsonAtomic(uploadActivityPath(root), patch);
}

function readExpectedSegmentTotal(root, sessionId) {
  const activity = readUploadActivity(root);
  const expected = Number(activity.expectedSegmentTotal || 0);
  if (expected > 0 && (!activity.sessionId || activity.sessionId === sessionId)) {
    return expected;
  }
  return 0;
}

function readUploadActivity(root) {
  return readJson(uploadActivityPath(root), {});
}

function incomingUploadActive(root) {
  const incoming = path.join(root, ".upload", "incoming");
  if (!fs.existsSync(incoming)) return false;
  const cutoff = Date.now() - 30_000;
  try {
    for (const name of fs.readdirSync(incoming)) {
      if (!name.endsWith(".tar.zst")) continue;
      const st = fs.statSync(path.join(incoming, name));
      if (st.mtimeMs >= cutoff) return true;
    }
  } catch {
    /* ignore */
  }
  return false;
}

function idleDeriveSeconds() {
  return Math.max(15, Number(process.env.DERIVE_IDLE_SECONDS || 60));
}

function isSessionUploadComplete(root, sessionId) {
  const rawTotal = countRawTarZstForSession(root, sessionId);
  const committed = countCommittedSegmentsForSession(root, sessionId);
  const expected = readExpectedSegmentTotal(root, sessionId);
  const total = Math.max(rawTotal, committed, expected);
  if (total <= 0) return false;

  if (rawTotal > 0) {
    const verified = listSegmentStates(root, { sessionId, status: SEGMENT_STATUS.UPLOADED });
    if (verified.length >= rawTotal) return true;
    const rawDir = path.join(root, "raw", "segments", sessionId);
    if (!fs.existsSync(rawDir)) return false;
    const onDisk = fs.readdirSync(rawDir).filter((f) => f.endsWith(".tar.zst")).length;
    return onDisk >= rawTotal && verified.length >= onDisk;
  }

  // Committed-only path: live/sessions/.../*.done (no raw tar.zst on disk).
  if (expected > 0) return committed >= expected;
  return committed > 0 && committed >= total;
}

/**
 * After each raw ACK: debounce then auto-start derive when session upload is complete.
 */
export function maybeScheduleDeriveWhenSessionComplete(stationId, sessionId) {
  if (!shouldAutoStartDeriveOnUploadComplete()) {
    return { scheduled: false, reason: "auto_start_disabled" };
  }
  if (!isDeriveAsyncEnabled(stationId)) return { scheduled: false, reason: "async_disabled" };
  if (!shouldDeferDeriveUntilUpload()) return { scheduled: false, reason: "defer_disabled" };
  if (!sessionId) return { scheduled: false, reason: "no_session" };
  if (getDerivePipelineRunning(stationId)) return { scheduled: false, reason: "pipeline_running" };

  const root = stationRoot(stationId);
  const disk = computeDeriveStatusFromDisk(stationId, sessionId);
  if (disk.phase === "READY") return { scheduled: false, reason: "already_ready" };
  if (!isSessionUploadComplete(root, sessionId)) {
    return { scheduled: false, reason: "session_incomplete" };
  }

  const timerKey = `${stationId}:${sessionId}`;
  const prev = deriveAutoStartTimers.get(timerKey);
  if (prev) clearTimeout(prev);
  const debounceMs = autoStartDebounceMs();
  const timer = setTimeout(() => {
    deriveAutoStartTimers.delete(timerKey);
    if (getDerivePipelineRunning(stationId)) return;
    if (incomingUploadActive(root)) return;
    const latest = computeDeriveStatusFromDisk(stationId, sessionId);
    if (latest.phase === "READY") return;
    if (!isSessionUploadComplete(root, sessionId)) return;
    if (isDeriveStandalone()) {
      kickStandaloneDeriveMarker(stationId, sessionId, "session_complete");
      return;
    }
    scheduleDerivePipeline(stationId);
    streamLog(stationId, "derive_auto_start", {
      source: "session_complete",
      sessionId,
      segments: latest.total,
    });
  }, debounceMs);
  deriveAutoStartTimers.set(timerKey, timer);
  return { scheduled: true, debounceMs };
}

function readDeriveFailures(root) {
  const failures = [];
  const muxFail = readJson(path.join(root, "live", "derive", "mux_last_failure.json"), null);
  if (muxFail && muxFail.message) {
    failures.push({
      segmentId: "session_mux",
      errorMsg: String(muxFail.message).slice(0, 300),
      stage: "mux",
      attempt: muxFail.attempt,
    });
  }
  const ingestErrDir = path.join(root, "live", "sessions");
  if (fs.existsSync(ingestErrDir)) {
    try {
      for (const sess of fs.readdirSync(ingestErrDir)) {
        const errDir = path.join(ingestErrDir, sess, "ingest_errors");
        if (!fs.existsSync(errDir)) continue;
        for (const f of fs.readdirSync(errDir).filter((n) => n.endsWith(".json")).slice(0, 6)) {
          const item = readJson(path.join(errDir, f), {});
          if (item?.error) {
            failures.push({
              segmentId: item.segmentId || f.replace(/\.json$/, ""),
              errorMsg: String(item.error).slice(0, 300),
              stage: "ingest",
            });
          }
        }
      }
    } catch {
      /* ignore */
    }
  }
  return failures.slice(0, 8);
}

function mapExternalPhase(internalPhase, disk, pipelineRunning) {
  if (!shouldExposeTwoPhaseApi()) return internalPhase;
  if (internalPhase === "READY") return "READY";
  if (internalPhase === "IDLE") return "IDLE";
  return "UPLOADED";
}

const DERIVE_SUB_PHASE_LABELS = {
  MUXING: {
    zh: "视频合成中（MP4 多路编码）",
    en: "Muxing MP4 (multi-camera encode)",
  },
  DERIVING_SEGMENTS: {
    zh: "段级派生中",
    en: "Deriving segments",
  },
};

function resolveDeriveSubPhase(disk, pipelineRunning, muxActive) {
  if (disk.fullyReady) return null;
  const total = Number(disk.total) || 0;
  if (total > 0 && disk.markers < total) return "DERIVING_SEGMENTS";
  if (disk.parquetReady && !disk.mp4Ok) return "MUXING";
  if (muxActive || (pipelineRunning && disk.markers >= total && total > 0)) return "MUXING";
  return null;
}

/**
 * Kick derive when uploads have been idle long enough (fallback if auto-start missed).
 */
export function evaluateAndKickIdleDerive(stationId, source = "idle") {
  if (!isDeriveAsyncEnabled(stationId)) return { kicked: false, reason: "async_disabled" };
  if (!shouldDeferDeriveUntilUpload()) return { kicked: false, reason: "defer_disabled" };
  if (!isDeriveStandalone() && getDerivePipelineRunning(stationId)) {
    return { kicked: false, reason: "pipeline_running" };
  }

  const root = stationRoot(stationId);
  const sessionId = pickPrimarySessionId(stationId);
  if (!sessionId) return { kicked: false, reason: "no_session" };

  const disk = computeDeriveStatusFromDisk(stationId, sessionId);
  if (disk.phase === "READY") return { kicked: false, reason: "already_ready" };
  if (disk.total <= 0) return { kicked: false, reason: "no_raw" };
  if (incomingUploadActive(root)) return { kicked: false, reason: "incoming_active" };

  const activity = readUploadActivity(root);
  const lastMs = Number(activity.lastUploadMs || 0);
  if (!lastMs) return { kicked: false, reason: "no_upload_activity" };
  const idleMs = Date.now() - lastMs;

  if (isDeriveStandalone()) {
    if (idleMs < idleDeriveFallbackSeconds() * 1000) {
      return { kicked: false, reason: "upload_not_idle", idleMs };
    }
    const verified = listSegmentStates(root, { sessionId, status: SEGMENT_STATUS.UPLOADED });
    if (verified.length < disk.total && !isSessionUploadComplete(root, sessionId)) {
      return { kicked: false, reason: "segments_not_all_verified", verified: verified.length, raw: disk.total };
    }
    const marked = kickStandaloneDeriveMarker(stationId, sessionId, source, { idleMs });
    return { kicked: marked.marked, source: `${source}_standalone`, sessionId, idleMs, ...marked };
  }

  if (disk.markers >= disk.total && disk.parquetReady && !disk.fullyReady) {
    if (idleMs < idleDeriveFallbackSeconds() * 1000) {
      return { kicked: false, reason: "upload_not_idle_mux_fallback", idleMs };
    }
    scheduleDerivePipeline(stationId, { muxOnly: true });
    streamLog(stationId, "derive_start_kicked", {
      source: `${source}_mux_fallback`,
      sessionId,
      idleMs,
      markers: disk.markers,
      total: disk.total,
    });
    return { kicked: true, source: `${source}_mux_fallback`, sessionId, idleMs, muxOnly: true };
  }

  if (disk.markers >= disk.total) {
    return { kicked: false, reason: "all_raw_derived", markers: disk.markers, total: disk.total };
  }

  if (idleMs < idleDeriveFallbackSeconds() * 1000) {
    return { kicked: false, reason: "upload_not_idle", idleMs };
  }

  const verified = listSegmentStates(root, { sessionId, status: SEGMENT_STATUS.UPLOADED });
  if (verified.length < disk.total && !isSessionUploadComplete(root, sessionId)) {
    return { kicked: false, reason: "segments_not_all_verified", verified: verified.length, raw: disk.total };
  }

  scheduleDerivePipeline(stationId);
  streamLog(stationId, "derive_start_kicked", { source, sessionId, idleMs, segments: disk.total });
  return { kicked: true, source, sessionId, idleMs };
}

let idleWatcherStarted = false;

export function ensureIdleDeriveWatcher() {
  if (idleWatcherStarted || !shouldDeferDeriveUntilUpload()) return;
  idleWatcherStarted = true;
  const intervalMs = Math.max(5000, Number(process.env.DERIVE_IDLE_CHECK_MS || 15_000));
  setInterval(() => {
    if (!fs.existsSync(STREAM_ROOT)) return;
    for (const stationId of fs.readdirSync(STREAM_ROOT)) {
      if (stationId.startsWith(".")) continue;
      try {
        evaluateAndKickIdleDerive(stationId, "idle");
      } catch {
        /* ignore */
      }
    }
  }, intervalMs);
  streamLog("system", "derive_idle_watcher_started", {
    intervalMs,
    idleFallbackSeconds: idleDeriveFallbackSeconds(),
  });
}

export function handleDeriveStart(stationId) {
  if (!isDeriveAsyncEnabled(stationId)) {
    return {
      started: false,
      reason: "async_disabled",
      pipelineRunning: getDerivePipelineRunning(stationId),
    };
  }
  const sessionId = pickPrimarySessionId(stationId);
  const disk = sessionId ? computeDeriveStatusFromDisk(stationId, sessionId) : { phase: "IDLE", total: 0 };
  if (disk.phase === "READY") {
    return {
      started: true,
      alreadyReady: true,
      pipelineRunning: false,
      phase: "READY",
      sessionId,
    };
  }
  if (isDeriveStandalone()) {
    if (!sessionId) {
      return { started: false, reason: "no_session", mode: "standalone" };
    }
    const marked = kickStandaloneDeriveMarker(stationId, sessionId, "api");
    return {
      started: true,
      mode: "standalone",
      marker: marked.marker,
      sessionId,
      hint: "ego-derive run --station " + stationId,
      pipelineRunning: false,
      phase: "UPLOADED",
    };
  }
  const running = getDerivePipelineRunning(stationId);
  if (running) {
    return {
      started: true,
      alreadyRunning: true,
      pipelineRunning: true,
      phase: "DERIVING",
    };
  }
  scheduleDerivePipeline(stationId);
  streamLog(stationId, "derive_start_kicked", { source: "api", sessionId });
  return {
    started: true,
    alreadyRunning: false,
    pipelineRunning: true,
    phase: "DERIVING",
    sessionId,
  };
}

function segmentStatePath(root) {
  return path.join(root, "state", "segments.json");
}

function rawSegmentPath(root, sessionId, segmentId) {
  return path.join(root, "raw", "segments", sessionId, `${segmentId}.tar.zst`);
}

function rawManifestPath(root, sessionId, segmentId) {
  return path.join(root, "raw", "manifest", sessionId, `${segmentId}.json`);
}

function segmentKey(sessionId, segmentId) {
  return `${sessionId}:${segmentId}`;
}

export function loadSegmentStateStore(root) {
  return readJson(segmentStatePath(root), { revision: 0, segments: {} });
}

export function saveSegmentStateStore(root, store) {
  store.revision = Number(store.revision || 0) + 1;
  store.updatedAt = new Date().toISOString();
  writeJsonAtomic(segmentStatePath(root), store);
}

export function getSegmentState(root, sessionId, segmentId) {
  const store = loadSegmentStateStore(root);
  return store.segments[segmentKey(sessionId, segmentId)] || null;
}

function upsertSegmentState(root, sessionId, segmentId, patch) {
  const store = loadSegmentStateStore(root);
  const key = segmentKey(sessionId, segmentId);
  const prev = store.segments[key] || {
    sessionId,
    segmentId,
    status: SEGMENT_STATUS.UPLOADED,
  };
  store.segments[key] = { ...prev, ...patch, sessionId, segmentId };
  saveSegmentStateStore(root, store);
  return store.segments[key];
}

function enrichSegmentSla(seg) {
  const st = seg.status;
  let sla = "UPLOADED";
  if (st === "uploading") sla = "UPLOADING";
  return { ...seg, sla };
}

export function listSegmentStates(root, { sessionId, status } = {}) {
  const store = loadSegmentStateStore(root);
  let items = Object.values(store.segments || {});
  if (sessionId) items = items.filter((s) => s.sessionId === sessionId);
  if (status) items = items.filter((s) => s.status === status);
  items.sort((a, b) => String(a.segmentId).localeCompare(String(b.segmentId)));
  return items.map(enrichSegmentSla);
}

function parseSegmentIdFromFileName(fileName) {
  const m = /(seg_\d{6})/i.exec(String(fileName || ""));
  return m ? m[1] : null;
}

async function resolveSegmentIdentity({ archivePath, sessionId, segmentId, fileName }) {
  let sid = sessionId || null;
  let seg = segmentId || parseSegmentIdFromFileName(fileName);
  if (sid && seg) return { sessionId: sid, segmentId: seg };
  const manifest = await Promise.resolve(peekManifestFromTarZstArchive(archivePath));
  sid = sid || manifest.session_id || manifest.sessionId;
  seg = seg || manifest.segment_id || manifest.segmentId;
  if (!sid || !seg) throw new Error("cannot resolve sessionId/segmentId from manifest");
  return { sessionId: sid, segmentId: seg };
}

export function getDeriveQueueStats(stationId) {
  return {
    stationId,
    active: getDerivePipelineRunning(stationId) ? 1 : 0,
    pending: 0,
    dlq: 0,
    concurrency: 1,
    current: [],
    mode: "linear",
  };
}

/**
 * Disk-only status summary: UPLOADED | DERIVING | READY.
 */
export function getDeriveStatusSummary(stationId, { sessionId: sessionFilter } = {}) {
  const root = stationRoot(stationId);
  const stationWide = !sessionFilter;
  const sessionId = sessionFilter || pickPrimarySessionId(stationId);
  const disk = stationWide
    ? computeStationDeriveStatusFromDisk(stationId)
    : computeDeriveStatusFromDisk(stationId, sessionId);
  const uploaded = listSegmentStates(
    root,
    stationWide ? { status: SEGMENT_STATUS.UPLOADED } : { sessionId, status: SEGMENT_STATUS.UPLOADED },
  ).length;
  const rawOnDisk = stationWide
    ? disk.total
    : sessionId
      ? countRawTarZstForSession(root, sessionId)
      : 0;
  const batchExpected = Number(readUploadActivity(root).expectedSegmentTotal || 0);
  const expectedTotal = stationWide ? batchExpected : readExpectedSegmentTotal(root, sessionId);
  const received = Math.max(uploaded, rawOnDisk, disk.total);
  const total = Math.max(expectedTotal, received);
  const markers = disk.markers;
  const pipelineRunning = getDerivePipelineRunning(stationId);
  const deferUntilUpload = shouldDeferDeriveUntilUpload();
  const failedSegments = readDeriveFailures(root);

  let internalPhase = disk.phase;
  if (disk.phase === "READY") {
    internalPhase = "READY";
  } else if (pipelineRunning || disk.markers > 0 || (disk.parquetReady && !disk.fullyReady)) {
    internalPhase = "DERIVING";
  } else if (deferUntilUpload && total > 0) {
    internalPhase = "UPLOADED";
  } else if (total > 0) {
    internalPhase = disk.phase === "IDLE" ? "UPLOADED" : disk.phase;
  } else {
    internalPhase = "IDLE";
  }

  const phase = mapExternalPhase(internalPhase, disk, pipelineRunning);
  const muxActive = isMuxPipelineActive(stationId);
  const subPhase = resolveDeriveSubPhase(disk, pipelineRunning, muxActive);

  const percent =
    phase === "READY" && total > 0
      ? 100
      : total > 0
        ? Math.min(99, Math.round((markers / total) * 100))
        : 0;

  const muxRetry = readJson(path.join(root, "live", "derive", "mux_retry_state.json"), {});

  const summary = {
    version: 3,
    updatedAt: new Date().toISOString(),
    stationId,
    sessionId,
    phase,
    asyncEnabled: isDeriveAsyncEnabled(stationId),
    deferUntilUpload,
    mode: "linear",
    progress: {
      total,
      uploaded: received,
      ready: phase === "READY" ? total : 0,
      markers,
      parquetRows: disk.parquetRows,
      parquetReady: disk.parquetReady,
      mp4Ok: disk.mp4Ok,
      percent,
      remaining: phase === "READY" ? 0 : Math.max(0, total - markers),
      subPhase,
      subPhaseLabelZh: subPhase ? DERIVE_SUB_PHASE_LABELS[subPhase]?.zh : null,
      subPhaseLabelEn: subPhase ? DERIVE_SUB_PHASE_LABELS[subPhase]?.en : null,
    },
    deriveQueue: getDeriveQueueStats(stationId),
    pipelineRunning,
    failedSegments,
    logsHint: {
      command: `docker logs data-lab-stream-ingest-1 --since 2h 2>&1 | grep '${stationId}'`,
      deriveFailFilter: `docker logs data-lab-stream-ingest-1 --since 2h 2>&1 | grep -E 'derive_segment_fail|derive_pipeline_fail|mux_fail|mux_retry'`,
    },
    sla: {
      UPLOADED: "Raw tar.zst verified on disk; derive in progress until READY",
      READY: "Parquet rows + MP4 frames validated on disk",
    },
  };

  if (shouldExposeInternalDeriveApi()) {
    summary.internal = {
      phase: internalPhase,
      pipelineRunning,
      muxRetryAttempt: Number(muxRetry.attempt || 0),
    };
  }

  return summary;
}

export function enqueueDeriveJob(stationId) {
  scheduleDerivePipeline(stationId);
}

/** After raw upload ACK: defer or enqueue derive per DERIVE_DEFER_UNTIL_UPLOAD. */
export function maybeEnqueueDeriveAfterUpload(stationId, ack) {
  touchRawUploadActivity(stationId, { sessionId: ack?.sessionId });
  if (!ack?.job) return { queued: false, deferred: false };
  if (shouldDeferDeriveUntilUpload()) {
    streamLog(stationId, "derive_deferred_until_upload", {
      sessionId: ack.sessionId,
      segmentId: ack.segmentId,
    });
    maybeScheduleDeriveWhenSessionComplete(stationId, ack.sessionId);
    return { queued: false, deferred: true };
  }
  enqueueDeriveJob(stationId);
  return { queued: true, deferred: false };
}

export async function acceptRawTarZstUpload(stationId, options) {
  const {
    archivePath,
    expectedSha,
    sessionId: hintSessionId,
    segmentId: hintSegmentId,
    fileName,
    source = "edge",
    expectedSegmentTotal = 0,
  } = options;
  const root = stationRoot(stationId);
  const actualSha = await sha256File(archivePath);
  if (expectedSha && actualSha !== String(expectedSha).toLowerCase()) {
    throw new Error(
      `sha256 mismatch expected=${String(expectedSha).slice(0, 12)} actual=${actualSha.slice(0, 12)}`,
    );
  }
  const { sessionId, segmentId } = await resolveSegmentIdentity({
    archivePath,
    sessionId: hintSessionId,
    segmentId: hintSegmentId,
    fileName,
  });

  if (isSegmentParquetDerivedOnDisk(stationId, sessionId, segmentId)) {
    const disk = computeDeriveStatusFromDisk(stationId, sessionId);
    return {
      duplicate: true,
      sessionId,
      segmentId,
      sha256: actualSha,
      status: disk.phase === "READY" ? "ready" : SEGMENT_STATUS.UPLOADED,
      sla: disk.phase,
      message: "segment already derived on disk",
      job: null,
    };
  }

  const dest = rawSegmentPath(root, sessionId, segmentId);
  const existing = getSegmentState(root, sessionId, segmentId);
  if (fs.existsSync(dest)) {
    const onDiskSha = readJson(rawManifestPath(root, sessionId, segmentId), {}).sha256;
    if (onDiskSha && onDiskSha !== actualSha) {
      throw new Error(`raw exists with different sha256 for ${segmentId}`);
    }
  } else {
    ensureDir(path.dirname(dest));
    fs.renameSync(archivePath, dest);
  }
  if (fs.existsSync(archivePath) && path.resolve(archivePath) !== path.resolve(dest)) {
    try {
      fs.rmSync(archivePath, { force: true });
    } catch {
      /* ignore */
    }
  }

  const bytes = fs.statSync(dest).size;
  writeJsonAtomic(rawManifestPath(root, sessionId, segmentId), {
    sessionId,
    segmentId,
    sha256: actualSha,
    bytes,
    source,
    receivedAt: new Date().toISOString(),
    rawPath: path.relative(root, dest),
  });
  upsertSegmentState(root, sessionId, segmentId, {
    status: SEGMENT_STATUS.UPLOADED,
    sha256: actualSha,
    bytes,
    source,
    storedAt: new Date().toISOString(),
    verifiedAt: new Date().toISOString(),
    uploadedAt: new Date().toISOString(),
    rawPath: path.relative(root, dest),
  });
  touchRawUploadActivity(stationId, {
    sessionId,
    expectedSegmentTotal: Number(expectedSegmentTotal) || 0,
  });

  return {
    duplicate: false,
    sessionId,
    segmentId,
    sha256: actualSha,
    bytes,
    status: SEGMENT_STATUS.UPLOADED,
    sla: "UPLOADED",
    message: "raw stored and verified",
    job: { sessionId, segmentId, sha256: actualSha, source },
  };
}

export function handleDeriveRetry(stationId, sessionId, segmentId) {
  const root = stationRoot(stationId);
  const rawPath = rawSegmentPath(root, sessionId, segmentId);
  if (!fs.existsSync(rawPath)) {
    const err = new Error("raw_not_found");
    err.statusCode = 404;
    throw err;
  }
  const marker = path.join(root, "live", "derive", "markers", sessionId, `${segmentId}.ok.json`);
  try {
    fs.rmSync(marker, { force: true });
  } catch {
    /* ignore */
  }
  upsertSegmentState(root, sessionId, segmentId, {
    status: SEGMENT_STATUS.UPLOADED,
  });
  scheduleDerivePipeline(stationId);
  return {
    queued: true,
    sessionId,
    segmentId,
    status: SEGMENT_STATUS.UPLOADED,
    sla: "UPLOADED",
  };
}

export function resumeDeriveQueuesForAllStations() {
  if (!fs.existsSync(STREAM_ROOT)) return;
  if (isDeriveStandalone()) {
    ensureIdleDeriveWatcher();
    for (const stationId of fs.readdirSync(STREAM_ROOT)) {
      if (stationId.startsWith(".")) continue;
      if (!isDeriveAsyncEnabled(stationId)) continue;
      evaluateAndKickIdleDerive(stationId, "startup");
    }
    streamLog("system", "derive_standalone_resume", { hint: "ego-derive watch" });
    return;
  }
  if (shouldDeferDeriveUntilUpload()) {
    ensureIdleDeriveWatcher();
    for (const stationId of fs.readdirSync(STREAM_ROOT)) {
      if (stationId.startsWith(".")) continue;
      if (!isDeriveAsyncEnabled(stationId)) continue;
      evaluateAndKickIdleDerive(stationId, "startup");
    }
    return;
  }
  for (const stationId of fs.readdirSync(STREAM_ROOT)) {
    if (stationId.startsWith(".")) continue;
    if (!isDeriveAsyncEnabled(stationId)) continue;
    scheduleDerivePipeline(stationId);
  }
}

export {
  computeDeriveStatusFromDisk,
  scheduleDerivePipeline,
  resumeDerivePipelinesForAllStations,
};
