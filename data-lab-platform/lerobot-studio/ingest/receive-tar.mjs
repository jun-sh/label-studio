/**
 * Raw-first tar.zst ingest orchestration (Phase3).
 */

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { ingestLog, stationRoot } from "./station-context.mjs";
import {
  atomicMoveFile,
  ensureDir,
  purgeConflictingMcapArchive,
  rawSegmentArchivePath,
  sha256File,
} from "./io.mjs";
import {
  assertSourceFormatCompatible,
  lockSessionSourceFormat,
  SOURCE_FORMAT,
} from "./protocol-guard.mjs";
import {
  markSegmentIngestFailed,
  readSegmentState,
  SEGMENT_INGEST_STATUS,
  transitionSegmentState,
} from "./segment-state.mjs";
import { maybeMarkSessionDoneUpload, touchUploadActivity } from "./session-coordinator.mjs";
import { writeServerSessionSeal } from "./session-seal.mjs";
import { materializeStagingFromExtractDir } from "./staging-materialize.mjs";
import { validateTarZstArchive, validationErrorFromResult } from "./tar-validator.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const EXTRACT_SCRIPT = path.join(__dirname, "..", "scripts", "extract-tar-zst.py");

function resolvePython() {
  for (const bin of ["python3", "python"]) {
    const res = spawnSync(bin, ["--version"], { encoding: "utf8" });
    if (res.status === 0) return bin;
  }
  return "python3";
}

function extractTarZst(archivePath, destDir, expectedSha256) {
  const py = resolvePython();
  const args = [EXTRACT_SCRIPT, archivePath, destDir];
  if (expectedSha256) args.push("--expected-sha256", expectedSha256);
  const res = spawnSync(py, args, { encoding: "utf8" });
  if (res.status !== 0) {
    let msg = String(res.stderr || res.stdout || "extract failed").trim();
    try {
      const parsed = JSON.parse(msg.split("\n").filter(Boolean).pop() || "{}");
      if (parsed.error) msg = parsed.error;
    } catch {
      /* keep */
    }
    throw new Error(msg.slice(0, 500));
  }
}

function resolveIdentity(manifest, hints) {
  const sessionId = hints.sessionId || manifest?.session_id || manifest?.sessionId;
  const segmentId = hints.segmentId || manifest?.segment_id || manifest?.segmentId;
  if (!sessionId || !segmentId) {
    throw new Error("cannot resolve sessionId/segmentId");
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
    deriveAsync: false,
    message: "segment already ingested",
    framesCommitted: 0,
  };
}

/**
 * Ingest one tar.zst archive already written to archivePath on disk.
 */
export async function ingestTarZstArchive(stationId, options = {}) {
  const {
    archivePath,
    sessionId: hintSessionId,
    segmentId: hintSegmentId,
    expectedSha,
    expectedSegmentTotal = 0,
    sessionSeal = null,
    source = "edge",
  } = options;

  const root = stationRoot(stationId);
  const actualSha = await sha256File(archivePath);
  if (expectedSha && actualSha !== String(expectedSha).toLowerCase()) {
    throw new Error(
      `sha256 mismatch expected=${String(expectedSha).slice(0, 12)} actual=${actualSha.slice(0, 12)}`,
    );
  }

  const preValidate = await validateTarZstArchive(archivePath);
  const identity = resolveIdentity(preValidate.manifest, {
    sessionId: hintSessionId,
    segmentId: hintSegmentId,
  });
  const { sessionId, segmentId } = identity;
  const purgedMcap = purgeConflictingMcapArchive(root, sessionId, segmentId);
  if (purgedMcap) {
    ingestLog(stationId, "ingest_tar_purge_mcap", { sessionId, segmentId });
  }
  assertSourceFormatCompatible(stationId, root, sessionId, SOURCE_FORMAT.TARZST);
  const rawDest = rawSegmentArchivePath(root, sessionId, segmentId);

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

  lockSessionSourceFormat(root, sessionId, SOURCE_FORMAT.TARZST);

  const bytes = fs.statSync(rawDest).size;
  transitionSegmentState(root, sessionId, segmentId, SEGMENT_INGEST_STATUS.RECEIVED, {
    sha256: actualSha,
    bytes,
    raw_archive: path.relative(root, rawDest),
    source,
    sourceFormat: SOURCE_FORMAT.TARZST,
    received_at: new Date().toISOString(),
  });

  transitionSegmentState(root, sessionId, segmentId, SEGMENT_INGEST_STATUS.INGESTING, {
    sha256: actualSha,
    bytes,
    raw_archive: path.relative(root, rawDest),
    sourceFormat: SOURCE_FORMAT.TARZST,
  });

  const extractDir = path.join(
    root,
    ".upload",
    "extract",
    `ingest_${sessionId}_${segmentId}_${Date.now()}`,
  );

  try {
    const validation = preValidate.ok ? preValidate : await validateTarZstArchive(rawDest);
    if (!validation.ok) {
      const err = validationErrorFromResult(validation);
      markSegmentIngestFailed(root, sessionId, segmentId, err);
      throw new Error(err.message);
    }

    extractTarZst(rawDest, extractDir, actualSha);
    const staged = materializeStagingFromExtractDir(root, extractDir, { stationId });

    transitionSegmentState(root, sessionId, segmentId, SEGMENT_INGEST_STATUS.DERIVE_PENDING, {
      sha256: actualSha,
      bytes,
      raw_archive: path.relative(root, rawDest),
      sourceFormat: SOURCE_FORMAT.TARZST,
      frame_count: staged.frameCount,
      integrity: { ok: true, checks: ["manifest", "rows.jsonl", "imu_raw.jsonl", "frames"] },
      error: null,
    });

    touchUploadActivity(root, { sessionId, expectedSegmentTotal });
    if (sessionSeal?.complete) {
      writeServerSessionSeal(root, sessionId, sessionSeal, { source });
    }
    const done = maybeMarkSessionDoneUpload(root, sessionId, {
      segmentId,
      source,
    });

    ingestLog(stationId, "ingest_segment_ok", {
      sessionId,
      segmentId,
      bytes,
      frames: staged.frameCount,
      sessionDoneUpload: done.marked,
    });

    return {
      status: SEGMENT_INGEST_STATUS.DERIVE_PENDING,
      sessionId,
      segmentId,
      duplicate: false,
      sha256: actualSha,
      bytes,
      sourceFormat: SOURCE_FORMAT.TARZST,
      deriveAsync: false,
      message: done.marked ? "ingested; session DONE_UPLOAD" : "ingested; awaiting session segments",
      framesCommitted: staged.framesWritten,
      sessionDoneUpload: done.marked,
    };
  } catch (err) {
    if (readSegmentState(root, sessionId, segmentId)?.status !== SEGMENT_INGEST_STATUS.INGEST_FAILED) {
      markSegmentIngestFailed(root, sessionId, segmentId, err);
    }
    ingestLog(stationId, "ingest_segment_fail", {
      sessionId,
      segmentId,
      message: String(err?.message || err).slice(0, 300),
    });
    throw err;
  } finally {
    try {
      if (fs.existsSync(extractDir)) fs.rmSync(extractDir, { recursive: true, force: true });
    } catch {
      /* staging retained; extract temp only */
    }
  }
}

export async function handleTarZstIngestUpload(stationId, options = {}) {
  return ingestTarZstArchive(stationId, options);
}
