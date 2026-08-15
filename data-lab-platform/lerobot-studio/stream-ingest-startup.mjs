/**
 * Non-blocking stream-ingest startup: schedule heavy work via timers only.
 * The HTTP server must never wait on disk walks, ffprobe, or spawnSync during boot.
 */
import {
  cleanupOrphanIncomingArchives,
  ensurePeriodicDiskCleanupForAllStations,
  ensureStreamViewerScaffoldAsync,
  listStreamStationIds,
  repairStreamViewerScaffoldAsync,
  resumePendingStreamMuxForAllStations,
  scheduleDiskCleanupForAllStationsStaggered,
  streamLog,
} from "./stream-ingest.mjs";
import { ensureIdleDeriveWatcher, resumeDeriveQueuesForAllStationsAsync } from "./derive-async.mjs";

let startupScheduled = false;
let startupDone = false;

function yieldEventLoop() {
  return new Promise((resolve) => setImmediate(resolve));
}

export function isStreamIngestStartupDone() {
  return startupDone;
}

export function isStreamIngestStartupRunning() {
  return startupScheduled && !startupDone;
}

async function runViewerScaffoldPhase() {
  const stationIds = listStreamStationIds();
  for (const stationId of stationIds) {
    try {
      await ensureStreamViewerScaffoldAsync(stationId);
      await repairStreamViewerScaffoldAsync(stationId);
    } catch (err) {
      console.warn(
        `[stream-ingest] viewer scaffold station=${stationId}:`,
        err?.message || err,
      );
    }
    await yieldEventLoop();
  }
  startupDone = true;
  streamLog("system", "background_startup_complete");
  console.log("[stream-ingest] background startup complete");
}

/** Fire-and-forget: returns immediately; work is chained through timers. */
export function scheduleStreamIngestBackgroundStartup() {
  if (startupScheduled) return;
  startupScheduled = true;
  console.log("[stream-ingest] background startup scheduled");

  setImmediate(() => {
    ensurePeriodicDiskCleanupForAllStations();
    ensureIdleDeriveWatcher();
    streamLog("system", "background_startup_light");
  });

  setTimeout(() => {
    try {
      cleanupOrphanIncomingArchives();
    } catch (err) {
      console.warn("[stream-ingest] orphan cleanup:", err?.message || err);
    }
  }, 2_000);

  setTimeout(() => {
    try {
      resumePendingStreamMuxForAllStations();
    } catch (err) {
      console.warn("[stream-ingest] mux resume:", err?.message || err);
    }
  }, 5_000);

  setTimeout(() => {
    void resumeDeriveQueuesForAllStationsAsync().catch((err) => {
      console.warn("[stream-ingest] derive resume:", err?.message || err);
    });
  }, 8_000);

  setTimeout(() => {
    scheduleDiskCleanupForAllStationsStaggered();
  }, 10_000);

  setTimeout(() => {
    void runViewerScaffoldPhase().catch((err) => {
      console.error("[stream-ingest] scaffold phase failed:", err?.message || err);
      streamLog("system", "background_startup_fail", {
        message: String(err?.message || err).slice(0, 300),
      });
    });
  }, 15_000);
}
