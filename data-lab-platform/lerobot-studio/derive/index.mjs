/**
 * EGO-001 Phase4 derive module public API.
 */

export {
  buildFrameMap,
  rebuildAndWriteFrameMap,
  readFrameMap,
  writeFrameMap,
  findSegmentEntry,
  globalIndexForLocal,
  validateFrameMapContinuity,
  validateFrameMapPrerequisites,
  buildFrameMapForDerive,
  FrameMapError,
  episodeMapPath,
  FRAME_MAP_VERSION,
} from "./frame-map.mjs";

export {
  writeMainTableFromSegments,
  buildMainTableRows,
  remapSegmentRows,
  mainJsonlPath,
  mainParquetPath,
  alignMainTableImu,
} from "./parquet-writer.mjs";

export { runSessionMux, runFourCameraMux, writeMuxValidatedSnapshot } from "./mux-exec.mjs";

export { runDerivePipeline } from "./pipeline.mjs";

export {
  runReadyGate,
  checkMainTableContinuity,
  checkMp4FrameCoverage,
  checkMuxValidated,
  checkLeRobotSchema,
  checkImuRawParquet,
  checkImuMainAlignment,
  READY_GATE_CHECKS_TOTAL,
} from "./ready-gate.mjs";

export {
  runLifecycleGc,
  evaluateStagingGc,
  purgeAllStagingJpgs,
  FAILED_STAGING_RETENTION_MS,
} from "./lifecycle-gc.mjs";

export { rawSegmentArchivePath } from "./io.mjs";
