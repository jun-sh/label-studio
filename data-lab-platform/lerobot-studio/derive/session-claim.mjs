/**
 * Q1-1: per-session derive claims (multi-worker pending queue guard).
 * Files: state/derive-claims/{sessionId}.json
 */

import fs from "node:fs";
import path from "node:path";

import { readJson, writeJsonAtomic } from "./io.mjs";

const CLAIM_ATTEMPTS = 5;

export function deriveClaimsDir(root) {
  return path.join(root, "state", "derive-claims");
}

export function sessionClaimPath(root, sessionId) {
  return path.join(deriveClaimsDir(root), `${sessionId}.json`);
}

export function deriveClaimLeaseSec() {
  return Math.max(60, Number(process.env.DERIVE_CLAIM_LEASE_S || 600));
}

export function resolveDeriveWorkerId() {
  return String(process.env.DERIVE_WORKER_ID || process.env.HOSTNAME || `derive-worker-${process.pid}`).trim();
}

export function readSessionClaim(root, sessionId) {
  return readJson(sessionClaimPath(root, sessionId), null);
}

export function isSessionClaimActive(claim, { nowMs = Date.now() } = {}) {
  if (!claim?.leaseExpiresAt) return false;
  const expiresMs = Date.parse(claim.leaseExpiresAt);
  if (!Number.isFinite(expiresMs)) return false;
  return expiresMs > nowMs;
}

export function isSessionClaimedByOther(root, sessionId, workerId) {
  const claim = readSessionClaim(root, sessionId);
  if (!isSessionClaimActive(claim)) return false;
  return String(claim.workerId || "") !== String(workerId || "");
}

function buildClaimPayload(sessionId, workerId, leaseSec) {
  const nowMs = Date.now();
  const leaseMs = Math.max(60, Number(leaseSec || deriveClaimLeaseSec())) * 1000;
  return {
    version: 1,
    sessionId,
    workerId: String(workerId),
    claimedAt: new Date(nowMs).toISOString(),
    leaseExpiresAt: new Date(nowMs + leaseMs).toISOString(),
    pid: process.pid,
  };
}

function writeExclusiveClaim(claimPath, payload) {
  fs.mkdirSync(path.dirname(claimPath), { recursive: true });
  const fd = fs.openSync(claimPath, "wx");
  try {
    fs.writeFileSync(fd, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  } finally {
    fs.closeSync(fd);
  }
}

export function renewSessionClaim(root, sessionId, workerId, { leaseSec = deriveClaimLeaseSec() } = {}) {
  const existing = readSessionClaim(root, sessionId);
  if (!existing || String(existing.workerId || "") !== String(workerId || "")) {
    return { ok: false, reason: "not_owner", claim: existing };
  }
  const payload = buildClaimPayload(sessionId, workerId, leaseSec);
  writeJsonAtomic(sessionClaimPath(root, sessionId), payload);
  return { ok: true, claim: payload, renewed: true };
}

/**
 * Atomically claim a pending session for this worker.
 * Returns { ok: true, claim } or { ok: false, reason, claim? }.
 */
export function claimSession(root, sessionId, workerId = resolveDeriveWorkerId(), { leaseSec = deriveClaimLeaseSec() } = {}) {
  if (!sessionId) {
    return { ok: false, reason: "missing_session_id" };
  }
  const claimPath = sessionClaimPath(root, sessionId);
  const ownerId = String(workerId || resolveDeriveWorkerId());

  for (let attempt = 0; attempt < CLAIM_ATTEMPTS; attempt += 1) {
    const payload = buildClaimPayload(sessionId, ownerId, leaseSec);
    try {
      writeExclusiveClaim(claimPath, payload);
      return { ok: true, claim: payload, reclaimed: attempt > 0 };
    } catch (err) {
      if (err?.code !== "EEXIST") throw err;
    }

    const existing = readSessionClaim(root, sessionId);
    if (!existing) continue;

    if (isSessionClaimActive(existing)) {
      if (String(existing.workerId || "") === ownerId) {
        return renewSessionClaim(root, sessionId, ownerId, { leaseSec });
      }
      return { ok: false, reason: "claimed_by_other", claim: existing };
    }

    try {
      fs.rmSync(claimPath, { force: true });
    } catch {
      /* race — retry */
    }
  }

  return { ok: false, reason: "claim_contention" };
}

/** Release claim when owned by workerId (no-op if missing or expired). */
export function releaseSessionClaim(root, sessionId, workerId = resolveDeriveWorkerId()) {
  const claimPath = sessionClaimPath(root, sessionId);
  if (!fs.existsSync(claimPath)) return { ok: true, released: false };
  const existing = readSessionClaim(root, sessionId);
  if (existing && String(existing.workerId || "") !== String(workerId || "")) {
    return { ok: false, reason: "not_owner", claim: existing };
  }
  try {
    fs.rmSync(claimPath, { force: true });
  } catch {
    /* ignore */
  }
  return { ok: true, released: true };
}

/** Force-clear claim (READY / FAILED cleanup). */
export function clearSessionClaim(root, sessionId) {
  const claimPath = sessionClaimPath(root, sessionId);
  if (!fs.existsSync(claimPath)) return false;
  try {
    fs.rmSync(claimPath, { force: true });
    return true;
  } catch {
    return false;
  }
}
