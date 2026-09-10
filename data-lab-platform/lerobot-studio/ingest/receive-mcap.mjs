/**
 * MCAP (.mcap / .mcap.zst) ingest for ego-mcap-pilot (Track 1 P2).
 */

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { ingestLog, stationRoot } from "./station-context.mjs";
import {
  atomicMoveFile,
  ensureDir,
  purgeConflictingTarArchive,
  rawMcapArchivePath,
  sha256File,
} from "./io.mjs";
import {
  markSegmentIngestFailed,
  readSegmentState,
  SEGMENT_INGEST_STATUS,
  transitionSegmentState,
} from "./segment-state.mjs";
import { maybeMarkSessionDoneUpload, touchUploadActivity } from "./session-coordinator.mjs";
import { validateMcapArchive, validationErrorFromMcapResult } from "./mcap-validator.mjs";
import {
  assertSourceFormatCompatible,
  lockSessionSourceFormat,
  SOURCE_FORMAT,
} from "./protocol-guard.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function resolveIdentity(validation, hints) {
  const sessionId =
    hints.sessionId || validation?.session_id || validation?.sessionId;
  const segmentId =
    hints.segmentId || validation?.segment_id || validation?.segmentId;
  if (!sessionId || !segmentId) {
    throw new Error("cannot resolve sessionId/segmentId from MCAP");
  }
  return { sessionId, segmentId };
}

function buildDuplicateAck(state, { sessionId, segmentId, sha256 }) {
  return {
    status: state?.status || SEGMENT_INGEST_STATUS.DERIVE_PENDING,
    sessionId,
    segmentId,
    duplicate: true,
    sha256: sha256 || state?.sha256 || null,
    sourceFormat: "mcap",
    deriveAsync: false,
    message: "segment already ingested",
    framesCommitted: state?.frame_count || 0,
  };
}

/**
 * Ingest one MCAP archive already written to archivePath on disk.
 */
export async function ingestMcapArchive(stationId, options = {}) {
  const {
    archivePath,
    sessionId: hintSessionId,
    segmentId: hintSegmentId,
    expectedSha,
    expectedSegmentTotal = 0,
    source = "edge",
  } = options;

  const root = stationRoot(stationId);
  const actualSha = await sha256File(archivePath);
  if (expectedSha && actualSha !== String(expectedSha).toLowerCase()) {
    throw new Error(
      `sha256 mismatch expected=${String(expectedSha).slice(0, 12)} actual=${actualSha.slice(0, 12)}`,
    );
  }

  const validation = await validateMcapArchive(archivePath);
  if (!validation.ok) {
    const err = validationErrorFromMcapResult(validation);
    const sid = hintSessionId || validation.session_id || "sess_unknown";
    const seg = hintSegmentId || validation.segment_id || "seg_unknown";
    markSegmentIngestFailed(root, sid, seg, err);
    throw new Error(err.message);
  }

  const { sessionId, segmentId } = resolveIdentity(validation, {
    sessionId: hintSessionId,
    segmentId: hintSegmentId,
  });
  const purgedTar = purgeConflictingTarArchive(root, sessionId, segmentId);
  if (purgedTar) {
    ingestLog(stationId, "ingest_mcap_purge_tar", { sessionId, segmentId });
  }
  assertSourceFormatCompatible(stationId, root, sessionId, SOURCE_FORMAT.MCAP);
  const rawDest = rawMcapArchivePath(root, sessionId, segmentId);

  const existing = readSegmentState(root, sessionId, segmentId);
  if (fs.existsSync(rawDest)) {
    if (existing?.sha256 && existing.sha256 !== actualSha) {
      throw new Error(`raw exists with different sha256 for ${segmentId}`);
    }
    if (existing?.status === SEGMENT_INGEST_STATUS.DERIVE_PENDING) {
      return buildDuplicateAck(existing, { sessionId, segmentId, sha256: actualSha });
    }
  }

  ensureDir(path.dirname(rawDest));
  if (!fs.existsSync(rawDest)) {
    atomicMoveFile(archivePath, rawDest);
  } else if (path.resolve(archivePath) !== path.resolve(rawDest) && fs.existsSync(archivePath)) {
    try {
      fs.rmSync(archivePath, { force: true });
    } catch {
      /* ignore */
    }
  }

  lockSessionSourceFormat(root, sessionId, SOURCE_FORMAT.MCAP);

  const bytes = fs.statSync(rawDest).size;
  const frameCount = Number(validation.frame_count || 0);
  const mcapTopics = Object.keys(validation.topics || {});

  transitionSegmentState(root, sessionId, segmentId, SEGMENT_INGEST_STATUS.RECEIVED, {
    sha256: actualSha,
    bytes,
    raw_archive: path.relative(root, rawDest),
    source,
    sourceFormat: "mcap",
    received_at: new Date().toISOString(),
  });

  transitionSegmentState(root, sessionId, segmentId, SEGMENT_INGEST_STATUS.INGESTING, {
    sha256: actualSha,
    bytes,
    raw_archive: path.relative(root, rawDest),
    sourceFormat: "mcap",
  });

  transitionSegmentState(root, sessionId, segmentId, SEGMENT_INGEST_STATUS.DERIVE_PENDING, {
    sha256: actualSha,
    bytes,
    raw_archive: path.relative(root, rawDest),
    sourceFormat: "mcap",
    mcapTopics,
    frame_count: frameCount,
    integrity: { ok: true, checks: ["mcap_topics", "session_meta"] },
    error: null,
  });

  touchUploadActivity(root, { sessionId, expectedSegmentTotal });
  const done = maybeMarkSessionDoneUpload(root, sessionId, {
    segmentId,
    source,
  });

  ingestLog(stationId, "ingest_mcap_ok", {
    sessionId,
    segmentId,
    bytes,
    frames: frameCount,
    topics: mcapTopics.length,
    sessionDoneUpload: done.marked,
  });

  return {
    status: SEGMENT_INGEST_STATUS.DERIVE_PENDING,
    sessionId,
    segmentId,
    duplicate: false,
    sha256: actualSha,
    bytes,
    sourceFormat: "mcap",
    deriveAsync: false,
    message: done.marked ? "ingested; session DONE_UPLOAD" : "ingested; awaiting session segments",
    framesCommitted: frameCount,
    sessionDoneUpload: done.marked,
  };
}

export async function handleMcapIngestUpload(stationId, options) {
  return ingestMcapArchive(stationId, options);
}
