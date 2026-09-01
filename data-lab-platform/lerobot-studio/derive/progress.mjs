/**
 * Crash-safe derive progress (live/derive/progress/{sessionId}.json).
 */

import fs from "node:fs";
import path from "node:path";

import { renewDeriverLock } from "../derive-lock.mjs";

export const DERIVE_PROGRESS_PHASES = Object.freeze({
  EXTRACT: "EXTRACT",
  TABLE: "TABLE",
  MUX_ENCODE: "MUX_ENCODE",
  MUX_REMUX: "MUX_REMUX",
  MUX_MERGE: "MUX_MERGE",
  MUX_VALIDATE: "MUX_VALIDATE",
  READY: "READY",
  FAILED: "FAILED",
});

function progressDir(root) {
  return path.join(root, "live", "derive", "progress");
}

export function deriveProgressPath(root, sessionId) {
  return path.join(progressDir(root), `${sessionId}.json`);
}

export function deriveHeartbeatTimeoutSec() {
  return Math.max(60, Number(process.env.DERIVE_HEARTBEAT_TIMEOUT_S || 900));
}

export function readDeriveProgress(root, sessionId) {
  if (!sessionId) return null;
  const p = deriveProgressPath(root, sessionId);
  if (!fs.existsSync(p)) return null;
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return null;
  }
}

export function writeDeriveProgress(root, sessionId, patch = {}) {
  if (!sessionId) return null;
  const dir = progressDir(root);
  fs.mkdirSync(dir, { recursive: true });
  const prev = readDeriveProgress(root, sessionId) || {};
  const payload = {
    version: 1,
    sessionId,
    stationId: patch.stationId ?? prev.stationId ?? null,
    phase: patch.phase ?? prev.phase ?? DERIVE_PROGRESS_PHASES.EXTRACT,
    camera: patch.camera ?? prev.camera ?? null,
    done: patch.done ?? prev.done ?? 0,
    total: patch.total ?? prev.total ?? 0,
    message: patch.message ?? prev.message ?? null,
    startedAt: patch.startedAt ?? prev.startedAt ?? new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    pid: process.pid,
    workerId: process.env.DERIVE_WORKER_ID || process.env.HOSTNAME || "derive-worker",
    ...patch,
  };
  payload.updatedAt = new Date().toISOString();
  const p = deriveProgressPath(root, sessionId);
  const tmp = `${p}.tmp.${process.pid}.${Date.now()}`;
  fs.writeFileSync(tmp, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  fs.renameSync(tmp, p);
  try {
    renewDeriverLock(root, { sessionId, workerId: payload.workerId, stationId: payload.stationId });
  } catch {
    /* ignore — lock renew is best-effort */
  }
  return payload;
}

export function clearDeriveProgress(root, sessionId) {
  if (!sessionId) return;
  try {
    fs.rmSync(deriveProgressPath(root, sessionId), { force: true });
  } catch {
    /* ignore */
  }
}

export function isDeriveProgressStale(root, sessionId, { timeoutSec = deriveHeartbeatTimeoutSec() } = {}) {
  const progress = readDeriveProgress(root, sessionId);
  if (!progress?.updatedAt) return false;
  const ageMs = Date.now() - Date.parse(progress.updatedAt);
  if (!Number.isFinite(ageMs) || ageMs < 0) return true;
  return ageMs > timeoutSec * 1000;
}

export function deriveProgressPercent(progress) {
  if (!progress) return 0;
  const total = Number(progress.total) || 0;
  const done = Number(progress.done) || 0;
  if (total <= 0) return progress.phase === DERIVE_PROGRESS_PHASES.READY ? 100 : 0;
  if (progress.phase === DERIVE_PROGRESS_PHASES.READY) return 100;
  return Math.min(99, Math.round((done / total) * 100));
}
