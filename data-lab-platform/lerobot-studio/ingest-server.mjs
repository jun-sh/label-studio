/**
 * Stream ingest only — POST /lerobot/api/collection/stations/:id/upload
 * Run separately from server.mjs (collection gateway) to keep HTTP APIs responsive.
 */
import http from "node:http";
import {
  cleanupOrphanIncomingArchives,
  ensurePeriodicDiskCleanupForAllStations,
  ensureStreamViewerScaffoldForAllStations,
  handleStreamScaffoldRequest,
  handleStreamUploadRequest,
  resumePendingStreamMuxForAllStations,
  runDiskCleanupForAllStations,
  stationRoot,
  verifyStationUploadToken,
} from "./stream-ingest.mjs";
import { handleImportTaskGet, handleImportUpload } from "./import-handlers.mjs";
import {
  listSegmentStates,
  segmentStateForApi,
} from "./ingest/index.mjs";
import {
  getDeriveQueueStats,
  getDeriveStatusSummary,
  handleDeriveRetry,
  handleDeriveStart,
  resumeDeriveQueuesForAllStations,
  ensureIdleDeriveWatcher,
} from "./derive-async.mjs";

const PORT = Number(process.env.INGEST_PORT || process.env.PORT || 7862);
const BASE = "/lerobot";

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers":
      "Content-Type, X-Station-Token, X-Session-Id, X-Segment-Id, X-Segment-Seq, X-Content-Sha256, X-Upload-Protocol, X-File-Name",
  };
}

function send(res, status, body, headers = {}) {
  res.writeHead(status, { ...headers, ...corsHeaders() });
  res.end(body);
}

function sendJson(res, status, obj) {
  send(res, status, JSON.stringify(obj), { "Content-Type": "application/json; charset=utf-8" });
}

function handleIngestError(res, err) {
  const status = err.statusCode === 401 ? 401 : err.statusCode === 404 ? 404 : 400;
  sendJson(res, status, {
    error: status === 401 ? "unauthorized" : status === 404 ? "not_found" : "bad_request",
    reason: err.reason || null,
    message: String(err.message || err),
  });
}

