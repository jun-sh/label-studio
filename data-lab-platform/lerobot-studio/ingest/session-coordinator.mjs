/**
 * Session-level upload completion gate (Phase0 §3.3).
 * Writes session.DONE_UPLOAD only when all segments are DERIVE_PENDING.
 */

import path from "node:path";

import {
  clearSessionMarker,
  hasSessionMarker,
  markSessionUploadDone,
  readSessionMarker,
  SESSION_MARKERS,
} from "../session-markers.mjs";
import { ensureDir, readJson, writeJsonAtomic } from "./io.mjs";
import { isSessionIngestComplete, listSessionSegmentStates, SEGMENT_INGEST_STATUS } from "./segment-state.mjs";

function uploadActivityPath(root) {
  return path.join(root, "live", "upload_activity.json");
}

export function touchUploadActivity(root, { sessionId, expectedSegmentTotal = 0 } = {}) {
  ensureDir(path.dirname(uploadActivityPath(root)));
  const prev = readJson(uploadActivityPath(root), {});
  const sessions = { ...(prev.sessions || {}) };
  const ent = { ...(sessions[sessionId] || {}) };
  ent.updatedAt = new Date().toISOString();
  if (expectedSegmentTotal > 0) {
    ent.expectedSegmentTotal = Math.max(Number(ent.expectedSegmentTotal || 0), expectedSegmentTotal);
  }
  sessions[sessionId] = ent;
  writeJsonAtomic(uploadActivityPath(root), {
    ...prev,
    sessions,
    updatedAt: new Date().toISOString(),
  });
}

export function readExpectedSegmentTotal(root, sessionId) {
  const activity = readJson(uploadActivityPath(root), {});
  const ent = activity.sessions?.[sessionId] || {};
  return Number(ent.expectedSegmentTotal || 0);
}

export function evaluateSessionUploadGate(root, sessionId) {
  const expected = readExpectedSegmentTotal(root, sessionId);
  const states = listSessionSegmentStates(root, sessionId);
  const derivePending = states.filter((s) => s.status === SEGMENT_INGEST_STATUS.DERIVE_PENDING);
  const failed = states.filter((s) => s.status === SEGMENT_INGEST_STATUS.INGEST_FAILED);
  const complete = isSessionIngestComplete(root, sessionId, { expectedTotal: expected });
  return {
    sessionId,
    expectedTotal: expected,
    segmentCount: states.length,
    derivePendingCount: derivePending.length,
    failedCount: failed.length,
    complete,
  };
}

export function maybeMarkSessionDoneUpload(root, sessionId, meta = {}) {
  const gate = evaluateSessionUploadGate(root, sessionId);
  if (!gate.complete || gate.failedCount > 0) {
    return { marked: false, gate };
  }
  markSessionUploadDone(root, sessionId, {
    source: "ingest_module",
    phase: "DONE_UPLOAD",
    ...meta,
    gate,
  });
  return { marked: true, gate };
}

/**
 * Reconcile session.DONE_UPLOAD when segment ingest state no longer matches.
 * Clears stale DONE_UPLOAD if segments failed or are incomplete.
 */
export function reconcileSessionUploadMarkers(root, sessionId) {
  const actions = [];
  const gate = evaluateSessionUploadGate(root, sessionId);
  const hasDone = hasSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD);
  const hasReady = hasSessionMarker(root, sessionId, SESSION_MARKERS.READY);
  if (hasReady) {
    return { sessionId, actions, gate, phase: "READY" };
  }
  if (hasDone && (gate.failedCount > 0 || (!gate.complete && gate.segmentCount > 0))) {
    clearSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD);
    actions.push("cleared_stale_done_upload");
  }
  if (hasSessionMarker(root, sessionId, SESSION_MARKERS.FAILED)) {
    const failed = readSessionMarker(root, sessionId, SESSION_MARKERS.FAILED);
    return { sessionId, actions, gate, phase: "FAILED", failed };
  }
  if (hasDone && gate.complete) {
    return { sessionId, actions, gate, phase: "DONE_UPLOAD" };
  }
  return { sessionId, actions, gate, phase: gate.complete ? "INGEST_COMPLETE" : "INGESTING" };
}
