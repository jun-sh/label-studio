#!/usr/bin/env node
/**
 * One-time: copy existing hot-tier raw MCAP to cold archive (idempotent).
 * Usage: STREAM_DATA_ROOT=... node scripts/backfill-raw-cold-backup.mjs ego-002
 */
import fs from "node:fs";
import path from "node:path";

import { queueRawMcapColdBackup } from "../ingest/raw-cold-backup.mjs";
import { stationRoot } from "../ingest/station-context.mjs";

const stationId = (process.argv[2] || "ego-002").trim();
const root = stationRoot(stationId);
const rawRoot = path.join(root, "raw", "segments");
let n = 0;
for (const sessionId of fs.readdirSync(rawRoot)) {
  const sessDir = path.join(rawRoot, sessionId);
  if (!fs.statSync(sessDir).isDirectory()) continue;
  for (const file of fs.readdirSync(sessDir)) {
    if (!file.endsWith(".mcap") && !file.endsWith(".mcap.zst")) continue;
    const seg = file.replace(/\.mcap(\.zst)?$/, "");
    const rawPath = path.join(sessDir, file);
    queueRawMcapColdBackup(stationId, root, sessionId, seg, rawPath);
    n += 1;
  }
}
console.log(`queued cold backup for ${n} raw archives station=${stationId}`);