const server = http.createServer((req, res) => {
  if (req.method === "OPTIONS") {
    return send(res, 204, "");
  }

  const url = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);
  const p = url.pathname;

  if (p === "/healthz" || p === `${BASE}/healthz`) {
    return send(res, 200, "ok\n", { "Content-Type": "text/plain" });
  }

  const importTaskMatch = p.match(
    new RegExp(`^${BASE}/api/collection/stations/([^/]+)/import/tasks/([^/]+)$`),
  );
  if (importTaskMatch && req.method === "GET") {
    const stationId = decodeURIComponent(importTaskMatch[1]);
    const taskId = decodeURIComponent(importTaskMatch[2]);
    try {
      return sendJson(res, 200, handleImportTaskGet(stationId, taskId));
    } catch (err) {
      return handleIngestError(res, err);
    }
  }

  const importUploadMatch = p.match(
    new RegExp(`^${BASE}/api/collection/stations/([^/]+)/import/upload$`),
  );
  if (importUploadMatch && req.method === "POST") {
    const stationId = decodeURIComponent(importUploadMatch[1]);
    return handleImportUpload(stationId, req)
      .then((out) => sendJson(res, 202, out))
      .catch((err) => handleIngestError(res, err));
  }

  const deriveStatusMatch = p.match(
    new RegExp(`^${BASE}/api/collection/stations/([^/]+)/derive-status$`),
  );
  if (deriveStatusMatch && req.method === "GET") {
    const stationId = decodeURIComponent(deriveStatusMatch[1]);
    const sessionFilter = url.searchParams.get("session") || undefined;
    return sendJson(res, 200, getDeriveStatusSummary(stationId, { sessionId: sessionFilter }));
  }

  const segmentsListMatch = p.match(
    new RegExp(`^${BASE}/api/collection/stations/([^/]+)/segments$`),
  );
  if (segmentsListMatch && req.method === "GET") {
    const stationId = decodeURIComponent(segmentsListMatch[1]);
    const sessionFilter = url.searchParams.get("session") || undefined;
    const statusFilter = url.searchParams.get("status") || undefined;
    const items = listSegmentStates(stationRoot(stationId), {
      sessionId: sessionFilter,
      status: statusFilter,
    }).map(segmentStateForApi);
    return sendJson(res, 200, {
      stationId,
      segments: items,
      deriveQueue: getDeriveQueueStats(stationId),
      sla: {
        UPLOADED: "Raw verified on disk (tar.zst)",
        DERIVING: "Background derive in progress",
        READY: "Parquet + MP4 validated on disk",
      },
    });
  }

  const deriveRetryMatch = p.match(
    new RegExp(
      `^${BASE}/api/collection/stations/([^/]+)/segments/([^/]+)/([^/]+)/derive-retry$`,
    ),
  );
  if (deriveRetryMatch && req.method === "POST") {
    const stationId = decodeURIComponent(deriveRetryMatch[1]);
    const sessionId = decodeURIComponent(deriveRetryMatch[2]);
    const segmentId = decodeURIComponent(deriveRetryMatch[3]);
    try {
      const auth = verifyStationUploadToken(stationId, req);
      if (!auth.ok) {
        const err = new Error("unauthorized");
        err.statusCode = 401;
        err.reason = auth.reason;
        throw err;
      }
      return sendJson(res, 202, handleDeriveRetry(stationId, sessionId, segmentId));
    } catch (err) {
      return handleIngestError(res, err);
    }
  }

  const deriveStartMatch = p.match(
    new RegExp(`^${BASE}/api/collection/stations/([^/]+)/derive-start$`),
  );
  if (deriveStartMatch && req.method === "POST") {
    const stationId = decodeURIComponent(deriveStartMatch[1]);
    try {
      const auth = verifyStationUploadToken(stationId, req);
      if (!auth.ok) {
        const err = new Error("unauthorized");
        err.statusCode = 401;
        err.reason = auth.reason;
        throw err;
      }
      return sendJson(res, 202, handleDeriveStart(stationId));
    } catch (err) {
      return handleIngestError(res, err);
    }
  }

  const scaffoldMatch = p.match(new RegExp(`^${BASE}/api/stream/([^/]+)/scaffold$`));
  if (scaffoldMatch && req.method === "GET") {
    const stationId = decodeURIComponent(scaffoldMatch[1]);
    try {
      return sendJson(res, 200, handleStreamScaffoldRequest(stationId));
    } catch (err) {
      return handleIngestError(res, err);
    }
  }

  const uploadMatch = p.match(
    new RegExp(`^${BASE}/api/collection/stations/([^/]+)/upload$`),
  );
  if (uploadMatch) {
    const stationId = decodeURIComponent(uploadMatch[1]);
    if (req.method === "GET") {
      return sendJson(res, 200, {
        status: "ready",
        stationId,
        message: "POST JSON (heartbeat|session_start) or multipart (frame)",
      });
    }
    if (req.method === "POST") {
      return handleStreamUploadRequest(stationId, req)
        .then((out) => {
          sendJson(res, 202, out);
        })
        .catch((err) => handleIngestError(res, err));
    }
    return send(res, 405, "Method Not Allowed\n", { "Content-Type": "text/plain" });
  }

  return send(res, 404, "Not Found\n", { "Content-Type": "text/plain" });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`Stream ingest on :${PORT} (upload ${BASE}/api/collection/stations/*/upload)`);
  setImmediate(() => {
    console.log("[stream-ingest] running startup disk cleanup for all stations…");
    cleanupOrphanIncomingArchives();
    runDiskCleanupForAllStations();
    ensurePeriodicDiskCleanupForAllStations();
    resumePendingStreamMuxForAllStations();
    resumeDeriveQueuesForAllStations();
    ensureIdleDeriveWatcher();
    ensureStreamViewerScaffoldForAllStations();
  });
});
