/**
 * Per-session derive worker locks (v2) with lease heartbeats.
 * Legacy global state/deriver.lock (pid-only, version<2) is never active.
 */
import fs from "node:fs";
import path from "node:path";

const LOCK_VERSION = 2;

export function deriveLockLeaseSec() {
  return Math.max(60, Number(process.env.DERIVE_LOCK_LEASE_S || process.env.DERIVE_CLAIM_LEASE_S || 600));
}

function resolveWorkerId(meta = {}) {
  return String(
    meta.workerId || process.env.DERIVE_WORKER_ID || process.env.HOSTNAME || `derive-worker-${process.pid}`,
  ).trim();
}

function sessionLockPath(root, sessionId) {
  if (sessionId) {
    return path.join(root, "state", "sessions", String(sessionId), "deriver.lock");
  }
  return path.join(root, "state", "deriver.lock");
}

function readLockFile(lockPath) {
  if (!fs.existsSync(lockPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(lockPath, "utf8"));
  } catch {
    return { stale: true };
  }
}

function writeLockAtomic(lockPath, payload) {
  fs.mkdirSync(path.dirname(lockPath), { recursive: true });
  const tmp = `${lockPath}.tmp.${process.pid}.${Date.now()}`;
  fs.writeFileSync(tmp, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  fs.renameSync(tmp, lockPath);
}

export function isDeriverLockActive(lock, { workerId = null, nowMs = Date.now() } = {}) {
  if (!lock || lock.stale) return false;
  if (lock.version !== LOCK_VERSION) return false;
  if (!lock.workerId || !lock.leaseExpiresAt) return false;
  const expiresMs = Date.parse(lock.leaseExpiresAt);
  if (!Number.isFinite(expiresMs) || expiresMs <= nowMs) return false;
  if (workerId != null && String(lock.workerId) !== String(workerId)) return false;
  return true;
}

function buildLockPayload(meta = {}) {
  const nowMs = Date.now();
  const sessionId = meta.sessionId ? String(meta.sessionId) : null;
  const workerId = resolveWorkerId(meta);
  return {
    version: LOCK_VERSION,
    pid: process.pid,
    startedAt: new Date(nowMs).toISOString(),
    leaseExpiresAt: new Date(nowMs + deriveLockLeaseSec() * 1000).toISOString(),
    workerId,
    ...meta,
    sessionId,
  };
}

export function readDeriverLock(root, sessionId = null) {
  return readLockFile(sessionLockPath(root, sessionId));
}

export function listActiveDeriverLocks(root) {
  const locks = [];
  const sessionsDir = path.join(root, "state", "sessions");
  if (fs.existsSync(sessionsDir)) {
    for (const ent of fs.readdirSync(sessionsDir, { withFileTypes: true })) {
      if (!ent.isDirectory()) continue;
      const lock = readLockFile(path.join(sessionsDir, ent.name, "deriver.lock"));
      if (isDeriverLockActive(lock)) locks.push(lock);
    }
  }
  const legacy = readLockFile(sessionLockPath(root, null));
  if (isDeriverLockActive(legacy)) locks.push(legacy);
  return locks;
}

export function isDeriverLockHeldByOther(root, workerId, sessionId = null) {
  if (sessionId) {
    const lock = readDeriverLock(root, sessionId);
    return isDeriverLockActive(lock) && String(lock.workerId) !== String(workerId);
  }
  return listActiveDeriverLocks(root).some((lock) => String(lock.workerId) !== String(workerId));
}

export function acquireDeriverLock(root, meta = {}) {
  const sessionId = meta.sessionId ? String(meta.sessionId) : null;
  const workerId = resolveWorkerId(meta);
  const lockPath = sessionLockPath(root, sessionId);
  const existing = readLockFile(lockPath);

  if (existing && isDeriverLockActive(existing)) {
    if (String(existing.workerId) !== workerId) {
      const err = new Error(`deriver.lock held by worker ${existing.workerId}`);
      err.code = "DERIVER_LOCKED";
      err.lock = existing;
      throw err;
    }
  } else if (existing) {
    try {
      fs.rmSync(lockPath, { force: true });
    } catch {
      /* ignore */
    }
  }

  const payload = buildLockPayload({ ...meta, sessionId, workerId });
  writeLockAtomic(lockPath, payload);
  return payload;
}

export function renewDeriverLock(root, meta = {}) {
  const sessionId = meta.sessionId ? String(meta.sessionId) : null;
  const workerId = meta.workerId ? String(meta.workerId) : null;
  const lockPath = sessionLockPath(root, sessionId);
  const existing = readLockFile(lockPath);
  if (!existing || !isDeriverLockActive(existing)) {
    return { ok: false, lock: existing };
  }
  if (workerId && String(existing.workerId) !== workerId) {
    return { ok: false, lock: existing };
  }
  const payload = {
    ...existing,
    ...meta,
    renewedAt: new Date().toISOString(),
    leaseExpiresAt: new Date(Date.now() + deriveLockLeaseSec() * 1000).toISOString(),
  };
  writeLockAtomic(lockPath, payload);
  return { ok: true, lock: payload };
}

function removeLockFile(lockPath, { force = false, workerId = null } = {}) {
  if (!fs.existsSync(lockPath)) return false;
  const existing = readLockFile(lockPath);
  if (!force && workerId && existing?.workerId && String(existing.workerId) !== String(workerId)) {
    return false;
  }
  if (!force && existing?.pid && existing.pid !== process.pid && existing.version === LOCK_VERSION) {
    return false;
  }
  try {
    fs.rmSync(lockPath, { force: true });
    return true;
  } catch {
    return false;
  }
}

export function releaseDeriverLock(root, options = undefined) {
  if (options === undefined) {
    let released = false;
    if (removeLockFile(sessionLockPath(root, null), { force: true })) released = true;
    const workerId = resolveWorkerId();
    const sessionsDir = path.join(root, "state", "sessions");
    if (fs.existsSync(sessionsDir)) {
      for (const ent of fs.readdirSync(sessionsDir, { withFileTypes: true })) {
        if (!ent.isDirectory()) continue;
        const lockPath = path.join(sessionsDir, ent.name, "deriver.lock");
        const lock = readLockFile(lockPath);
        if (lock?.pid === process.pid || String(lock?.workerId || "") === workerId) {
          if (removeLockFile(lockPath, { force: true })) released = true;
        }
      }
    }
    return { released };
  }

  const { sessionId = null, workerId = null, force = false } = options;
  const released = removeLockFile(sessionLockPath(root, sessionId), { force, workerId });
  return { released };
}
