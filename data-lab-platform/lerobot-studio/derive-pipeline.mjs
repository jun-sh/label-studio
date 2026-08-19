/**
 * Single-process linear derive pipeline (34 async path).
 * Stage 1: per-segment jsonl + incremental parquet (serial)
 * Stage 2: one-shot session MP4 mux
 * External states: UPLOADED | READY only (disk-validated).
 */
import fs from "node:fs";
import path from "node:path";
import { isFullMuxMode } from "./mux-exec.mjs";
import {
  computeDeriveStatusFromDisk,
  computeStationDeriveStatusFromDisk,
  countDeriveMarkersForSession,
  countRawTarZstForSession,
  countCommittedSegmentsForSession,
  hasPendingSessionEpisode,
  isSegmentParquetDerivedOnDisk,
  listRawSessionIds,
  listSessionIdsOnDisk,
  prepareStagingForFullMux,
  processTarZstDeriveSegment,
  publishStreamViewer,
  refreshSessionEpisodeFromInfo,
  resetMuxArtifactsForFullRemux,
  runSessionMuxOnceSync,
  stagingJpegCount,
  stagingNeedsRehydrate,
  stationRoot,
  streamLog,
  spawnFinalizeSessionEpisodeSync,
  syncDataParquetFromJsonl,
  syncEpisodesMetaOnly,
  useSessionSingleEpisode,
  deriveVideoExportBackend,
  writeMuxValidatedSnapshot,
  STREAM_ROOT,
} from "./stream-ingest.mjs";

const pipelineRunning = new Map();
/** Per-segment guard against concurrent derive of the same tar.zst (e.g. double container restart). */
const derivingSegments = new Set();
/** @type {Map<string, NodeJS.Timeout>} */
const muxRetryTimers = new Map();

function rawSegmentPath(root, sessionId, segmentId) {
  return path.join(root, "raw", "segments", sessionId, `${segmentId}.tar.zst`);
}

function readJson(p, fallback) {
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return fallback;
  }
}

function writeJsonAtomic(p, obj) {
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const tmp = `${p}.tmp.${process.pid}.${Date.now()}`;
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2));
  fs.renameSync(tmp, p);
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

function scheduleMuxRetry(stationId, sessionId, attempt, lastError) {
  if (!shouldMuxAutoRetry()) return false;
  const root = stationRoot(stationId);
  if (attempt >= muxMaxRetries()) {
    writeJsonAtomic(muxLastFailurePath(root), {
      sessionId,
      attempt,
      message: String(lastError || "mux_validation_failed").slice(0, 500),
      at: new Date().toISOString(),
    });
    streamLog(stationId, "mux_retry_exhausted", { sessionId, attempt, maxRetries: muxMaxRetries() });
    return false;
  }
  const delayMs = muxRetryBaseMs() * 2 ** attempt;
  writeJsonAtomic(muxRetryStatePath(root), {
    sessionId,
    attempt: attempt + 1,
    nextRetryAt: new Date(Date.now() + delayMs).toISOString(),
    lastError: String(lastError || "mux_validation_failed").slice(0, 300),
  });
  const prev = muxRetryTimers.get(stationId);
  if (prev) clearTimeout(prev);
  const timer = setTimeout(() => {
    muxRetryTimers.delete(stationId);
    scheduleDerivePipeline(stationId, { muxOnly: true });
    streamLog(stationId, "mux_retry_scheduled", {
      sessionId,
      attempt: attempt + 1,
      delayMs,
    });
  }, delayMs);
  muxRetryTimers.set(stationId, timer);
  return true;
}

function segmentStatePath(root) {
  return path.join(root, "state", "segments.json");
}

function listUploadedSegments(root, sessionId) {
  const store = readJson(segmentStatePath(root), { segments: {} });
  const items = Object.values(store.segments || {}).filter(
    (s) => s.sessionId === sessionId && s.status === "verified",
  );
  items.sort((a, b) => String(a.segmentId).localeCompare(String(b.segmentId)));
  if (items.length > 0) return items;
  const rawDir = path.join(root, "raw", "segments", sessionId);
  if (!fs.existsSync(rawDir)) return [];
  return fs
    .readdirSync(rawDir)
    .filter((f) => f.endsWith(".tar.zst"))
    .map((f) => {
      const segmentId = f.replace(/\.tar\.zst$/, "");
      const manifest = readJson(
        path.join(root, "raw", "manifest", sessionId, `${segmentId}.json`),
        {},
      );
      return {
        sessionId,
        segmentId,
        status: "verified",
        sha256: manifest.sha256,
      };
    })
    .sort((a, b) => String(a.segmentId).localeCompare(String(b.segmentId)));
}

