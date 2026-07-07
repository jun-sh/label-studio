/**
 * Disk-only session lifecycle markers (commercial derive worker contract).
 * Upload service writes DONE_UPLOAD; derive CLI writes DERIVING / READY / FAILED.
 */
import fs from "node:fs";
import path from "node:path";

export const SESSION_MARKERS = {
  DONE_UPLOAD: "session.DONE_UPLOAD",
  DERIVING: "session.DERIVING",
  READY: "session.READY",
  FAILED: "session.FAILED",
};

export function sessionStateDir(root, sessionId) {
  return path.join(root, "state", "sessions", sessionId);
}

export function sessionMarkerPath(root, sessionId, marker) {
  return path.join(sessionStateDir(root, sessionId), marker);
}

function writeJsonAtomic(p, obj) {
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const tmp = `${p}.tmp.${process.pid}.${Date.now()}`;
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2));
  fs.renameSync(tmp, p);
}

export function readSessionMarker(root, sessionId, marker) {
  const p = sessionMarkerPath(root, sessionId, marker);
  if (!fs.existsSync(p)) return null;
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return { at: null, raw: true };
  }
}

export function hasSessionMarker(root, sessionId, marker) {
  return fs.existsSync(sessionMarkerPath(root, sessionId, marker));
}

export function writeSessionMarker(root, sessionId, marker, meta = {}) {
  const p = sessionMarkerPath(root, sessionId, marker);
  writeJsonAtomic(p, {
    sessionId,
    marker,
    at: new Date().toISOString(),
    ...meta,
  });
}

export function clearSessionMarker(root, sessionId, marker) {
  try {
    fs.rmSync(sessionMarkerPath(root, sessionId, marker), { force: true });
  } catch {
    /* ignore */
  }
}

export function markSessionUploadDone(root, sessionId, meta = {}) {
  if (hasSessionMarker(root, sessionId, SESSION_MARKERS.READY)) return;
  writeSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD, meta);
}

export function markSessionDeriving(root, sessionId, meta = {}) {
  clearSessionMarker(root, sessionId, SESSION_MARKERS.FAILED);
  writeSessionMarker(root, sessionId, SESSION_MARKERS.DERIVING, meta);
}

export function markSessionReady(root, sessionId, disk = {}) {
  clearSessionMarker(root, sessionId, SESSION_MARKERS.DERIVING);
  clearSessionMarker(root, sessionId, SESSION_MARKERS.FAILED);
  writeSessionMarker(root, sessionId, SESSION_MARKERS.READY, {
    phase: "READY",
    markers: disk.markers,
    total: disk.total,
    parquetRows: disk.parquetRows,
    mp4Ok: disk.mp4Ok,
  });
}

export function markSessionFailed(root, sessionId, error, meta = {}) {
  clearSessionMarker(root, sessionId, SESSION_MARKERS.DERIVING);
  writeSessionMarker(root, sessionId, SESSION_MARKERS.FAILED, {
    message: String(error || "derive_failed").slice(0, 500),
    ...meta,
  });
}

/** Sessions with upload complete marker pending derive (no READY / in-flight DERIVING). */
export function listSessionsPendingDerive(root) {
  const base = path.join(root, "state", "sessions");
  if (!fs.existsSync(base)) return [];
  const pending = [];
  for (const sessionId of fs.readdirSync(base)) {
    if (sessionId.startsWith(".")) continue;
    const dir = path.join(base, sessionId);
    if (!fs.statSync(dir).isDirectory()) continue;
    if (!hasSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD)) continue;
    if (hasSessionMarker(root, sessionId, SESSION_MARKERS.READY)) continue;
    if (hasSessionMarker(root, sessionId, SESSION_MARKERS.DERIVING)) continue;
    pending.push(sessionId);
  }
  return pending.sort();
}
