#!/usr/bin/env node
/**
 * Watch for session.DONE_UPLOAD markers and run standalone derive (one station at a time).
 */
import fs from "node:fs";
import process from "node:process";
import { runDerivePipelineBlocking } from "./derive-pipeline.mjs";
import { listSessionsPendingDerive } from "./session-markers.mjs";
import { readDeriverLock } from "./derive-lock.mjs";
import { stationRoot, streamLog, STREAM_ROOT } from "./stream-ingest.mjs";

const stationId = (process.env.STATION_ID || process.env.EGO_STATION_ID || "ego-001").trim();
const pollMs = Math.max(3000, Number(process.env.DERIVE_WATCH_POLL_MS || 10_000));

async function tick() {
  if (!fs.existsSync(STREAM_ROOT)) return;
  const root = stationRoot(stationId);
  const lock = readDeriverLock(root);
  if (lock?.pid) {
    try {
      process.kill(lock.pid, 0);
      return;
    } catch {
      /* stale lock — derive run will clear */
    }
  }
  const pending = listSessionsPendingDerive(root);
  if (!pending.length) return;
  const sessionId = pending[0];
  streamLog(stationId, "derive_watch_kick", { sessionId, pending: pending.length });
  try {
    const result = await runDerivePipelineBlocking(stationId, { sessionId });
    streamLog(stationId, "derive_watch_done", { sessionId, ok: result.ok, phase: result.phase });
  } catch (err) {
    streamLog(stationId, "derive_watch_fail", {
      sessionId,
      message: String(err?.message || err).slice(0, 200),
    });
  }
}

console.log(`[ego-derive-watch] station=${stationId} poll=${pollMs}ms STREAM_DATA_ROOT=${STREAM_ROOT}`);
streamLog(stationId, "derive_watch_started", { pollMs });
await tick();
setInterval(() => {
  tick().catch(() => {});
}, pollMs);