function pickPrimarySessionId(stationId) {
  const root = stationRoot(stationId);
  const live = readJson(path.join(root, "live", "session.json"), {});
  if (live.sessionId) {
    const disk = computeDeriveStatusFromDisk(stationId, live.sessionId);
    if (disk.total > 0 && !disk.fullyReady) return live.sessionId;
  }
  for (const sid of listSessionIdsOnDisk(root)) {
    const disk = computeDeriveStatusFromDisk(stationId, sid);
    if (disk.total > 0 && !disk.fullyReady) return sid;
  }
  return live.sessionId || listSessionIdsOnDisk(root)[0] || null;
}

async function runStagingMuxDerive(stationId, sessionId, diskBefore, { muxOnly = false, inlineRetry = false, round = 0 } = {}) {
  const root = stationRoot(stationId);
  resetMuxArtifactsForFullRemux(stationId, root);
  await prepareStagingForFullMux(stationId, sessionId);
  const attempt = inlineRetry ? round : readMuxRetryAttempt(root);
  if (muxOnly || round > 0) {
    streamLog(stationId, "derive_mux_retry_start", {
      sessionId,
      attempt,
      parquetRows: diskBefore.parquetRows,
      inlineRetry,
      backend: "staging",
    });
  } else {
    streamLog(stationId, "derive_mux_once_start", {
      sessionId,
      parquetRows: diskBefore.parquetRows,
      backend: "staging",
    });
  }
  await runSessionMuxOnceSync(stationId);
  await writeMuxValidatedSnapshot(stationId, sessionId);
  const disk = computeDeriveStatusFromDisk(stationId, sessionId);
  streamLog(stationId, muxOnly || round > 0 ? "derive_mux_retry_done" : "derive_mux_once_done", {
    sessionId,
    mp4Ok: disk.mp4Ok,
    phase: disk.phase,
    attempt: muxOnly || round > 0 ? attempt : undefined,
    backend: "staging",
  });
  return disk;
}

async function runMuxStage(stationId, sessionId, { muxOnly = false, inlineRetry = false } = {}) {
  const root = stationRoot(stationId);
  const fullMux = isFullMuxMode();
  if (fullMux) inlineRetry = false;
  const maxAttempts = fullMux ? 1 : inlineRetry ? muxMaxRetries() + 1 : 1;
  let lastDisk = computeDeriveStatusFromDisk(stationId, sessionId);

  for (let round = 0; round < maxAttempts; round++) {
    const diskBefore = computeDeriveStatusFromDisk(stationId, sessionId);
    if (!diskBefore.parquetReady || diskBefore.fullyReady) return diskBefore;

    if (fullMux && stagingNeedsRehydrate(root)) {
      streamLog(stationId, "mux_full_rehydrate_staging", {
        sessionId,
        stagingJpgs: stagingJpegCount(root),
        expected: diskBefore.parquetRows,
      });
      await prepareStagingForFullMux(stationId, sessionId);
    }

    const disk = await runStagingMuxDerive(stationId, sessionId, diskBefore, {
      muxOnly,
      inlineRetry,
      round,
    });
    lastDisk = disk;
    if (disk.fullyReady) {
      clearMuxRetryState(root);
      return disk;
    }
    if (!disk.mp4Ok) {
      if (fullMux) {
        streamLog(stationId, "mux_full_failed", { sessionId, mp4Ok: false });
        return disk;
      }
      if (inlineRetry && round + 1 < maxAttempts) {
        const delayMs = muxRetryBaseMs() * 2 ** round;
        streamLog(stationId, "mux_retry_scheduled", { sessionId, attempt: round + 1, delayMs, inlineRetry: true });
        await new Promise((r) => setTimeout(r, delayMs));
        continue;
      }
      if (!inlineRetry) {
        scheduleMuxRetry(stationId, sessionId, readMuxRetryAttempt(root), "mux_validation_failed");
      }
      return disk;
    }
    return disk;
  }
  return lastDisk;
}

async function runLinearPipeline(stationId, { muxOnly = false, sessionId: forcedSessionId = null, inlineMuxRetry = false } = {}) {
  const { runDerivePipeline } = await import("./derive/pipeline.mjs");
  const result = await runDerivePipeline(stationId, {
    sessionId: forcedSessionId,
    muxOnly,
  });
  if (result.reason === "no_session" || result.reason === "done_upload_missing" || result.reason === "empty_frame_map") {
    streamLog(stationId, "derive_pipeline_skip", result);
    return result;
  }
  streamLog(stationId, result.phase === "READY" ? "derive_pipeline_ready" : "derive_pipeline_failed", {
    sessionId: result.sessionId,
    phase: result.phase,
    frames: result.frameMap?.length,
    muxValidated: result.mux?.validated?.ok,
    reasonCode: result.reason?.code,
    muxOnly,
  });
  return result;
}

