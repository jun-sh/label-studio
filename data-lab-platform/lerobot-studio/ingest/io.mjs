/** Shared filesystem helpers for ingest module (Phase 3). */

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

export function readJson(filePath, fallback = null) {
  if (!fs.existsSync(filePath)) return fallback;
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

export function writeJsonAtomic(filePath, obj) {
  ensureDir(path.dirname(filePath));
  const tmp = `${filePath}.tmp.${process.pid}.${Date.now()}`;
  fs.writeFileSync(tmp, `${JSON.stringify(obj, null, 2)}\n`, "utf8");
  fs.renameSync(tmp, filePath);
}

export function sha256File(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash("sha256");
    const rs = fs.createReadStream(filePath);
    rs.on("data", (chunk) => hash.update(chunk));
    rs.on("end", () => resolve(hash.digest("hex")));
    rs.on("error", reject);
  });
}

/** Atomic move into place (same filesystem). */
export function atomicMoveFile(srcPath, destPath) {
  ensureDir(path.dirname(destPath));
  try {
    fs.renameSync(srcPath, destPath);
    return;
  } catch (err) {
    if (err.code !== "EXDEV") throw err;
  }
  const tmp = `${destPath}.tmp.${process.pid}.${Date.now()}`;
  fs.copyFileSync(srcPath, tmp);
  fs.renameSync(tmp, destPath);
  fs.rmSync(srcPath, { force: true });
}

export function rawSegmentArchivePath(root, sessionId, segmentId) {
  return path.join(root, "raw", "segments", sessionId, `${segmentId}.tar.zst`);
}

export function rawMcapArchivePath(root, sessionId, segmentId) {
  return path.join(root, "raw", "segments", sessionId, `${segmentId}.mcap.zst`);
}

/** Remove stale tar.zst for the same segment when MCAP ingest wins (POC hardening). */
export function purgeConflictingTarArchive(root, sessionId, segmentId) {
  const tarPath = rawSegmentArchivePath(root, sessionId, segmentId);
  if (!fs.existsSync(tarPath)) return false;
  try {
    fs.rmSync(tarPath, { force: true });
    return true;
  } catch {
    return false;
  }
}

/** Remove stale mcap archives for the same segment when tar.zst ingest wins. */
export function purgeConflictingMcapArchive(root, sessionId, segmentId) {
  const rawDir = path.join(root, "raw", "segments", sessionId);
  if (!fs.existsSync(rawDir)) return false;
  let removed = false;
  for (const name of [`${segmentId}.mcap`, `${segmentId}.mcap.zst`]) {
    const p = path.join(rawDir, name);
    if (!fs.existsSync(p)) continue;
    try {
      fs.rmSync(p, { force: true });
      removed = true;
    } catch {
      /* ignore */
    }
  }
  return removed;
}
