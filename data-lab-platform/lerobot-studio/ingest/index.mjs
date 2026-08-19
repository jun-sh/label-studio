/**
 * EGO-001 Phase3 ingest module public API.
 */

export {
  SEGMENT_INGEST_STATUS,
  listSegmentStates,
  listSessionSegmentStates,
  readSegmentState,
  segmentStateForApi,
} from "./segment-state.mjs";

export { validateTarZstArchive } from "./tar-validator.mjs";

export {
  evaluateSessionUploadGate,
  maybeMarkSessionDoneUpload,
  touchUploadActivity,
} from "./session-coordinator.mjs";

export { handleTarZstIngestUpload, ingestTarZstArchive } from "./receive-tar.mjs";

export { rawSegmentArchivePath } from "./io.mjs";
