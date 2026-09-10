/**
 * Segment archive frame_index contract (manifest schema v2).
 * rows.jsonl and frames/*.bin share session-global indices when declared in manifest.
 */

import fs from "node:fs";
import path from "node:path";

export function readSegmentManifest(extractDir) {
  const manifestPath = path.join(extractDir, "manifest.json");
  if (!fs.existsSync(manifestPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  } catch {
    return null;
  }
}

function segmentStartFrame(manifest) {
  if (!manifest || typeof manifest !== "object") return null;
  const raw = manifest.start_frame_index ?? manifest.startFrameIndex;
  if (raw == null || raw === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

/**
 * Resolve the session-global frame index for a row.
 * - Schema v2 / session-global: row.frame_index is already session-global (>= start_frame_index).
 * - Legacy segment-local: remap via frame_map global_start.
 */
export function resolveFrameIndex(row, manifest, globalStart = 0) {
  const raw = Number(row.frame_index ?? row.frameIndex ?? -1);
  if (!Number.isInteger(raw) || raw < 0) return -1;
  const segStart = segmentStartFrame(manifest);
  // Session-global rows only when manifest declares a non-zero segment origin.
  // MCAP H264 trim resets per-segment rows to 0..N-1 with start_frame_index=0 — use globalStart.
  if (segStart != null && segStart > 0 && raw >= segStart) return raw;
  return globalStart + raw;
}

export function frameBinName(frameIndex) {
  return `${String(frameIndex).padStart(8, "0")}.bin`;
}

export function stagingFrameName(frameIndex) {
  return `frame_${String(frameIndex).padStart(6, "0")}.jpg`;
}
