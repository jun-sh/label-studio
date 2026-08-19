/**
 * Derive pipeline orchestration (Phase0 §6 steps 1–7; no READY/GC in Phase4).
 */

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { rawSegmentArchivePath } from "./io.mjs";
import { stationRoot, deriveLog } from "./station-context.mjs";
import {
  buildFrameMapForDerive,
  readFrameMap,
  validateFrameMapContinuity,
  validateFrameMapPrerequisites,
} from "./frame-map.mjs";
import {
  cleanupExtractDir,
  extractTarZstSync,
  makeExtractDir,
  validateRawArchive,
} from "./tar-io.mjs";
import { writeMainTableFromSegments } from "./parquet-writer.mjs";
import { runSessionMux } from "./mux-exec.mjs";
import { materializeStagingFromExtractDir } from "../ingest/staging-materialize.mjs";
import { listSegmentStates, SEGMENT_INGEST_STATUS } from "../ingest/segment-state.mjs";
import {
  hasSessionMarker,
  markSessionFailed,
  markSessionReady,
  SESSION_MARKERS,
} from "../session-markers.mjs";
import { runReadyGate } from "./ready-gate.mjs";
import { runLifecycleGc } from "./lifecycle-gc.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const IMU_INGEST_SCRIPT = path.join(__dirname, "imu", "ingest-raw.py");

function resolvePython() {
  for (const bin of ["python3", "python"]) {
    const res = spawnSync(bin, ["--version"], { encoding: "utf8" });
    if (res.status === 0) return bin;
  }
  return "python3";
}

function ingestImuFromExtract(root, extractDir, sessionId, segmentId, { replace = false } = {}) {
  const imuPath = path.join(extractDir, "imu_raw.jsonl");
  if (!fs.existsSync(imuPath)) {
    throw new Error(`imu_raw.jsonl missing for ${segmentId}`);
  }
  const py = resolvePython();
  const args = [
    IMU_INGEST_SCRIPT,
    root,
    "--imu-jsonl",
    imuPath,
    "--segment-id",
    segmentId,
    "--session-id",
    sessionId,
  ];
  if (replace) args.push("--replace");
  else args.push("--append");
  const res = spawnSync(py, args, { encoding: "utf8" });
  const raw = String(res.stdout || "").trim();
  if (res.status !== 0) {
    throw new Error(String(res.stderr || raw || "ingest-raw failed").slice(0, 500));
  }
  try {
    return JSON.parse(raw.split("\n").filter(Boolean).pop() || "{}");
  } catch {
    return { ok: true };
  }
}

function listDeriveSegments(root, sessionId = null) {
  return listSegmentStates(root, {
    sessionId,
    status: SEGMENT_INGEST_STATUS.DERIVE_PENDING,
  });
}

function pickSessionId(root, stationId, forcedSessionId) {
  if (forcedSessionId) return forcedSessionId;
  const pending = listDeriveSegments(root);
  if (pending.length) return pending[0].session_id;
  const sessionsDir = path.join(root, "state", "sessions");
  if (!fs.existsSync(sessionsDir)) return null;
  for (const sid of fs.readdirSync(sessionsDir).sort()) {
    if (hasSessionMarker(root, sid, SESSION_MARKERS.DONE_UPLOAD)) return sid;
  }
  return null;
}

/**
 * Run derive pipeline (Phase0 §6 steps 1–10).
 * Steps 8–10: READY gate → session marker → lifecycle GC.
 */
