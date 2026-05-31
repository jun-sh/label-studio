/**
 * Visualizer static host + sample API + bundled dataset files + collection API.
 */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  getStreamStatus,
  handleStreamUploadRequest,
  isStationLiveCached,
  resolveStreamFile,
  streamDatasetUrl,
  streamHttpDatasetUrl,
} from "./stream-ingest.mjs";
import { proxyStationPreview } from "./preview-proxy.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = process.env.LEROBOT_STUDIO_ROOT || "/srv/lerobot";
const BUNDLED = process.env.BUNDLED_DATA_ROOT || "/srv/bundled";
const PORT = Number(process.env.PORT || 7860);
const BASE = "/lerobot";
const BRANDING = path.join(__dirname, "branding");

const datasetsPath =
  process.env.LEROBOT_DATASETS_JSON || path.join(__dirname, "config", "datasets.json");
const datasets = JSON.parse(fs.readFileSync(datasetsPath, "utf8"));

const samplesManifestPath =
  process.env.LEROBOT_SAMPLES_MANIFEST_JSON ||
  path.join(__dirname, "config", "sample-datasets.manifest.json");
const samplesManifest = JSON.parse(fs.readFileSync(samplesManifestPath, "utf8"));

const collectionStationsPath =
  process.env.COLLECTION_STATIONS_JSON || path.join(__dirname, "config", "collection-stations.json");
const collectionStations = JSON.parse(fs.readFileSync(collectionStationsPath, "utf8"));

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".wasm": "application/wasm",
  ".json": "application/json; charset=utf-8",
  ".zip": "application/zip",
  ".tar": "application/x-tar",
  ".webp": "image/webp",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".mp4": "video/mp4",
  ".parquet": "application/octet-stream",
  ".jsonl": "application/jsonl; charset=utf-8",
};

function corsHeaders(extra = {}) {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, HEAD, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Range, Content-Type, Accept, Authorization",
    "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
    ...extra,
  };
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      try {
        const raw = Buffer.concat(chunks).toString("utf8");
        resolve(raw ? JSON.parse(raw) : {});
      } catch (e) {
        reject(e);
      }
    });
    req.on("error", reject);
  });
}

function send(res, status, body, headers = {}) {
  res.writeHead(status, { "Cache-Control": "no-cache", ...headers });
  res.end(body);
}

function sendJson(res, status, payload) {
  send(res, status, JSON.stringify(payload), {
    "Content-Type": "application/json; charset=utf-8",
    ...corsHeaders(),
  });
}

function safeJoin(root, urlPath) {
  const rel = decodeURIComponent(urlPath).replace(/\0/g, "");
  const resolved = path.normalize(path.join(root, rel));
  const rootNorm = path.normalize(root + path.sep);
  if (!resolved.startsWith(rootNorm) && resolved !== path.normalize(root)) {
    return null;
  }
  return resolved;
}

function shouldPatchCollectionGoHome(req) {
  const ref = String(req?.headers?.referer || req?.headers?.referrer || "");
  if (!ref) return false;
  if (ref.includes("datalab_collection=1")) return true;
  return ref.includes("/collection") && ref.includes("station=");
}

function patchLeRobotJsBundle(req, filePath, data) {
  if (!shouldPatchCollectionGoHome(req)) return data;
  if (!/index-[^/]+\.js$/i.test(filePath)) return data;
  let text = data.toString("utf8");
  const next = text
    .replace(/goHome:`返回首页`/g, "goHome:`返回采集站列表`")
    .replace(/goHome:`Go to Home`/g, "goHome:`Back to station list`")
    .replace(/goHome:`ホームへ`/g, "goHome:`Back to station list`");
  return next === text ? data : Buffer.from(next, "utf8");
}

function serveFile(req, res, filePath) {
  const ext = path.extname(filePath);
  const type = MIME[ext] || "application/octet-stream";
  let data = fs.readFileSync(filePath);
  if (ext === ".js") {
    data = patchLeRobotJsBundle(req, filePath, data);
  }
  send(res, 200, data, { "Content-Type": type });
}

