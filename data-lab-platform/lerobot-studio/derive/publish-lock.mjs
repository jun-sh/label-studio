/**
 * Single-writer publish lock per station (L2 parquet / manifest updates).
 */
import fs from "node:fs";
import path from "node:path";

function lockPath(root) {
  return path.join(root, ".locks", "publish.lock");
}

function tryAcquire(lockPath, workerId) {
  try {
    const fd = fs.openSync(lockPath, "wx");
    fs.writeFileSync(fd, `${process.pid}\n${workerId}\n${Date.now()}\n`);
    fs.closeSync(fd);
    return true;
  } catch (e) {
    if (e?.code !== "EEXIST") throw e;
    return false;
  }
}

export function withPublishLock(root, workerId, fn) {
  const lp = lockPath(root);
  fs.mkdirSync(path.dirname(lp), { recursive: true });
  const owner = String(workerId || `publish-${process.pid}`);
  if (!tryAcquire(lp, owner)) {
    throw new Error(`publish lock busy: ${lp}`);
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
