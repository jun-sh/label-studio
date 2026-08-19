import path from "node:path";

export function stationRoot(stationId) {
  const streamRoot = process.env.STREAM_DATA_ROOT || "/srv/stream";
  return path.join(streamRoot, stationId);
}

export function ingestLog(stationId, event, fields = {}) {
  const parts = ["[ingest]", `station=${stationId}`, `event=${event}`];
  for (const [key, value] of Object.entries(fields)) {
    if (value === undefined || value === null) continue;
    parts.push(`${key}=${value}`);
  }
  console.log(parts.join(" "));
}
