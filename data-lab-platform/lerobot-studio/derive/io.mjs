/** Shared filesystem helpers for derive module (Phase 4). */

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

export function readJsonl(filePath) {
  if (!fs.existsSync(filePath)) return [];
  return fs
    .readFileSync(filePath, "utf8")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

export function writeJsonlAtomic(filePath, rows) {
  ensureDir(path.dirname(filePath));
  const tmp = `${filePath}.tmp.${process.pid}.${Date.now()}`;
  const body = rows.map((row) => `${JSON.stringify(row)}\n`).join("");
  fs.writeFileSync(tmp, body, "utf8");
  fs.renameSync(tmp, filePath);
}

export function appendJsonlAtomic(filePath, rows) {
  if (!rows.length) return;
  ensureDir(path.dirname(filePath));
  const body = rows.map((row) => `${JSON.stringify(row)}\n`).join("");
  fs.appendFileSync(filePath, body, "utf8");
}

export function rawSegmentArchivePath(root, sessionId, segmentId) {
  return path.join(root, "raw", "segments", sessionId, `${segmentId}.tar.zst`);
}

export function rawMcapArchivePath(root, sessionId, segmentId) {
  return path.join(root, "raw", "segments", sessionId, `${segmentId}.mcap.zst`);
}