export async function runDerivePipeline(stationId, options = {}) {
  const { sessionId: forcedSessionId = null, muxOnly = false } = options;
  const root = stationRoot(stationId);
  const sessionId = pickSessionId(root, stationId, forcedSessionId);
  if (!sessionId) {
    return { ok: false, reason: "no_session", stationId };
  }

  if (!hasSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD)) {
    deriveLog(stationId, "derive_skip_no_done_upload", { sessionId });
    return { ok: false, reason: "done_upload_missing", sessionId };
  }

  deriveLog(stationId, "derive_pipeline_start", { sessionId, muxOnly });

  const frameMapResult = buildFrameMapForDerive(root);
  if (!frameMapResult.ok) {
    deriveLog(stationId, "derive_frame_map_not_ready", {
      sessionId,
      message: frameMapResult.message,
    });
    return {
      ok: false,
      reason: frameMapResult.reason,
      message: frameMapResult.message,
      sessionId,
    };
  }
  const frameMap = frameMapResult.frameMap;

  const segmentExtracts = [];

  if (!muxOnly) {
  // Step 1–5: read raw, validate, extract, staging, IMU sensor_raw
    let imuReplace = true;
    for (const seg of frameMap.frame_segments) {
      const { session_id: sid, segment_id: segmentId, global_start: globalStart } = seg;
      const archivePath = rawSegmentArchivePath(root, sid, segmentId);
      if (!fs.existsSync(archivePath)) {
        throw new Error(`raw archive missing: ${archivePath}`);
      }

      deriveLog(stationId, "derive_segment_start", { sessionId: sid, segmentId });

      const validation = await validateRawArchive(archivePath);
      if (!validation.ok) {
        throw new Error(`tar validation failed for ${segmentId}: ${(validation.issues || []).join("; ")}`);
      }

      const extractDir = makeExtractDir(root, sid, segmentId);
      try {
        extractTarZstSync(archivePath, extractDir);
        materializeStagingFromExtractDir(root, extractDir, {
          stationId,
          globalStart,
        });
        ingestImuFromExtract(root, extractDir, sid, segmentId, { replace: imuReplace });
        imuReplace = false;
        segmentExtracts.push({ sessionId: sid, segmentId, extractDir });
      } catch (err) {
        cleanupExtractDir(extractDir);
        throw err;
      }
    }

    const tableReport = writeMainTableFromSegments(root, stationId, frameMap, segmentExtracts);
    deriveLog(stationId, "derive_main_table_done", {
      sessionId,
      rows: tableReport.rows,
      warnCount: tableReport.alignReport?.warn_count,
    });

    for (const item of segmentExtracts) {
      cleanupExtractDir(item.extractDir);
    }
  }

  const muxReport = await runSessionMux(stationId, root, readFrameMap(root) || frameMap, sessionId);
  deriveLog(stationId, "derive_mux_done", {
    sessionId,
    muxOk: muxReport.validated?.ok,
    allOk: muxReport.allOk,
  });

  const finalFrameMap = readFrameMap(root) || frameMap;
  let gate;
  try {
    gate = runReadyGate(root, stationId, sessionId);
  } catch (err) {
    const reason = {
      code: "GATE_INTERNAL_ERROR",
      message: String(err?.message || err).slice(0, 500),
      category: "derive",
    };
    markSessionFailed(root, sessionId, reason, {
      gate: { ok: false, internalError: { stack: String(err?.stack || "").slice(0, 2000) } },
    });
    deriveLog(stationId, "derive_failed", {
      sessionId,
      reasonCode: reason.code,
      failedCheckId: "GATE",
    });
    return {
      ok: false,
      sessionId,
      frameMap: finalFrameMap,
      mux: muxReport,
      reason,
      phase: "FAILED",
    };
  }

  if (gate.ok) {
    markSessionReady(root, sessionId, {
      total: finalFrameMap.length,
      parquetRows: finalFrameMap.length,
      mp4Ok: true,
      gate,
    });
    const gc = runLifecycleGc(root, stationId, sessionId);
    deriveLog(stationId, "derive_ready", {
      sessionId,
      checksPassed: gate.checksPassed,
      stagingGc: gc.purged,
    });
    return {
      ok: true,
      sessionId,
      frameMap: finalFrameMap,
      mux: muxReport,
      gate,
      gc,
      phase: "READY",
    };
  }

  markSessionFailed(root, sessionId, gate.reason, { gate });
  const gc = runLifecycleGc(root, stationId, sessionId);
  deriveLog(stationId, "derive_failed", {
    sessionId,
    reasonCode: gate.reason?.code,
    failedCheckId: gate.failedCheckId,
    stagingGc: gc.purged,
  });
  return {
    ok: false,
    sessionId,
    frameMap: finalFrameMap,
    mux: muxReport,
    gate,
    gc,
    reason: gate.reason,
    phase: "FAILED",
  };
}

export { buildFrameMapForDerive, readFrameMap, validateFrameMapContinuity, validateFrameMapPrerequisites };
