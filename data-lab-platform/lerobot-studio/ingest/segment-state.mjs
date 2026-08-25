/**
 * Phase3 segment ingest state machine (Foxglove-style).
 * Persisted at state/segments/<sessionId>/<segmentId>.json
 */

import fs from "node:fs";
import path from "node:path";

import { ensureDir, readJson, writeJsonAtomic } from "./io.mjs";

export const SEGMENT_INGEST_STATUS = Object.freeze({
  RECEIVED: "RECEIVED",
  INGESTING: "INGESTING",
  INGEST_FAILED: "INGEST_FAILED",
  DERIVE_PENDING: "DERIVE_PENDING",
});

const TERMINAL_OK = new Set([SEGMENT_INGEST_STATUS.DERIVE_PENDING]);

export function segmentStatePath(root, sessionId, segmentId) {
  return path.join(root, "state", "segments", sessionId, `${segmentId}.json`);
}

export function readSegmentState(root, sessionId, segmentId) {
  return readJson(segmentStatePath(root, sessionId, segmentId), null);
}

export function writeSegmentState(root, sessionId, segmentId, payload) {
  const p = segmentStatePath(root, sessionId, segmentId);
  const body = {
    segment_id: segmentId,
    session_id: sessionId,
    ...payload,
    updated_at: new Date().toISOString(),
  };
  writeJsonAtomic(p, body);
  return body;
}

export function transitionSegmentState(root, sessionId, segmentId, newStatus, patch = {}) {
  const prev = readSegmentState(root, sessionId, segmentId) || {
    segment_id: segmentId,
    session_id: sessionId,
    status: null,
  };
  const next = {
    ...prev,
    ...patch,
    status: newStatus,
    segment_id: segmentId,
    session_id: sessionId,
  };
  if (!prev.received_at && newStatus === SEGMENT_INGEST_STATUS.RECEIVED) {
    next.received_at = new Date().toISOString();
  }
  return writeSegmentState(root, sessionId, segmentId, next);
}

export function markSegmentIngestFailed(root, sessionId, segmentId, error) {
  const reason =
    typeof error === "object" && error
      ? {
          code: error.code || "INGEST_FAILED",
          message: String(error.message || error).slice(0, 500),
          category: error.category || "ingest",
        }
      : {
          code: "INGEST_FAILED",
          message: String(error || "ingest_failed").slice(0, 500),
          category: "ingest",
        };
  return transitionSegmentState(root, sessionId, segmentId, SEGMENT_INGEST_STATUS.INGEST_FAILED, {
    error: reason,
    integrity: { ok: false, issues: [reason.code] },
  });
}

export function listSegmentStates(root, { sessionId, status } = {}) {
  const base = path.join(root, "state", "segments");
  if (!fs.existsSync(base)) return [];
  const items = [];
  const sessionDirs = sessionId
    ? [path.join(base, sessionId)]
    : fs.readdirSync(base).map((name) => path.join(base, name));

  for (const sessDir of sessionDirs) {
    if (!fs.existsSync(sessDir) || !fs.statSync(sessDir).isDirectory()) continue;
    const sid = path.basename(sessDir);
    for (const file of fs.readdirSync(sessDir)) {
      if (!file.endsWith(".json")) continue;
      const segId = file.replace(/\.json$/, "");
      const state = readSegmentState(root, sid, segId);
      if (!state) continue;
      if (status && state.status !== status) continue;
      items.push(state);
    }
  }
  items.sort((a, b) => String(a.segment_id).localeCompare(String(b.segment_id)));
  return items;
}

export function listSessionSegmentStates(root, sessionId) {
  return listSegmentStates(root, { sessionId });
}

export function isSessionIngestComplete(root, sessionId, { expectedTotal = 0 } = {}) {
  const states = listSessionSegmentStates(root, sessionId);
  if (!states.length) return false;
  const pending = states.filter((s) => s.status === SEGMENT_INGEST_STATUS.DERIVE_PENDING);
  if (expectedTotal > 0) {
    return pending.length >= expectedTotal;
  }
  // Single-segment sessions without a declared total remain supported.
  if (states.length === 1 && pending.length === 1) {
    return true;
  }
  return false;
}

export function segmentStateForApi(state) {
  return {
    sessionId: state.session_id,
    segmentId: state.segment_id,
    status: state.status,
    sha256: state.sha256 || null,
    bytes: state.bytes || null,
    rawArchive: state.raw_archive || null,
    frameCount: state.frame_count || null,
    error: state.error || null,
    receivedAt: state.received_at || null,
    updatedAt: state.updated_at || null,
  };
}
