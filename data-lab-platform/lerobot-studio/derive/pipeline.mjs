/**
 * Derive pipeline orchestration — unit layout only (Phase D).
 */

export async function runDerivePipeline(stationId, options = {}) {
  const { runDerivePipelineUnit } = await import("./pipeline-unit.mjs");
  return runDerivePipelineUnit(stationId, options);
}

export { readFrameMap, validateFrameMapContinuity, validateFrameMapPrerequisites } from "./frame-map.mjs";
