/**
 * P1: defer/fail oversized sessions so mega derives do not block small-session SLA.
 */

import fs from "node:fs";
import path from "node:path";

export function deriveMaxSegmentsPerSession() {
  return Math.max(0, Math.floor(Number(process.env.DERIVE_MAX_SEGMENTS_PER_SESSION || 0)));
}

export function deriveMaxSegmentsAction() {
  const action = String(process.env.DERIVE_MAX_SEGMENTS_ACTION || "defer").trim().toLowerCase();
  return action === "fail" ? "fail" : "defer";
}

export function countSessionSegments(root, sessionId) {
  const rawDir = path.join(root, "raw", "segments", sessionId);
  if (!fs.existsSync(rawDir)) return 0;
  return fs
    .readdirSync(rawDir)
    .filter((name) => name.endsWith(".tar.zst") || name.endsWith(".mcap.zst")).length;
}

export function evaluateSessionSegmentLimit(root, sessionId) {
  const max = deriveMaxSegmentsPerSession();
  const count = countSessionSegments(root, sessionId);
  if (!max || count <= max) {
    return { ok: true, count, max: max || null, action: null, reason: null };
  }
  return {
    ok: false,
    count,
    max,
    action: deriveMaxSegmentsAction(),
    reason: {
      code: "SEGMENT_LIMIT_EXCEEDED",
      message: `session has ${count} segments > limit ${max}`,
      category: "derive",
    },
  };
}

export function filterSessionsWithinSegmentLimit(root, sessionIds) {
  return sessionIds.filter((sessionId) => evaluateSessionSegmentLimit(root, sessionId).ok);
}
