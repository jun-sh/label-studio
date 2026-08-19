/**
 * Staging lifecycle GC (Phase0 §9.1).
 * Staging lifecycle GC (Phase0 §9.1) — purge only at session terminal state.
 */

import fs from "node:fs";
import path from "node:path";

import { readJson, writeJsonAtomic } from "./io.mjs";
import { mainJsonlPath, mainParquetPath } from "./parquet-writer.mjs";
import { videoKeysForStation } from "../ingest/staging-materialize.mjs";
import {
  hasSessionMarker,
  readSessionMarker,
  SESSION_MARKERS,
} from "../session-markers.mjs";
import { deriveLog } from "./station-context.mjs";

export const FAILED_STAGING_RETENTION_MS = 24 * 60 * 60 * 1000;

function stagingDir(root, videoKey) {
  return path.join(root, "_staging", videoKey.replace(/\./g, "_"));
}

function countStagingJpgs(root, stationId) {
  const keys = stationId ? videoKeysForStation(stationId) : [];
  let count = 0;
  for (const key of keys) {
    const dir = stagingDir(root, key);
    if (!fs.existsSync(dir)) continue;
    for (const f of fs.readdirSync(dir)) {
      if (/^frame_\d+\.jpg$/.test(f)) count += 1;
    }
  }
  return count;
}

function purgeStagingJpgsInDir(dirPath) {
  if (!fs.existsSync(dirPath)) return 0;
  let removed = 0;
  for (const f of fs.readdirSync(dirPath)) {
    if (!/^frame_\d+\.jpg$/.test(f)) continue;
    try {
      fs.unlinkSync(path.join(dirPath, f));
      removed += 1;
    } catch {
      /* ignore */
    }
  }
  return removed;
}

export function purgeAllStagingJpgs(root, stationId) {
  const keys = stationId ? videoKeysForStation(stationId) : [];
  let removed = 0;
  for (const key of keys) {
    removed += purgeStagingJpgsInDir(stagingDir(root, key));
  }
  return removed;
}

function derivedArtifactsPresent(root) {
  const mainOk =
    (fs.existsSync(mainParquetPath(root)) && fs.statSync(mainParquetPath(root)).size > 0) ||
    (fs.existsSync(mainJsonlPath(root)) && fs.statSync(mainJsonlPath(root)).size > 0);
  const muxVal = readJson(path.join(root, "live", "derive", "mux_validated.json"), null);
  return mainOk && Boolean(muxVal);
}

function failedRetentionElapsed(failedMarker) {
  if (!failedMarker?.at) return false;
  const failedAt = Date.parse(failedMarker.at);
  if (!Number.isFinite(failedAt)) return false;
  return Date.now() - failedAt >= FAILED_STAGING_RETENTION_MS;
}

/**
 * Whether staging purge is allowed for this session state.
 */
export function evaluateStagingGc(root, sessionId, stationId) {
  if (hasSessionMarker(root, sessionId, SESSION_MARKERS.DERIVING)) {
    return { allowed: false, reason: "session_deriving" };
  }
  if (hasSessionMarker(root, sessionId, SESSION_MARKERS.READY)) {
    if (!derivedArtifactsPresent(root)) {
      return { allowed: false, reason: "artifacts_incomplete" };
    }
    return { allowed: true, reason: "session_ready" };
  }
  if (hasSessionMarker(root, sessionId, SESSION_MARKERS.FAILED)) {
    const failedMarker = readSessionMarker(root, sessionId, SESSION_MARKERS.FAILED);
    if (!failedRetentionElapsed(failedMarker)) {
      return { allowed: false, reason: "failed_retention_24h" };
    }
    return { allowed: true, reason: "failed_retention_elapsed" };
  }
  return { allowed: false, reason: "not_terminal" };
}

/**
 * Run staging GC if policy allows. Returns { purged, removed, ... }.
 */
export function runLifecycleGc(root, stationId, sessionId) {
  const stagingBefore = countStagingJpgs(root, stationId);
  const gate = evaluateStagingGc(root, sessionId, stationId);
  if (!gate.allowed) {
    deriveLog(stationId, "lifecycle_gc_skip", { sessionId, reason: gate.reason, stagingJpgs: stagingBefore });
    return { purged: false, removed: 0, reason: gate.reason, stagingBefore };
  }
  const removed = purgeAllStagingJpgs(root, stationId);
  const gcMetaPath = path.join(root, "live", "derive", "staging_gc.json");
  writeJsonAtomic(gcMetaPath, {
    sessionId,
    at: new Date().toISOString(),
    reason: gate.reason,
    removedJpgs: removed,
  });
  deriveLog(stationId, "lifecycle_gc_done", { sessionId, reason: gate.reason, removedJpgs: removed });
  return { purged: true, removed, reason: gate.reason, stagingBefore };
}
