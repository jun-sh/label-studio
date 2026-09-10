/**
 * D1: MCAP single-segment derive fast path (parallel remux + lighter gates).
 */

import { DEFAULT_FPS } from "./station-context.mjs";
import { videoKeysForStation } from "../ingest/staging-materialize.mjs";

export function mcapSingleFastEnabled() {
  const raw = String(process.env.DERIVE_MCAP_SINGLE_FAST ?? "1").trim().toLowerCase();
  return !["0", "false", "off", "legacy"].includes(raw);
}

export function isMcapSingleFastCandidate(segments) {
  if (!mcapSingleFastEnabled()) return false;
  if (!Array.isArray(segments) || segments.length !== 1) return false;
  const seg = segments[0];
  return String(seg?.sourceFormat || "").toLowerCase() === "mcap";
}

export function fastMp4FrameProbeOptions({ muxMode = "remux" } = {}) {
  return {
    defaultFps: DEFAULT_FPS,
    // Ingest state + MCAP summary already bound frame count; skip ffprobe -count_frames.
    exact: false,
    muxMode,
  };
}

/** G2b fast: decode-probe primary camera only (H.264 remux parity checked on primary). */
export function primaryVideoKeyForStation(stationId) {
  const keys = videoKeysForStation(stationId);
  return keys[0] || "video.cam_front";
}
