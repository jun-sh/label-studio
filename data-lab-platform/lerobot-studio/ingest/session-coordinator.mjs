/**
 * Session-level upload completion gate (Phase0 §3.3).
 * Writes session.DONE_UPLOAD only when all segments are DERIVE_PENDING.
 */

import path from "node:path";

import { markSessionUploadDone } from "../session-markers.mjs";
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
  if (!gate.complete) {
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
