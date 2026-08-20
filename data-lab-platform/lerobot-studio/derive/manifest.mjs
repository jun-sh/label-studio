/**
 * P2 manifest: append-only journal + materialized manifest.json view.
 */

import fs from "node:fs";
import path from "node:path";

import { appendJsonlAtomic, readJson, writeJsonAtomic } from "./io.mjs";
import {
  MANIFEST_VERSION,
  derivedRoot,
  episodeChunkPaths,
  journalPath,
  manifestPath,
  unitJsonPath,
} from "./unit-paths.mjs";
import { videoKeysForStation } from "../ingest/staging-materialize.mjs";
import { probeMp4FrameCount } from "../mux-exec.mjs";
import { DEFAULT_FPS } from "./station-context.mjs";

export function appendJournalEvent(root, event) {
  const payload = {
    at: new Date().toISOString(),
    ...event,
  };
  appendJsonlAtomic(journalPath(root), [payload]);
  return payload;
}

export function readJournal(root) {
  const p = journalPath(root);
  if (!fs.existsSync(p)) return [];
  return fs
    .readFileSync(p, "utf8")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

export function readManifest(root) {
  return readJson(manifestPath(root), null);
}

export function isUnitLayoutPublished(root) {
  const manifest = readManifest(root);
  return Array.isArray(manifest?.episodes) && manifest.episodes.length > 0;
}

export function writeManifestAtomic(root, manifest) {
  writeJsonAtomic(manifestPath(root), manifest);
  return manifest;
}

/** Scan derived/<sess>/unit.json on disk (source of truth for published units). */
export function listReadyUnits(root) {
  const parent = derivedRoot(root);
  if (!fs.existsSync(parent)) return [];
  const units = [];
  for (const name of fs.readdirSync(parent).sort()) {
    if (!name.startsWith("sess_")) continue;
    const unitJson = unitJsonPath(root, name);
    if (!fs.existsSync(unitJson)) continue;
    const unit = readJson(unitJson, null);
    if (!unit?.session_id) continue;
    units.push(unit);
  }
  return units;
}

export function rebuildManifestFromDisk(root, stationId) {
  const units = listReadyUnits(root);
  units.sort((a, b) => {
    const ta = a.derive?.started_at || a.session_id;
    const tb = b.derive?.started_at || b.session_id;
    if (ta !== tb) return String(ta).localeCompare(String(tb));
    return String(a.session_id).localeCompare(String(b.session_id));
  });
  let cursor = 0;
  const episodes = units.map((unit, episodeIndex) => {
    const frames = Number(unit.frames || 0);
    const fromIndex = cursor;
    const toIndex = frames > 0 ? cursor + frames - 1 : cursor - 1;
    cursor += frames;
    return {
      episode_index: episodeIndex,
      session_id: unit.session_id,
      frames,
      from_index: fromIndex,
      to_index: toIndex,
    };
  });

  const journal = readJournal(root);
  const pending = [];
  const quarantined = [];
  for (const ev of journal) {
    if (ev.event === "derive_started" && ev.session_id) {
      const sid = ev.session_id;
      if (!units.some((u) => u.session_id === sid)) {
        pending.push({ session_id: sid, state: "DERIVING", attempt: ev.attempt ?? 1 });
      }
    }
    if (ev.event === "quarantined" && ev.session_id) {
      quarantined.push({ session_id: ev.session_id, after_attempts: ev.after_attempts });
    }
  }

  const manifest = {
    version: MANIFEST_VERSION,
    station_id: stationId,
    updated_at: new Date().toISOString(),
    total_frames: cursor,
    episodes,
    pending,
    quarantined,
  };
  writeManifestAtomic(root, manifest);
  return manifest;
}

export function episodeIndexForSession(manifest, sessionId) {
  const ep = (manifest?.episodes || []).find((e) => e.session_id === sessionId);
  return ep ? ep.episode_index : -1;
}

/** Station-wide metrics when manifest + per-episode L2 layout is active. */
export function resolveManifestDeriveMetrics(root, stationId) {
  const manifest = readManifest(root);
  if (!manifest?.episodes?.length) return null;
  const total = Number(manifest.total_frames || 0);
  if (total <= 0) return null;
  return {
    rowCount: total,
    frameIndexMin: 0,
    frameIndexMax: total > 0 ? total - 1 : -1,
    expectedMp4Frames: total,
    source: "manifest",
    episodeCount: manifest.episodes.length,
  };
}

export function verifyManifestMp4Coverage(root, stationId, manifest = null) {
  const m = manifest || readManifest(root);
  if (!m?.episodes?.length) return false;
  const expected = Number(m.total_frames || 0);
  if (expected <= 0) return false;
  const keys = videoKeysForStation(stationId);
  for (const videoKey of keys) {
    let sum = 0;
    for (const ep of m.episodes) {
      const rel = episodeChunkPaths(ep.episode_index).videoRel(videoKey);
      const mp4 = path.join(root, rel);
      if (!fs.existsSync(mp4)) return false;
      sum += probeMp4FrameCount(mp4, { defaultFps: DEFAULT_FPS });
    }
    if (sum < expected) return false;
  }
  return true;
}

export function writeMuxValidatedFromManifest(root, stationId, manifest = null) {
  const m = manifest || readManifest(root);
  if (!m?.episodes?.length) return null;
  const expected = Number(m.total_frames || 0);
  const frames = {};
  for (const videoKey of videoKeysForStation(stationId)) {
    let sum = 0;
    for (const ep of m.episodes) {
      const rel = episodeChunkPaths(ep.episode_index).videoRel(videoKey);
      const mp4 = path.join(root, rel);
      sum += fs.existsSync(mp4) ? probeMp4FrameCount(mp4, { defaultFps: DEFAULT_FPS }) : 0;
    }
    frames[videoKey] = sum;
  }
  const ok =
    expected > 0 &&
    Object.values(frames).length > 0 &&
    Object.values(frames).every((n) => Number(n) >= expected);
  const payload = {
    ok,
    checked_at: new Date().toISOString(),
    session_id: null,
    expected_frames: expected,
    frames,
    source: "manifest",
    layout: "unit",
  };
  writeJsonAtomic(path.join(root, "live", "derive", "mux_validated.json"), payload);
  return payload;
}
