import { parentPort, workerData } from "node:worker_threads";
import { kickDeriveSession } from "./watch-kick.mjs";
import { markSessionReady, SESSION_MARKERS, clearSessionMarker } from "../session-markers.mjs";

const { root, stationId, workerId, sessionId } = workerData;

const result = await kickDeriveSession(stationId, root, {
  workerId,
  sessionId,
  runPipeline: async (_stationId, { sessionId: sid }) => {
    markSessionReady(root, sid, { total: 1, mp4Ok: true, source: "watch_kick_test" });
    return { ok: true, phase: "READY", sessionId: sid };
  },
});

parentPort.postMessage(result);
