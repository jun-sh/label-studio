/**
 * Single-writer publish lock per station (L2 parquet / manifest updates).
 */
import fs from "node:fs";
import path from "node:path";

// A healthy publish takes seconds; a lock older than this was left by a killed process.
function publishLockStaleMs() {
  return Math.max(60, Number(process.env.DERIVE_PUBLISH_LOCK_STALE_S || 600)) * 1000;
}

function lockPath(root) {
  return path.join(root, ".locks", "publish.lock");
}

function tryAcquire(lp, workerId) {
  try {
    const fd = fs.openSync(lp, "wx");
    fs.writeFileSync(fd, `${process.pid}\n${workerId}\n${Date.now()}\n`);
    fs.closeSync(fd);
    return true;
  } catch (e) {
    if (e?.code !== "EEXIST") throw e;
    return false;
  }
}

function removeIfStale(lp) {
  try {
    if (Date.now() - fs.statSync(lp).mtimeMs > publishLockStaleMs()) {
      fs.rmSync(lp, { force: true });
      return true;
    }
  } catch {
    /* lock vanished between checks */
  }
  return false;
}

export function withPublishLock(root, workerId, fn) {
  const lp = lockPath(root);
  fs.mkdirSync(path.dirname(lp), { recursive: true });
  const owner = String(workerId || `publish-${process.pid}`);
  if (!tryAcquire(lp, owner) && !(removeIfStale(lp) && tryAcquire(lp, owner))) {
    const err = new Error(`publish lock busy: ${lp}`);
    err.code = "PUBLISH_LOCK_BUSY";
    throw err;
  }
  try {
    return fn();
  } finally {
    try {
      fs.rmSync(lp, { force: true });
    } catch {
      /* ignore */
    }
  }
}
