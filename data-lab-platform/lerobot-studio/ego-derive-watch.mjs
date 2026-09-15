#!/usr/bin/env node
/**
 * Watch for session.DONE_UPLOAD markers and run standalone derive (kickDeriveSession).
 */
import fs from "node:fs";
import process from "node:process";
import { kickDeriveSession } from "./derive/watch-kick.mjs";
import { listActiveDeriverLocks } from "./derive-lock.mjs";
import { stationRoot, streamLog, STREAM_ROOT } from "./stream-ingest.mjs";
import { resolveDeriveWorkerId } from "./derive/session-claim.mjs";

const stationId = (process.env.STATION_ID || process.env.EGO_STATION_ID || "ego-001").trim();
const pollMs = Math.max(3000, Number(process.env.DERIVE_WATCH_POLL_MS || 10_000));
const workerId = resolveDeriveWorkerId();

async function tick() {
  if (!fs.existsSync(STREAM_ROOT)) return;
  const root = stationRoot(stationId);
  if (listActiveDeriverLocks(root).length > 0) return;

  const result = await kickDeriveSession(stationId, root, { workerId });
  if (result.ok) {
    streamLog(stationId, "derive_watch_done", {
      sessionId: result.sessionId,
      ok: true,
      phase: result.phase,
      workerId,
    });
    return;
  }
  if (result.reason !== "no_pending" && result.reason !== "claim_failed" && result.reason !== "lock_failed") {
    streamLog(stationId, "derive_watch_skip", { workerId, ...result });
  }
}

console.log(`[ego-derive-watch] station=${stationId} workerId=${workerId} poll=${pollMs}ms STREAM_DATA_ROOT=${STREAM_ROOT}`);
streamLog(stationId, "derive_watch_started", { pollMs, workerId });
await tick();
setInterval(() => {
  tick().catch((err) => {
    streamLog(stationId, "derive_watch_fail", {
      workerId,
      message: String(err?.message || err).slice(0, 200),
    });
  });
}, pollMs);