/** Supports Range preflight for zip streaming (sample datasets). */
function serveFileWithRange(req, res, filePath) {
  const stat = fs.statSync(filePath);
  const ext = path.extname(filePath);
  const type = MIME[ext] || "application/octet-stream";
  const size = stat.size;
  const base = corsHeaders({
    "Content-Type": type,
    "Accept-Ranges": "bytes",
  });

  if (req.method === "HEAD") {
    res.writeHead(200, { ...base, "Content-Length": size });
    return res.end();
  }

  const range = req.headers.range;
  if (range) {
    const parts = /^bytes=(\d+)-(\d*)$/i.exec(range);
    if (!parts) {
      res.writeHead(416, { ...base, "Content-Range": `bytes */${size}` });
      return res.end();
    }
    const start = Number(parts[1]);
    let end = parts[2] ? Number(parts[2]) : size - 1;
    if (Number.isNaN(start) || start >= size || end >= size || start > end) {
      res.writeHead(416, { ...base, "Content-Range": `bytes */${size}` });
      return res.end();
    }
    const chunkSize = end - start + 1;
    res.writeHead(206, {
      ...base,
      "Content-Range": `bytes ${start}-${end}/${size}`,
      "Content-Length": chunkSize,
    });
    return fs.createReadStream(filePath, { start, end }).pipe(res);
  }

  res.writeHead(200, { ...base, "Content-Length": size });
  fs.createReadStream(filePath).pipe(res);
}

