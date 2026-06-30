/**
 * Stream ingest only — POST /lerobot/api/collection/stations/:id/upload
 * Run separately from server.mjs (collection gateway) to keep HTTP APIs responsive.
 */
import http from "node:http";
import {
  ensurePeriodicDiskCleanupForAllStations,
  handleStreamUploadRequest,
  resumePendingStreamMuxForAllStations,
  runDiskCleanupForAllStations,
} from "./stream-ingest.mjs";
import { handleImportTaskGet, handleImportUpload } from "./import-handlers.mjs";

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
    runDiskCleanupForAllStations();
    ensurePeriodicDiskCleanupForAllStations();
    resumePendingStreamMuxForAllStations();
  });
});
