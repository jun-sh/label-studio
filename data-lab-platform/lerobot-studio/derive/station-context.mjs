import path from "node:path";

export const DEFAULT_FPS = Number(process.env.STREAM_MUX_FPS || 30);

export function stationRoot(stationId) {
  const streamRoot = process.env.STREAM_DATA_ROOT || "/srv/stream";
  return path.join(streamRoot, stationId);
}

export function deriveLog(stationId, event, fields = {}) {
  const parts = ["[derive]", `station=${stationId}`, `event=${event}`];
  for (const [key, value] of Object.entries(fields)) {
    if (value === undefined || value === null) continue;
    parts.push(`${key}=${value}`);
  }
  console.log(parts.join(" "));
}