export function scheduleDerivePipeline(stationId, { muxOnly = false } = {}) {
  if (String(process.env.DERIVE_STANDALONE || "0").trim() === "1") {
    streamLog(stationId, "derive_in_process_skipped", {
      reason: "DERIVE_STANDALONE",
      hint: "ego-derive run",
      muxOnly,
    });
    return;
  }
  if (pipelineRunning.get(stationId)) return;
  setImmediate(() => {
    if (pipelineRunning.get(stationId)) return;
    pipelineRunning.set(stationId, true);
    runLinearPipeline(stationId, { muxOnly })
      .catch((err) => {
        streamLog(stationId, "derive_pipeline_fail", {
          message: String(err?.message || err).slice(0, 300),
          muxOnly,
        });
      })
      .finally(() => {
        pipelineRunning.set(stationId, false);
        const root = stationRoot(stationId);
        const stationDisk = computeStationDeriveStatusFromDisk(stationId);
        if (stationDisk.phase === "READY" || stationDisk.total <= 0) return;
        const pendingDerive = listRawSessionIds(root).some((sid) =>
          listUploadedSegments(root, sid).some(
            (s) => !isSegmentParquetDerivedOnDisk(stationId, s.sessionId, s.segmentId),
          ),
        );
        if (pendingDerive) {
          scheduleDerivePipeline(stationId);
        }
      });
  });
}

export function resumeDerivePipelinesForAllStations() {
  if (!fs.existsSync(STREAM_ROOT)) return;
  for (const stationId of fs.readdirSync(STREAM_ROOT)) {
    if (stationId.startsWith(".")) continue;
    scheduleDerivePipeline(stationId);
  }
}

export function getDerivePipelineRunning(stationId) {
  return Boolean(pipelineRunning.get(stationId));
}

/**
 * Blocking derive for standalone CLI / derive-worker container.
 * Acquires deriver.lock, writes session markers, runs full pipeline inline.
 */
export async function runDerivePipelineBlocking(
  stationId,
  { sessionId: forcedSessionId = null, muxOnly = false } = {},
) {
  const { acquireDeriverLock, releaseDeriverLock } = await import("./derive-lock.mjs");
  const {
    markSessionDeriving,
    markSessionFailed,
    markSessionReady,
    hasSessionMarker,
    SESSION_MARKERS,
  } = await import("./session-markers.mjs");

  const root = stationRoot(stationId);
  const sessionId = forcedSessionId || pickPrimarySessionId(stationId);
  if (!sessionId) {
    return { ok: false, reason: "no_session", stationId };
  }
  if (!muxOnly && !hasSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD)) {
    const committed = countCommittedSegmentsForSession(root, sessionId);
    const rawCount = countRawTarZstForSession(root, sessionId);
    if (committed <= 0 && rawCount <= 0) {
      return {
        ok: false,
        reason: "upload_not_complete",
        sessionId,
        hint: "session.DONE_UPLOAD missing",
      };
    }
    const { markSessionUploadDone: markDone } = await import("./session-markers.mjs");
    markDone(root, sessionId, { source: "cli_implicit", committed, rawCount });
  }

  acquireDeriverLock(root, { stationId, sessionId, muxOnly });
  markSessionDeriving(root, sessionId, { stationId, muxOnly, pid: process.pid });

  try {
    if (pipelineRunning.get(stationId)) {
      return { ok: false, reason: "pipeline_running_in_process", sessionId };
    }
    pipelineRunning.set(stationId, true);
    const result = await runLinearPipeline(stationId, {
      muxOnly,
      sessionId,
      inlineMuxRetry: true,
    });
    if (result?.phase === "READY") {
      return { ok: true, phase: "READY", sessionId, gate: result.gate, disk: computeDeriveStatusFromDisk(stationId, sessionId) };
    }
    if (result?.phase === "FAILED") {
      return {
        ok: false,
        phase: "FAILED",
        sessionId,
        reason: result.reason,
        gate: result.gate,
        disk: computeDeriveStatusFromDisk(stationId, sessionId),
      };
    }
    return { ok: Boolean(result?.ok), sessionId, ...result };
  } catch (err) {
    // Phase4: no session.FAILED marker (Phase5 ready-gate).
    throw err;
  } finally {
    pipelineRunning.set(stationId, false);
    releaseDeriverLock(root);
  }
}

export { computeDeriveStatusFromDisk, pickPrimarySessionId };
