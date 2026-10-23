/**
 * Async cold-tier copy of ingested raw MCAP (second copy before edge purge).
 */
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";

import { ingestLog } from "./station-context.mjs";
import { sha256File } from "./io.mjs";
import { recordSessionTimelineEvent } from "./session-timeline.mjs";

function coldArchiveRoot() {
  return String(
    process.env.STREAM_COLD_ARCHIVE_ROOT || process.env.COLD_ARCHIVE_ROOT || "/cold-archive",
  ).trim();
}

function coldBackupEnabled() {
  const v = String(process.env.STREAM_RAW_COLD_BACKUP ?? "1").trim().toLowerCase();
  return v !== "0" && v !== "false" && v !== "no";
}

export function coldBackupMarkerPath(coldDest) {
  return `${coldDest}.sha256.ok`;
}

export function coldBackupDestination(stationId, root, rawArchivePath) {
  return path.join(coldArchiveRoot(), stationId, path.relative(root, rawArchivePath));
}

function copyFile(src, dest) {
  return new Promise((resolve, reject) => {
    const proc = spawn("cp", ["-a", src, dest], { stdio: "ignore" });
    proc.on("error", reject);
    proc.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`cp exit ${code}`))));
  });
}

/** Copy + verify one raw archive. Idempotent: a matching marker short-circuits. */
export async function backupRawMcapToCold(stationId, root, sessionId, segmentId, rawArchivePath) {
  const coldDest = coldBackupDestination(stationId, root, rawArchivePath);
  const marker = coldBackupMarkerPath(coldDest);
  const srcHash = await sha256File(rawArchivePath);
  if (fs.existsSync(marker) && fs.readFileSync(marker, "utf8").split("\n")[0] === srcHash) {
    return { ok: true, skipped: true, coldDest };
  }
  fs.mkdirSync(path.dirname(coldDest), { recursive: true });
  const partial = `${coldDest}.partial`;
  fs.rmSync(partial, { force: true });
  await copyFile(rawArchivePath, partial);
  const destHash = await sha256File(partial);
  if (destHash !== srcHash) {
    fs.rmSync(partial, { force: true });
    throw new Error(`cold backup sha mismatch ${segmentId}`);
  }
  fs.renameSync(partial, coldDest);
  fs.writeFileSync(marker, `${srcHash}\n${new Date().toISOString()}\n`);
  recordSessionTimelineEvent(root, sessionId, "cold_backup_ok", {
    segmentId,
    bytes: fs.statSync(coldDest).size,
  });
  return { ok: true, skipped: false, coldDest };
}

// Serial queue: one copy at a time so backup IO never competes with upload bursts.
let backupChain = Promise.resolve();

export function queueRawMcapColdBackup(stationId, root, sessionId, segmentId, rawArchivePath) {
  if (!coldBackupEnabled()) return { queued: false, reason: "disabled" };
  if (!coldArchiveRoot()) return { queued: false, reason: "no_cold_root" };
  backupChain = backupChain
    .then(() => backupRawMcapToCold(stationId, root, sessionId, segmentId, rawArchivePath))
    .then((res) => {
      if (!res.skipped) {
        ingestLog(stationId, "raw_cold_backup_ok", { sessionId, segmentId, coldDest: res.coldDest });
      }
    })
    .catch((err) => {
      ingestLog(stationId, "raw_cold_backup_fail", {
        sessionId,
        segmentId,
        message: String(err?.message || err).slice(0, 200),
      });
    });
  return { queued: true };
}
