/**
 * Single-process linear derive pipeline (34 async path).
 * Stage 1: per-segment jsonl + incremental parquet (serial)
 * Stage 2: one-shot session MP4 mux
 * External states: UPLOADED | READY only (disk-validated).
 */
import fs from "node:fs";
import path from "node:path";
import { isFullMuxMode } from "./mux-exec.mjs";
import { isSegmentMp4PrimaryPath } from "./segment-mp4-ingest.mjs";
import {
  computeDeriveStatusFromDisk,
  computeStationDeriveStatusFromDisk,
  countDeriveMarkersForSession,
  countRawTarZstForSession,
  exportVideosFromParquetSync,
  hasPendingSessionEpisode,
  isSegmentParquetDerivedOnDisk,
  listRawSessionIds,
  processTarZstDeriveSegment,
  publishStreamViewer,
  purgeAllStagingJpgs,
  refreshSessionEpisodeFromInfo,
  rehydrateStagingFromRawSegments,
  resetMuxArtifactsForFullRemux,
  resetMuxArtifactsForIncrementalRemux,
  planMuxRemux,
  runSessionMuxOnceSync,
  stagingJpegCount,
  stagingNeedsRehydrate,
  stationRoot,
  streamLog,
  spawnFinalizeSessionEpisodeSync,
  syncDataParquetFromJsonl,
  syncEpisodesMetaOnly,
  rebuildSessionJsonlFromRawIfEmpty,
  spawnParquetSyncFromJsonlSync,
  useSessionSingleEpisode,
  usesParquetVideoExport,
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
  const rawRoot = path.join(root, "raw", "segments");
  if (!fs.existsSync(rawRoot)) return null;
  const sessions = fs
    .readdirSync(rawRoot)
    .filter((d) => !d.startsWith(".") && fs.statSync(path.join(rawRoot, d)).isDirectory());
  if (!sessions.length) return null;
  for (const sid of sessions) {
    const raw = countRawTarZstForSession(root, sid);
    const markers = countDeriveMarkersForSession(root, sid);
    if (raw > 0 && markers < raw) return sid;
  }
  for (const sid of sessions) {
    const disk = computeDeriveStatusFromDisk(stationId, sid);
    if (disk.parquetReady && !disk.fullyReady) return sid;
  }
  sessions.sort((a, b) => countRawTarZstForSession(root, b) - countRawTarZstForSession(root, a));
  return sessions[0];
}

