/**
 * Reverse-proxy MJPEG preview from EGO capture station (does not touch stream-ingest).
 */
import http from "node:http";
import { getStationCaptureState } from "./stream-ingest.mjs";

const PREVIEW_PORT = Number(process.env.PREVIEW_HTTP_PORT || 8765);
const PREVIEW_PROXY_TIMEOUT_MS = Number(process.env.PREVIEW_PROXY_TIMEOUT_MS || 4000);
const PREVIEW_MAX_CONCURRENT_PER_STATION = Number(process.env.PREVIEW_PROXY_MAX_CONCURRENT || 4);
const CANONICAL_CAMS = new Set(["front_left", "front_right", "rear_left", "rear_right"]);
/** Historical preview URL segments → canonical short names on 214. */
const LEGACY_PREVIEW_CAM_ALIASES = {
  head_left: "front_left",
  head_right: "front_right",
  depth: "rear_left",
  cam_02: "rear_right",
};
const VALID_CAMS = new Set([...CANONICAL_CAMS, ...Object.keys(LEGACY_PREVIEW_CAM_ALIASES)]);

/** @type {Map<string, number>} */
const previewInflight = new Map();

function resolvePreviewCam(cam) {
  return LEGACY_PREVIEW_CAM_ALIASES[cam] || cam;
}

function acquirePreviewSlot(stationId) {
  const active = previewInflight.get(stationId) || 0;
  if (active >= PREVIEW_MAX_CONCURRENT_PER_STATION) return false;
  previewInflight.set(stationId, active + 1);
  return true;
}

function releasePreviewSlot(stationId) {
  const active = previewInflight.get(stationId) || 0;
  if (active <= 1) previewInflight.delete(stationId);
  else previewInflight.set(stationId, active - 1);
}

export function proxyStationPreview(req, res, station, cam, format, { send, corsHeaders }) {
  if (!station || station.scope !== "lan" || !station.host) {
    return send(res, 404, "station_not_found\n", { "Content-Type": "text/plain" });
  }
  const stationId = station.id || station.host;
  const captureState = getStationCaptureState(stationId);
  if (captureState !== "idle") {
    return send(res, 403, "preview_capture_active\n", {
      "Content-Type": "text/plain",
      "X-Capture-State": captureState,
      ...corsHeaders(),
    });
  }
  if (!VALID_CAMS.has(cam)) {
    return send(res, 404, "camera_not_found\n", { "Content-Type": "text/plain" });
  }

  if (!acquirePreviewSlot(stationId)) {
    return send(res, 429, "preview_concurrency_limit\n", { "Content-Type": "text/plain" });
  }

  let slotReleased = false;
  function releaseOnce() {
    if (slotReleased) return;
    slotReleased = true;
    releasePreviewSlot(stationId);
  }

  const upstreamCam = resolvePreviewCam(cam);
  const suffix = format === "jpg" ? "jpg" : "mjpeg";
  const upstream = `http://${station.host}:${PREVIEW_PORT}/preview/${encodeURIComponent(upstreamCam)}/${suffix}`;
  if (format === "jpg") {
    const proxyReq = http.get(upstream, { timeout: PREVIEW_PROXY_TIMEOUT_MS }, (upstreamRes) => {
      const chunks = [];
      upstreamRes.on("data", (c) => chunks.push(c));
      upstreamRes.on("end", () => {
        releaseOnce();
        const body = Buffer.concat(chunks);
        if (upstreamRes.statusCode && upstreamRes.statusCode >= 400) {
          return send(res, upstreamRes.statusCode, body.length ? body : "preview_unavailable\n", {
            "Content-Type": upstreamRes.headers["content-type"] || "text/plain",
          });
        }
        send(res, 200, body, {
          "Cache-Control": "no-cache, no-store",
          "Content-Type": upstreamRes.headers["content-type"] || "image/jpeg",
          ...corsHeaders(),
        });
      });
    });
    proxyReq.on("error", () => {
      releaseOnce();
      send(res, 502, "preview_upstream_error\n", { "Content-Type": "text/plain" });
    });
    proxyReq.on("timeout", () => {
      proxyReq.destroy();
      releaseOnce();
      send(res, 504, "preview_upstream_timeout\n", { "Content-Type": "text/plain" });
    });
    req.on("close", () => {
      proxyReq.destroy();
      releaseOnce();
    });
    return;
  }
  const proxyReq = http.get(upstream, { timeout: PREVIEW_PROXY_TIMEOUT_MS }, (upstreamRes) => {
    if (upstreamRes.statusCode && upstreamRes.statusCode >= 400) {
      releaseOnce();
      return send(res, upstreamRes.statusCode, "preview_unavailable\n", {
        "Content-Type": "text/plain",
      });
    }
    const headers = {
      "Cache-Control": "no-cache, no-store",
      "Content-Type":
        upstreamRes.headers["content-type"] || "multipart/x-mixed-replace; boundary=datalabframe",
      ...corsHeaders(),
    };
    res.writeHead(200, headers);
    upstreamRes.on("end", releaseOnce);
    upstreamRes.on("close", releaseOnce);
    upstreamRes.pipe(res);
  });

  proxyReq.on("error", () => {
    releaseOnce();
    if (!res.headersSent) {
      send(res, 502, "preview_upstream_error\n", { "Content-Type": "text/plain" });
    } else {
      res.end();
    }
  });
  proxyReq.on("timeout", () => {
    proxyReq.destroy();
    releaseOnce();
    if (!res.headersSent) {
      send(res, 504, "preview_upstream_timeout\n", { "Content-Type": "text/plain" });
    } else {
      res.end();
    }
  });

  req.on("close", () => {
    proxyReq.destroy();
    releaseOnce();
  });
}
