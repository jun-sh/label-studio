/**
 * Reverse-proxy MJPEG preview from EGO capture station (does not touch stream-ingest).
 */
import http from "node:http";

const PREVIEW_PORT = Number(process.env.PREVIEW_HTTP_PORT || 8765);
const VALID_CAMS = new Set(["head_left", "head_right", "depth", "cam_02"]);

export function proxyStationPreview(req, res, station, cam, { send, corsHeaders }) {
  if (!station || station.scope !== "lan" || !station.host) {
    return send(res, 404, "station_not_found\n", { "Content-Type": "text/plain" });
  }
  if (!VALID_CAMS.has(cam)) {
    return send(res, 404, "camera_not_found\n", { "Content-Type": "text/plain" });
  }

  const upstream = `http://${station.host}:${PREVIEW_PORT}/preview/${encodeURIComponent(cam)}/mjpeg`;
  const proxyReq = http.get(upstream, (upstreamRes) => {
    if (upstreamRes.statusCode && upstreamRes.statusCode >= 400) {
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
    upstreamRes.pipe(res);
  });

  proxyReq.on("error", () => {
    if (!res.headersSent) {
      send(res, 502, "preview_upstream_error\n", { "Content-Type": "text/plain" });
    } else {
      res.end();
    }
  });

  req.on("close", () => {
    proxyReq.destroy();
  });
}