function injectBranding(html) {
  const inject =
    '<link rel="stylesheet" href="/lerobot/branding/overlay.css?v=40"/>' +
    '<script src="/lerobot/branding/stream-embed-gate.js?v=40"></script>' +
    '<script src="/lerobot/branding/stream-http-source.js?v=40"></script>' +
    '<script defer src="/lerobot/branding/overlay.js?v=40"></script>' +
    '<script defer src="/lerobot/branding/stream-live-poll.js?v=40"></script>';
  html = html.replace(/<link[^>]*\/lerobot\/branding\/overlay\.css[^>]*>\s*/gi, "");
  html = html.replace(/<script[^>]*\/lerobot\/branding\/[^"']+[^>]*>\s*<\/script>\s*/gi, "");
  if (!html.includes("/lerobot/branding/overlay.js")) {
    html = html.replace("</head>", `${inject}</head>`);
  }
  return html;
}

function findDataset(id) {
  return datasets.find((d) => d.id === id);
}

const server = http.createServer((req, res) => {
  if (req.method === "OPTIONS") {
    return send(res, 204, "", corsHeaders());
  }

  const url = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);
  let p = url.pathname;

  if (p === "/healthz") {
    return send(res, 200, "ok\n", { "Content-Type": "text/plain" });
  }

  if (p === `${BASE}/datasets` || p === `${BASE}/api/datasets` || p === `${BASE}/samples`) {
    return sendJson(res, 200, datasets);
  }

  if (p === `${BASE}/sample-datasets.manifest.json`) {
    return sendJson(res, 200, samplesManifest);
  }

  const sampleMatch = p.match(new RegExp(`^${BASE}/api/sample/([^/]+)$`));
  if (sampleMatch) {
    const sample = findDataset(decodeURIComponent(sampleMatch[1]));
    if (!sample) {
      return sendJson(res, 404, { error: "sample_not_found" });
    }
    return sendJson(res, 200, sample);
  }

  function enrichStation(station) {
    const heartbeatOnline = station.stream ? isStationLiveCached(station.id) : false;
    const online = heartbeatOnline || Boolean(station.online);
    let datasetUrl = station.datasetUrl;
    if (!datasetUrl && online) {
      datasetUrl = station.stream ? streamDatasetUrl(station.id) : "sample://sensexperience_ego";
    }
    const httpDatasetUrl =
      datasetUrl && String(datasetUrl).startsWith("stream://")
        ? streamHttpDatasetUrl(station.id)
        : null;
    return { ...station, online, datasetUrl, httpDatasetUrl };
  }

  if (p === `${BASE}/api/collection/stations/catalog`) {
    const catalog = collectionStations.map((station) => ({
      ...station,
      online: Boolean(station.online),
      httpDatasetUrl:
        station.datasetUrl && String(station.datasetUrl).startsWith("stream://")
          ? streamHttpDatasetUrl(station.id)
          : null,
    }));
    return sendJson(res, 200, { stations: catalog, updatedAt: new Date().toISOString() });
  }

  if (p === `${BASE}/api/collection/stations`) {
    const enriched = collectionStations.map(enrichStation);
    return sendJson(res, 200, { stations: enriched, updatedAt: new Date().toISOString() });
  }

  const stationMatch = p.match(new RegExp(`^${BASE}/api/collection/stations/([^/]+)$`));
  if (stationMatch) {
    const station = collectionStations.find((s) => s.id === decodeURIComponent(stationMatch[1]));
    if (!station) {
      return sendJson(res, 404, { error: "station_not_found" });
    }
    return sendJson(res, 200, enrichStation(station));
  }

  const previewMatch = p.match(
    new RegExp(`^${BASE}/api/collection/stations/([^/]+)/preview/([^/]+)/mjpeg$`),
  );
  if (previewMatch && req.method === "GET") {
    const stationId = decodeURIComponent(previewMatch[1]);
    const cam = decodeURIComponent(previewMatch[2]);
    const station = collectionStations.find((s) => s.id === stationId);
    return proxyStationPreview(req, res, station, cam, { send, corsHeaders });
  }

  const streamMatch = p.match(new RegExp(`^${BASE}/api/collection/stations/([^/]+)/stream$`));
  if (streamMatch) {
    const station = collectionStations.find((s) => s.id === decodeURIComponent(streamMatch[1]));
    if (!station) {
      return sendJson(res, 404, { error: "station_not_found" });
    }
    return sendJson(res, 200, {
      stationId: station.id,
      status: station.online ? "ready" : "offline",
      streamUrl: station.streamUrl || null,
      message: "Realtime stream endpoint placeholder — connect EGO ingest service here.",
    });
  }

  const streamStatusMatch = p.match(new RegExp(`^${BASE}/api/stream/([^/]+)/status$`));
  if (streamStatusMatch && req.method === "GET") {
    const stationId = decodeURIComponent(streamStatusMatch[1]);
    return sendJson(res, 200, getStreamStatus(stationId));
  }

  const streamFileMatch = p.match(new RegExp(`^${BASE}/api/stream/([^/]+)/(.*)$`));
  if (streamFileMatch && (req.method === "GET" || req.method === "HEAD")) {
    const stationId = decodeURIComponent(streamFileMatch[1]);
    const rel = streamFileMatch[2] || "";
    const disk =
      rel === "" || rel.endsWith("/")
        ? resolveStreamFile(stationId, "meta/info.json")
        : resolveStreamFile(stationId, rel);
    if (disk) {
      return serveFileWithRange(req, res, disk);
    }
    return send(res, 404, "Not Found\n", { "Content-Type": "text/plain" });
  }

  const uploadMatch = p.match(new RegExp(`^${BASE}/api/collection/stations/([^/]+)/upload$`));
  if (uploadMatch) {
    const stationId = decodeURIComponent(uploadMatch[1]);
    const station = collectionStations.find((s) => s.id === stationId);
    if (!station) {
      return sendJson(res, 404, { error: "station_not_found" });
    }
    if (req.method === "GET") {
      return sendJson(res, 200, {
        status: "ready",
        stationId,
        datasetUrl: enrichStation(station).datasetUrl,
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
          const error = status === 401 ? "unauthorized" : "bad_request";
          sendJson(res, status, {
            error,
            reason: err.reason || null,
            message: String(err.message || err),
          });
        });
    }
    return send(res, 405, "Method Not Allowed\n", { "Content-Type": "text/plain" });
  }

  if (p.startsWith(`${BASE}/branding/`)) {
    const rel = p.slice(`${BASE}/branding/`.length);
    const disk = safeJoin(BRANDING, rel);
    if (disk && fs.existsSync(disk) && fs.statSync(disk).isFile()) {
      return serveFile(req, res, disk);
    }
    return send(res, 404, "Not Found\n", { "Content-Type": "text/plain" });
  }

  if (p.startsWith(`${BASE}/bundled/`)) {
    const rel = p.slice(`${BASE}/bundled/`.length);
    const disk = safeJoin(BUNDLED, rel);
    if (disk && fs.existsSync(disk) && fs.statSync(disk).isFile()) {
      return serveFileWithRange(req, res, disk);
    }
    return send(res, 404, "Not Found\n", { "Content-Type": "text/plain" });
  }

  if (p === BASE || p === `${BASE}/`) {
    p = `${BASE}/index.html`;
  }

  if (!p.startsWith(`${BASE}/`)) {
    return send(res, 404, "Not Found\n", { "Content-Type": "text/plain" });
  }

  const rel = p.slice(BASE.length);
  const disk = safeJoin(ROOT, rel === "/" ? "/index.html" : rel);

  if (!disk) {
    return send(res, 403, "Forbidden\n", { "Content-Type": "text/plain" });
  }

  if (fs.existsSync(disk) && fs.statSync(disk).isFile()) {
    if (disk.endsWith(".html")) {
      const html = injectBranding(fs.readFileSync(disk, "utf8"));
      return send(res, 200, html, { "Content-Type": "text/html; charset=utf-8" });
    }
    return serveFile(req, res, disk);
  }

  const index = path.join(ROOT, "index.html");
  if (rel.startsWith("/assets/")) {
    return send(res, 404, "Not Found\n", { "Content-Type": "text/plain" });
  }

  if (fs.existsSync(index)) {
    const html = injectBranding(fs.readFileSync(index, "utf8"));
    return send(res, 200, html, { "Content-Type": "text/html; charset=utf-8" });
  }

  return send(res, 404, "Not Found\n", { "Content-Type": "text/plain" });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`Visualizer on :${PORT}${BASE}/ (assets=${ROOT}, bundled=${BUNDLED})`);
});
