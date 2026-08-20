/**
 * P2 unit-layout derive pipeline (DERIVE_LAYOUT=unit).
 */

import fs from "node:fs";
import path from "node:path";

import { stationRoot, deriveLog } from "./station-context.mjs";
import { deriveUnit } from "./unit.mjs";
import { publishSessionIfNeeded, rebuildView, refreshViewerFromManifest, updateInfoFromManifest } from "./publisher.mjs";
import { rebuildManifestFromDisk, writeMuxValidatedFromManifest } from "./manifest.mjs";
import {
  hasSessionMarker,
  markSessionFailed,
  markSessionReady,
  SESSION_MARKERS,
} from "../session-markers.mjs";
import { unitDir } from "./unit-paths.mjs";

function pickSessionId(root, forcedSessionId) {
  if (forcedSessionId) return forcedSessionId;
  const sessionsDir = path.join(root, "state", "sessions");
  if (!fs.existsSync(sessionsDir)) return null;
  for (const sid of fs.readdirSync(sessionsDir).sort()) {
    if (hasSessionMarker(root, sid, SESSION_MARKERS.DONE_UPLOAD)) return sid;
  }
  return null;
}

export async function runDerivePipelineUnit(stationId, options = {}) {
  const { sessionId: forcedSessionId = null, muxOnly = false, rebuildViewOnly = false } = options;
  const root = stationRoot(stationId);
  const sessionId = pickSessionId(root, forcedSessionId);
  if (!sessionId) {
    return { ok: false, reason: "no_session", stationId };
  }

  if (rebuildViewOnly) {
    const manifest = rebuildView(root, stationId);
    deriveLog(stationId, "derive_unit_rebuild_view", { episodes: manifest.episodes.length });
    return { ok: true, phase: "READY", sessionId, manifest };
  }

  if (!hasSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD)) {
    return { ok: false, reason: "done_upload_missing", sessionId };
  }

  deriveLog(stationId, "derive_unit_pipeline_start", { sessionId, muxOnly });

  if (muxOnly) {
    const existing = unitDir(root, sessionId);
    if (!fs.existsSync(path.join(existing, "unit.json"))) {
      return { ok: false, reason: "unit_missing", sessionId };
    }
    const pub = publishSessionIfNeeded(root, stationId, sessionId);
    return { ok: true, phase: "READY", sessionId, publish: pub };
  }

  const unitResult = await deriveUnit(stationId, root, sessionId, { attempt: 1 });
  if (!unitResult.ok) {
    markSessionFailed(root, sessionId, unitResult.gate.reason, { gate: unitResult.gate });
    deriveLog(stationId, "derive_unit_failed", {
      sessionId,
      reasonCode: unitResult.gate.reason?.code,
    });
    return {
      ok: false,
      phase: "FAILED",
      sessionId,
      gate: unitResult.gate,
      reason: unitResult.gate.reason,
    };
  }

  const pub = publishSessionIfNeeded(root, stationId, sessionId);
  const manifest = rebuildManifestFromDisk(root, stationId);
  updateInfoFromManifest(root, manifest);
  writeMuxValidatedFromManifest(root, stationId, manifest);
  refreshViewerFromManifest(root, stationId, manifest);
  const gate = unitResult.gate;
  markSessionReady(root, sessionId, {
    total: unitResult.frames,
    parquetRows: unitResult.frames,
    mp4Ok: true,
    gate,
    layout: "unit",
    episodeIndex: pub.episodeIndex,
  });
  deriveLog(stationId, "derive_unit_ready", {
    sessionId,
    frames: unitResult.frames,
    episodeIndex: pub.episodeIndex,
    checksPassed: gate.checksPassed,
  });
  return {
    ok: true,
    phase: "READY",
    sessionId,
    gate,
    publish: pub,
    frames: unitResult.frames,
  };
}

export async function runUnitFsck(root, stationId, { repair = false } = {}) {
  const manifest = rebuildManifestFromDisk(root, stationId);
  const issues = [];
  for (const ep of manifest.episodes) {
    const udir = unitDir(root, ep.session_id);
    if (!fs.existsSync(path.join(udir, "unit.json"))) {
      issues.push({ session_id: ep.session_id, issue: "unit_missing" });
    }
  }
  if (repair && issues.length) {
    rebuildView(root, stationId);
  }
  return { ok: issues.length === 0, issues, manifest };
}
