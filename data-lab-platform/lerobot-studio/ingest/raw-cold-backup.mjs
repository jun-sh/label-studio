/**
 * Async cold-tier copy of ingested raw MCAP (second copy before edge purge).
 */
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";

import { ingestLog, stationRoot } from "./station-context.mjs";
import { sha256File } from "./io.mjs";
import { recordSessionTimelineEvent } from "./session-timeline.mjs";

function coldArchiveRoot() {
  const raw = String(
    process.env.STREAM_COLD_ARCHIVE_ROOT ||
      process.env.COLD_ARCHIVE_ROOT ||
      "/cold-archive",
  ).trim();
  return raw;
}

function coldBackupEnabled() {
  const v = String(process.env.STREAM_RAW_COLD_BACKUP ?? "1").trim().toLowerCase();
  return v !== "0" && v !== "false" && v !== "no";
}

function markerPath(coldDest) {
  return `${coldDest}.sha256.ok`;
}

/**
 * Queue background copy; idempotent when marker exists with matching sha256.
 */
export function queueRawMcapColdBackup(stationId, root, sessionId, segmentId, rawArchivePath) {
  if (!coldBackupEnabled()) return { queued: false, reason: "disabled" };
  const base = coldArchiveRoot();
  if (!base) return { queued: false, reason: "no_cold_root" };

  const rel = path.relative(root, rawArchivePath);
  const coldDest = path.join(base, stationId, rel);
  const marker = markerPath(coldDest);

  void (async () => {
    try {
      if (fs.existsSync(marker)) {
        const expected = fs.readFileSync(marker, "utf8").trim().split("\n")[0];
        const actual = await sha256File(rawArchivePath);
        if (expected === actual) return;
      }
      fs.mkdirSync(path.dirname(coldDest), { recursive: true });
      if (!fs.existsSync(coldDest)) {
        await new Promise((resolve, reject) => {
          const proc = spawn("cp", ["-a", rawArchivePath, coldDest], { stdio: "ignore" });
          proc.on("error", reject);
          proc.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`cp exit ${code}`))));
        });
      }
      const hash = await sha256File(coldDest);
      const srcHash = await sha256File(rawArchivePath);
      if (hash !== srcHash) {
        throw new Error(`cold backup sha mismatch ${segmentId}`);
      }
      fs.writeFileSync(marker, `${hash}\n${new Date().toISOString()}\n`);
      recordSessionTimelineEvent(stationRoot(stationId), sessionId, "cold_backup_ok", {
        segmentId,
        bytes: fs.statSync(coldDest).size,
      });
      ingestLog(stationId, "raw_cold_backup_ok", { sessionId, segmentId, coldDest });
    } catch (err) {
      ingestLog(stationId, "raw_cold_backup_fail", {
        sessionId,
        segmentId,
        message: String(err?.message || err).slice(0, 200),
      });
    }
  })();

  return { queued: true, coldDest };
}
