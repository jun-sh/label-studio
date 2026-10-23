#!/usr/bin/env node
/**
 * One-time: copy existing hot-tier raw MCAP to cold archive, one file at a time (idempotent).
 * Usage: STREAM_DATA_ROOT=... STREAM_COLD_ARCHIVE_ROOT=... node scripts/backfill-raw-cold-backup.mjs ego-002
 */
import fs from "node:fs";
import path from "node:path";

import { backupRawMcapToCold } from "../ingest/raw-cold-backup.mjs";
import { stationRoot } from "../ingest/station-context.mjs";

const stationId = (process.argv[2] || "ego-002").trim();
const root = stationRoot(stationId);
const rawRoot = path.join(root, "raw", "segments");

let copied = 0;
let skipped = 0;
let failed = 0;
for (const sessionId of fs.readdirSync(rawRoot).sort()) {
  const sessDir = path.join(rawRoot, sessionId);
  if (!fs.statSync(sessDir).isDirectory()) continue;
  for (const file of fs.readdirSync(sessDir).sort()) {
    if (!file.endsWith(".mcap") && !file.endsWith(".mcap.zst")) continue;
    const segmentId = file.replace(/\.mcap(\.zst)?$/, "");
    try {
      const res = await backupRawMcapToCold(stationId, root, sessionId, segmentId, path.join(sessDir, file));
      if (res.skipped) skipped += 1;
      else copied += 1;
    } catch (err) {
      failed += 1;
      console.error(`fail ${sessionId}/${file}: ${err?.message || err}`);
    }
  }
  console.log(`${sessionId} done (copied=${copied} skipped=${skipped} failed=${failed})`);
}
console.log(`station=${stationId} copied=${copied} skipped=${skipped} failed=${failed}`);
process.exit(failed ? 1 : 0);
