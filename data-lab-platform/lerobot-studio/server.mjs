/**
 * LeRobot Studio static host + sample datasets API (IO-AI compatible paths).
 */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = process.env.LEROBOT_STUDIO_ROOT || "/srv/lerobot";
const PORT = Number(process.env.PORT || 7860);
const BASE = "/lerobot";

const datasetsPath =
  process.env.LEROBOT_DATASETS_JSON || path.join(__dirname, "config", "datasets.json");
const datasets = JSON.parse(fs.readFileSync(datasetsPath, "utf8"));

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".wasm": "application/wasm",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".ico": "image/x-icon",
};

function send(res, status, body, headers = {}) {
  res.writeHead(status, { "Cache-Control": "no-cache", ...headers });
  res.end(body);
}

function safeJoin(root, urlPath) {
  const rel = decodeURIComponent(urlPath).replace(/\0/g, "");
  const resolved = path.normalize(path.join(root, rel));
  if (!resolved.startsWith(path.normalize(root + path.sep)) && resolved !== path.normalize(root)) {
    return null;
  }
  return resolved;
}

function serveFile(res, filePath) {
  const ext = path.extname(filePath);
  const type = MIME[ext] || "application/octet-stream";
  const data = fs.readFileSync(filePath);
  send(res, 200, data, { "Content-Type": type });
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);
  let p = url.pathname;

  if (p === "/healthz") {
    return send(res, 200, "ok\n", { "Content-Type": "text/plain" });
  }

  // Sample list API (SPA fetches while cards show "Loading...")
  if (p === `${BASE}/datasets` || p === `${BASE}/api/datasets` || p === `${BASE}/samples`) {
    return send(res, 200, JSON.stringify(datasets), {
      "Content-Type": "application/json; charset=utf-8",
      "Access-Control-Allow-Origin": "*",
    });
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
    return serveFile(res, disk);
  }

  const index = path.join(ROOT, "index.html");
  if (fs.existsSync(index)) {
    return serveFile(res, index);
  }

  return send(res, 404, "Not Found\n", { "Content-Type": "text/plain" });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`LeRobot Studio on :${PORT}${BASE}/ (${ROOT})`);
});
