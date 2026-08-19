/**
 * Episode global frame mapping (Phase0 §5).
 * Persists meta/episodes/episode_000.json with frame_segments.
 */

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { readJson, writeJsonAtomic } from "./io.mjs";
import { rawSegmentArchivePath } from "./io.mjs";
import { readSessionMarker, SESSION_MARKERS } from "../session-markers.mjs";
import { listSegmentStates, SEGMENT_INGEST_STATUS } from "../ingest/segment-state.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const VALIDATE_SCRIPT = path.join(__dirname, "..", "scripts", "extract-tar-zst.py");

export const FRAME_MAP_VERSION = 1;
export const EPISODE_INDEX = 0;

export function episodeMapPath(root) {
  return path.join(root, "meta", "episodes", "episode_000.json");
}

export function readFrameMap(root) {
  return readJson(episodeMapPath(root), null);
}

export function writeFrameMap(root, frameMap) {
  writeJsonAtomic(episodeMapPath(root), frameMap);
  return frameMap;
}

function resolvePython() {
  for (const bin of ["python3", "python"]) {
    const res = spawnSync(bin, ["--version"], { encoding: "utf8" });
    if (res.status === 0) return bin;
  }
  return "python3";
}

export function readManifestFromArchive(archivePath) {
  const py = resolvePython();
  const res = spawnSync(py, [VALIDATE_SCRIPT, archivePath, "--validate-json"], { encoding: "utf8" });
  const raw = String(res.stdout || "").trim();
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed.ok ? parsed.manifest : null;
  } catch {
    return null;
  }
}

function sessionUploadedAt(root, sessionId) {
  const marker = readSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD);
  if (marker?.at) return marker.at;
  const activity = readJson(path.join(root, "live", "upload_activity.json"), {});
  const ent = activity.sessions?.[sessionId] || {};
  return ent.updatedAt || ent.uploadedAt || new Date(0).toISOString();
}

function collectDeriveSegments(root, { sessionIds = null } = {}) {
  const states = listSegmentStates(root, { status: SEGMENT_INGEST_STATUS.DERIVE_PENDING });
  const bySession = new Map();
  for (const state of states) {
    const sid = state.session_id;
    if (sessionIds && !sessionIds.includes(sid)) continue;
    if (!bySession.has(sid)) bySession.set(sid, []);
    bySession.get(sid).push(state);
  }

  const sessions = [];
  for (const [sessionId, segs] of bySession.entries()) {
    const uploadedAt = sessionUploadedAt(root, sessionId);
    const segmentEntries = segs.map((state) => {
      const archivePath = rawSegmentArchivePath(root, sessionId, state.segment_id);
      const manifest = fs.existsSync(archivePath) ? readManifestFromArchive(archivePath) : null;
      const frameCount = Number(
        state.frame_count || manifest?.frame_count || manifest?.frameCount || 0,
      );
      const createdAt = manifest?.created_at || manifest?.createdAt || state.received_at || "";
      return {
        sessionId,
        segmentId: state.segment_id,
        frameCount,
        createdAt,
        manifest,
        state,
      };
    });
    segmentEntries.sort((a, b) => {
      const ca = String(a.createdAt || a.segmentId);
      const cb = String(b.createdAt || b.segmentId);
      if (ca !== cb) return ca.localeCompare(cb);
      return String(a.segmentId).localeCompare(String(b.segmentId));
    });
    sessions.push({ sessionId, uploadedAt, segmentEntries });
  }

  sessions.sort((a, b) => {
    const ua = String(a.uploadedAt);
    const ub = String(b.uploadedAt);
    if (ua !== ub) return ua.localeCompare(ub);
    return String(a.sessionId).localeCompare(String(b.sessionId));
  });
  return sessions;
}

/**
 * Build frame map from DERIVE_PENDING segments (Phase0 §5.3).
 */
export function buildFrameMap(root, options = {}) {
  const sessionsInput = collectDeriveSegments(root, options);
  const frameSegments = [];
  let globalCursor = 0;

  const sessionsMeta = sessionsInput.map(({ sessionId, uploadedAt, segmentEntries }) => {
    for (const seg of segmentEntries) {
      const frameCount = Math.max(0, Number(seg.frameCount || 0));
      const localMin = 0;
      const localMax = frameCount > 0 ? frameCount - 1 : -1;
      const globalStart = globalCursor;
      const globalEnd = frameCount > 0 ? globalCursor + frameCount - 1 : globalCursor - 1;
      if (frameCount > 0) {
        frameSegments.push({
          session_id: sessionId,
          segment_id: seg.segmentId,
          local_frame_min: localMin,
          local_frame_max: localMax,
          global_start: globalStart,
          global_end: globalEnd,
          frame_count: frameCount,
        });
        globalCursor += frameCount;
      }
    }
    return {
      session_id: sessionId,
      uploaded_at: uploadedAt,
      segment_count: segmentEntries.length,
    };
  });

  const length = globalCursor;
  const frameMap = {
    episode_index: EPISODE_INDEX,
    length,
    frame_index_min: length > 0 ? 0 : 0,
    frame_index_max: length > 0 ? length - 1 : -1,
    sessions: sessionsMeta,
    frame_segments: frameSegments,
    mapping_version: FRAME_MAP_VERSION,
    updated_at: new Date().toISOString(),
  };
  return frameMap;
}

export function rebuildAndWriteFrameMap(root, options = {}) {
  const frameMap = buildFrameMap(root, options);
  writeFrameMap(root, frameMap);
  return frameMap;
}

export function findSegmentEntry(frameMap, sessionId, segmentId) {
  return (frameMap?.frame_segments || []).find(
    (s) => s.session_id === sessionId && s.segment_id === segmentId,
  );
}

export function globalIndexForLocal(frameMap, sessionId, segmentId, localIndex) {
  const entry = findSegmentEntry(frameMap, sessionId, segmentId);
  if (!entry) {
    throw new Error(`frame_segments missing for ${sessionId}/${segmentId}`);
  }
  return entry.global_start + localIndex;
}

export function validateFrameMapContinuity(frameMap) {
  const segs = [...(frameMap?.frame_segments || [])].sort((a, b) => a.global_start - b.global_start);
  if (!segs.length) return { ok: true, issues: [] };
  const issues = [];
  let expected = segs[0].global_start;
  for (const seg of segs) {
    if (seg.global_start !== expected) {
      issues.push(`gap at ${seg.segment_id}: expected global_start ${expected}, got ${seg.global_start}`);
    }
    const span = seg.global_end - seg.global_start + 1;
    if (span !== seg.frame_count) {
      issues.push(`frame_count mismatch for ${seg.segment_id}`);
    }
    expected = seg.global_end + 1;
  }
  const length = frameMap.length ?? 0;
  if (expected !== length) {
    issues.push(`length ${length} != end+1 ${expected}`);
  }
  return { ok: issues.length === 0, issues };
}
