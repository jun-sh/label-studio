/**
 * Global derive worker lock — one derive process per station at a time.
 */
import fs from "node:fs";
import path from "node:path";

function lockPath(root) {
  return path.join(root, "state", "deriver.lock");
}

function readLock(root) {
  const p = lockPath(root);
  if (!fs.existsSync(p)) return null;
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return { pid: -1, stale: true };
  }
}

function isProcessAlive(pid) {
  if (!pid || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

export function acquireDeriverLock(root, meta = {}) {
  const p = lockPath(root);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const existing = readLock(root);
  if (existing?.pid && isProcessAlive(existing.pid)) {
    const err = new Error(`deriver.lock held by pid ${existing.pid}`);
    err.code = "DERIVER_LOCKED";
    err.lock = existing;
    throw err;
  }
  if (existing) {
    try {
      fs.rmSync(p, { force: true });
    } catch {
      /* ignore */
    }
  }
  const payload = {
    pid: process.pid,
    startedAt: new Date().toISOString(),
    ...meta,
  };
  const tmp = `${p}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify(payload, null, 2));
  fs.renameSync(tmp, p);
  return payload;
}

export function releaseDeriverLock(root) {
  const p = lockPath(root);
  if (!fs.existsSync(p)) return;
  const existing = readLock(root);
  if (existing?.pid && existing.pid !== process.pid) return;
  try {
    fs.rmSync(p, { force: true });
  } catch {
    /* ignore */
  }
}

export function readDeriverLock(root) {
  return readLock(root);
}
