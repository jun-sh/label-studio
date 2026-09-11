/**
 * Server-side session seal (mirrors ego-stream-client session_seal.json).
 * DONE_UPLOAD requires complete seal + all listed segments reconciled.
 */

import path from "node:path";

import { ensureDir, readJson, writeJsonAtomic } from "./io.mjs";
import { listSessionSegmentStates, SEGMENT_INGEST_STATUS } from "./segment-state.mjs";

export function serverSessionSealPath(root, sessionId) {
  return path.join(root, "live", "session_seals", `${sessionId}.json`);
}

export function readServerSessionSeal(root, sessionId) {
  return readJson(serverSessionSealPath(root, sessionId), null);
}

export function parseSessionSealHeader(value) {
  if (!value || typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  try {
    const raw = trimmed.startsWith("{") ? trimmed : Buffer.from(trimmed, "base64").toString("utf8");
    const seal = JSON.parse(raw);
    if (!seal || typeof seal !== "object") return null;
    return seal;
  } catch {
    return null;
  }
}

function segmentIdFromSealEntry(entry) {
  if (!entry || typeof entry !== "object") return null;
  return String(entry.segment_dir || entry.segment_id || "").trim() || null;
}

/**
 * Persist seal from client upload. Rejects conflicting complete seals.
 */
export function writeServerSessionSeal(root, sessionId, seal, { source = "upload" } = {}) {
  if (!seal || typeof seal !== "object") {
    throw new Error("session seal payload required");
  }
  if (!seal.complete) {
    throw new Error("session seal must have complete=true");
  }
  const segmentCount = Number(seal.segment_count || 0);
  const segments = Array.isArray(seal.segments) ? seal.segments : [];
  if (segmentCount <= 0 || segments.length !== segmentCount) {
    throw new Error("session seal segment_count mismatch");
  }

  const existing = readServerSessionSeal(root, sessionId);
  if (existing?.complete) {
    const prevCount = Number(existing.segment_count || 0);
    if (prevCount !== segmentCount) {
      throw new Error(`session seal conflict: segment_count ${prevCount} vs ${segmentCount}`);
    }
    const prevIds = new Set((existing.segments || []).map(segmentIdFromSealEntry).filter(Boolean));
    const nextIds = new Set(segments.map(segmentIdFromSealEntry).filter(Boolean));
    for (const id of nextIds) {
      const prevEnt = (existing.segments || []).find((e) => segmentIdFromSealEntry(e) === id);
      const nextEnt = segments.find((e) => segmentIdFromSealEntry(e) === id);
      const prevSha = String(prevEnt?.sha256 || "").toLowerCase();
      const nextSha = String(nextEnt?.sha256 || "").toLowerCase();
      if (prevSha && nextSha && prevSha !== nextSha) {
        throw new Error(`session seal sha256 conflict for ${id}`);
      }
    }
    if (prevIds.size !== nextIds.size) {
      throw new Error("session seal segment list conflict");
    }
  }

  ensureDir(path.dirname(serverSessionSealPath(root, sessionId)));
  const body = {
    ...seal,
    session_id: sessionId,
    received_at: new Date().toISOString(),
    source,
  };
  writeJsonAtomic(serverSessionSealPath(root, sessionId), body);
  return body;
}

/**
 * Verify seal against ingested segment states.
 */
export function reconcileSealWithSegmentStates(root, sessionId) {
  const seal = readServerSessionSeal(root, sessionId);
  if (!seal?.complete) {
    return { ok: false, reason: "no_complete_seal", issues: [{ code: "no_complete_seal" }] };
  }

  const expected = Number(seal.segment_count || 0);
  const states = listSessionSegmentStates(root, sessionId);
  const failed = states.filter((s) => s.status === SEGMENT_INGEST_STATUS.INGEST_FAILED);
  if (failed.length) {
    return {
      ok: false,
      reason: "ingest_failed",
      issues: failed.map((s) => ({ code: "ingest_failed", segmentId: s.segment_id })),
      seal,
      expected,
    };
  }

  const byId = new Map(states.map((s) => [s.segment_id, s]));
  const issues = [];
  const sealEntries = Array.isArray(seal.segments) ? seal.segments : [];

  for (const ent of sealEntries) {
    const segId = segmentIdFromSealEntry(ent);
    if (!segId) {
      issues.push({ code: "invalid_seal_entry" });
      continue;
    }
    const st = byId.get(segId);
    if (!st || st.status !== SEGMENT_INGEST_STATUS.DERIVE_PENDING) {
      issues.push({ code: "missing_segment", segmentId: segId });
      continue;
    }
    const sealSha = String(ent.sha256 || ent.content_sha256 || "").toLowerCase();
    const stateSha = String(st.sha256 || "").toLowerCase();
    if (sealSha && stateSha && sealSha !== stateSha) {
      issues.push({ code: "sha256_mismatch", segmentId: segId });
    }
  }

  const pending = states.filter((s) => s.status === SEGMENT_INGEST_STATUS.DERIVE_PENDING);
  if (pending.length < expected) {
    issues.push({
      code: "pending_shortfall",
      have: pending.length,
      need: expected,
    });
  }

  return {
    ok: issues.length === 0,
    reason: issues.length ? "seal_reconcile_failed" : "ok",
    issues,
    seal,
    expected,
    pendingCount: pending.length,
  };
}

export function makeTestSessionSeal(sessionId, segmentIds, { sha256BySegment = {} } = {}) {
  const segments = segmentIds.map((segmentId, idx) => ({
    segment_dir: segmentId,
    segment_index: idx,
    frame_count: 1,
    status: "CLOSED",
    sha256: sha256BySegment[segmentId] || null,
  }));
  return {
    schema_version: 1,
    session_id: sessionId,
    complete: true,
    segment_count: segments.length,
    segments,
    written_at: new Date().toISOString(),
  };
}