async function runMuxStage(stationId, sessionId, { muxOnly = false, inlineRetry = false } = {}) {
  const root = stationRoot(stationId);
  if (isSegmentMp4PrimaryPath()) {
    streamLog(stationId, "derive_mux_skip", {
      sessionId,
      reason: "segment_mp4_ingest",
      source: "derive_pipeline",
    });
    await writeMuxValidatedSnapshot(stationId, sessionId);
    return computeDeriveStatusFromDisk(stationId, sessionId);
  }
  const parquetVideo = usesParquetVideoExport();
  const fullMux = isFullMuxMode() || parquetVideo;
  if (fullMux) inlineRetry = false;
  const maxAttempts = fullMux ? 1 : inlineRetry ? muxMaxRetries() + 1 : 1;
  let lastDisk = computeDeriveStatusFromDisk(stationId, sessionId);

  for (let round = 0; round < maxAttempts; round++) {
    const diskBefore = computeDeriveStatusFromDisk(stationId, sessionId);
    if (!diskBefore.parquetReady || diskBefore.fullyReady) return diskBefore;

    if (!parquetVideo) {
      const plan = planMuxRemux(root);
      if (plan.stagingFrames <= 0) {
        streamLog(stationId, "mux_skip", { sessionId, reason: "no_staging", frames: 0, source: "derive_pipeline" });
        return diskBefore;
      }
      if (plan.fullDatasetMux) {
        resetMuxArtifactsForFullRemux(stationId, root);
      } else {
        resetMuxArtifactsForIncrementalRemux(stationId, root);
      }
    }

    if (parquetVideo) {
      streamLog(stationId, "derive_video_export_start", {
        sessionId,
        parquetRows: diskBefore.parquetRows,
        backend: "lerobot",
      });
      exportVideosFromParquetSync(stationId);
      await writeMuxValidatedSnapshot(stationId, sessionId);
      const disk = computeDeriveStatusFromDisk(stationId, sessionId);
      streamLog(stationId, "derive_video_export_done", {
        sessionId,
        mp4Ok: disk.mp4Ok,
        phase: disk.phase,
      });
      lastDisk = disk;
      if (disk.fullyReady) clearMuxRetryState(root);
      return disk;
    }

    if (fullMux) {
      if (stagingNeedsRehydrate(root)) {
        streamLog(stationId, "mux_full_rehydrate_staging", {
          sessionId,
          stagingJpgs: stagingJpegCount(root),
          expected: diskBefore.parquetRows,
        });
        purgeAllStagingJpgs(root);
        await rehydrateStagingFromRawSegments(stationId, sessionId);
      }
    }

    const attempt = inlineRetry ? round : readMuxRetryAttempt(root);
    if (muxOnly || round > 0) {
      streamLog(stationId, "derive_mux_retry_start", {
        sessionId,
        attempt,
        parquetRows: diskBefore.parquetRows,
        inlineRetry,
      });
    } else {
      streamLog(stationId, "derive_mux_once_start", {
        sessionId,
        parquetRows: diskBefore.parquetRows,
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
        scheduleMuxRetry(stationId, sessionId, attempt, "mux_validation_failed");
      }
      return disk;
    }
    return disk;
  }
  return lastDisk;
}

async function runLinearPipeline(stationId, { muxOnly = false, sessionId: forcedSessionId = null, inlineMuxRetry = false } = {}) {
  const sessionId = forcedSessionId || pickPrimarySessionId(stationId);
  if (!sessionId) return;
  const root = stationRoot(stationId);
  const segments = listUploadedSegments(root, sessionId);
  streamLog(stationId, muxOnly ? "derive_pipeline_mux_only" : "derive_pipeline_start", {
    sessionId,
    segments: segments.length,
    muxOnly,
  });

  if (!muxOnly) {
    for (const seg of segments) {
      const { sessionId: sid, segmentId, sha256 } = seg;
      const archivePath = rawSegmentPath(root, sid, segmentId);
      if (!fs.existsSync(archivePath)) {
        streamLog(stationId, "derive_skip_missing_raw", { sessionId: sid, segmentId });
        continue;
      }
      if (isSegmentParquetDerivedOnDisk(stationId, sid, segmentId)) {
        streamLog(stationId, "derive_skip_done", { sessionId: sid, segmentId });
        continue;
      }
      const segKey = `${sid}:${segmentId}`;
      if (derivingSegments.has(segKey)) {
        streamLog(stationId, "derive_skip_inflight", { sessionId: sid, segmentId });
        continue;
      }
      derivingSegments.add(segKey);
      streamLog(stationId, "derive_segment_start", { sessionId: sid, segmentId });
      try {
        await processTarZstDeriveSegment(archivePath, stationId, {
          expectedSha: sha256,
          sessionId: sid,
          segmentId,
          ingestSource: seg.source || "edge",
        });
      } finally {
        derivingSegments.delete(segKey);
      }
    }

    const allSegmentsDerived =
      segments.length > 0 &&
      segments.every((seg) =>
        isSegmentParquetDerivedOnDisk(stationId, seg.sessionId, seg.segmentId),
      );
    if (
      allSegmentsDerived &&
      useSessionSingleEpisode(stationId) &&
      hasPendingSessionEpisode(root, sessionId)
    ) {
      streamLog(stationId, "session_episode_finalize_start", { sessionId });
      try {
        const finalized = spawnFinalizeSessionEpisodeSync(root, sessionId);
        streamLog(stationId, "session_episode_finalize_done", {
          sessionId,
          skipped: Boolean(finalized.skipped),
          episodeIndex: finalized.episode_index,
          framesCommitted: finalized.frames_committed,
          totalRows: finalized.total_rows,
        });
      } catch (err) {
        streamLog(stationId, "session_episode_finalize_error", {
          sessionId,
          message: String(err?.message || err).slice(0, 300),
        });
        throw err;
      }
    }

    if (isSegmentMp4PrimaryPath()) {
      await rebuildSessionJsonlFromRawIfEmpty(stationId, sessionId);
      try {
        spawnParquetSyncFromJsonlSync(root);
        streamLog(stationId, "derive_parquet_sync_ok", { sessionId });
      } catch (err) {
        streamLog(stationId, "derive_parquet_sync_fail", {
          sessionId,
          message: String(err?.message || err).slice(0, 300),
        });
        throw err;
      }
    }
  }

  let disk = computeDeriveStatusFromDisk(stationId, sessionId);
  if (!muxOnly && disk.markers >= disk.total && disk.total > 0) {
    syncDataParquetFromJsonl(stationId);
    refreshSessionEpisodeFromInfo(stationId, sessionId);
    syncEpisodesMetaOnly(stationId);
    streamLog(stationId, "session_episode_refreshed", {
      sessionId,
      parquetRows: disk.parquetRows,
      frameIndexMin: disk.frameIndexMin,
      frameIndexMax: disk.frameIndexMax,
    });
  }
  if (disk.parquetReady && !disk.fullyReady) {
    disk = await runMuxStage(stationId, sessionId, { muxOnly, inlineRetry: inlineMuxRetry });
  }
  streamLog(stationId, "derive_pipeline_done", {
    sessionId,
    phase: disk.phase,
    markers: disk.markers,
    total: disk.total,
    muxOnly,
  });
  if (disk.fullyReady || disk.phase === "READY") {
    publishStreamViewer(stationId);
  }
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
    const rawDir = path.join(root, "raw", "segments", sessionId);
    const rawCount = fs.existsSync(rawDir)
      ? fs.readdirSync(rawDir).filter((f) => f.endsWith(".tar.zst")).length
      : 0;
    if (rawCount <= 0) {
      return { ok: false, reason: "upload_not_complete", sessionId, hint: "session.DONE_UPLOAD missing" };
    }
    const { markSessionUploadDone: markDone } = await import("./session-markers.mjs");
    markDone(root, sessionId, { source: "cli_implicit", rawCount });
  }

  acquireDeriverLock(root, { stationId, sessionId, muxOnly });
  markSessionDeriving(root, sessionId, { stationId, muxOnly, pid: process.pid });

  try {
    if (pipelineRunning.get(stationId)) {
      return { ok: false, reason: "pipeline_running_in_process", sessionId };
    }
    pipelineRunning.set(stationId, true);
    await runLinearPipeline(stationId, {
      muxOnly,
      sessionId,
      inlineMuxRetry: true,
    });
    const disk = computeDeriveStatusFromDisk(stationId, sessionId);
    if (disk.fullyReady || disk.phase === "READY") {
      markSessionReady(root, sessionId, disk);
      return { ok: true, phase: "READY", sessionId, disk };
    }
    const errMsg = disk.mp4Ok === false ? "mux_validation_failed" : "derive_incomplete";
    markSessionFailed(root, sessionId, errMsg, { disk });
    return { ok: false, phase: disk.phase, sessionId, disk, error: errMsg };
  } catch (err) {
    markSessionFailed(root, sessionId, err?.message || err);
    throw err;
  } finally {
    pipelineRunning.set(stationId, false);
    releaseDeriverLock(root);
  }
}

export { computeDeriveStatusFromDisk, pickPrimarySessionId };
