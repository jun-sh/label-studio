/**
 * Episode provenance: raw MCAP → stream LeRobot → convert corpus linkage.
 */

import fs from "node:fs";
import path from "node:path";

export function pipelineRootFromStreamRoot(streamStationRoot) {
  const explicit = process.env.DATALAB_ROOT || process.env.EGO_DATALAB_ROOT || "";
  if (explicit) {
    return path.join(explicit, "data-storage", "pipeline");
  }
  return path.resolve(streamStationRoot, "../../pipeline");
}

/** True when ego-process convert finalized (HAMER / corpus path). */
export function isPoseReady(streamStationRoot, stationId, sessionId) {
  const marker = path.join(
    pipelineRootFromStreamRoot(streamStationRoot),
    stationId,
    sessionId,
    ".status",
    "finalize.done",
  );
  return fs.existsSync(marker);
}

export function episodeProvenance(streamStationRoot, stationId, sessionId, options = {}) {
  const videoCodec = String(options.video_codec || "jpeg").trim().toLowerCase();
  return {
    source_session_id: sessionId,
    video_codec: videoCodec,
    pose_ready: isPoseReady(streamStationRoot, stationId, sessionId),
  };
}
