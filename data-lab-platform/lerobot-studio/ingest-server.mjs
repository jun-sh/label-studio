/**
 * Stream ingest only — POST /lerobot/api/collection/stations/:id/upload
 * Run separately from server.mjs (collection gateway) to keep HTTP APIs responsive.
 */
import http from "node:http";
import { fileURLToPath } from "node:url";
import { handleStreamUploadRequest } from "./stream-ingest.mjs";

const PORT = Number(process.env.INGEST_PORT || process.env.PORT || 7862);
const BASE = "/lerobot";

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-Station-Token",
  };
}

function send(res, status, body, headers = {}) {
  res.writeHead(status, { ...headers, ...corsHeaders() });
  res.end(body);
}

function sendJson(res, status, obj) {
  send(res, status, JSON.stringify(obj), { "Content-Type": "application/json; charset=utf-8" });
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
        .catch((err) => {
          const status = err.statusCode === 401 ? 401 : 400;
          sendJson(res, status, {
            error: status === 401 ? "unauthorized" : "bad_request",
            reason: err.reason || null,
            message: String(err.message || err),
          });
        });
    }
    return send(res, 405, "Method Not Allowed\n", { "Content-Type": "text/plain" });
  }

  return send(res, 404, "Not Found\n", { "Content-Type": "text/plain" });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`Stream ingest on :${PORT} (upload ${BASE}/api/collection/stations/*/upload)`);
});
