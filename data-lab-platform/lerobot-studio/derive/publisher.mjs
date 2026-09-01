/**
 * P2 publisher: materialize L2 LeRobot v3 view from immutable L1 units (hardlink-first).
 */

import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { ensureDir, readJson, writeJsonAtomic } from "./io.mjs";
import { appendJournalEvent, rebuildManifestFromDisk, writeMuxValidatedFromManifest } from "./manifest.mjs";
import { episodeChunkPaths, unitDir } from "./unit-paths.mjs";
import { videoKeysForStation } from "../ingest/staging-materialize.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SYNC_EPISODES_SCRIPT = path.join(__dirname, "../scripts/sync-stream-parquet.py");

/** Rewrite meta/episodes parquet for unit layout (per-episode file_index). */
export function syncUnitEpisodesMeta(root) {
  execFileSync("python3", [SYNC_EPISODES_SCRIPT, "--meta-only", root], { stdio: "pipe" });
}

export function hardlinkOrCopy(src, dest) {
  ensureDir(path.dirname(dest));
  if (fs.existsSync(dest)) fs.rmSync(dest, { force: true });
  try {
    fs.linkSync(src, dest);
    return "hardlink";
  } catch {
    fs.copyFileSync(src, dest);
    return "copy";
  }
}

function unitArtifactPath(unitRoot, rel) {
  return path.join(unitRoot, rel);
}

export function publishUnitEpisode(root, stationId, sessionId, episodeIndex, episodeMeta = null) {
  const unitRoot = unitDir(root, sessionId);
  const unitJson = readJson(path.join(unitRoot, "unit.json"), null);
  if (!unitJson) {
    throw new Error(`unit.json missing for ${sessionId}`);
  }
  const paths = episodeChunkPaths(episodeIndex);
  const dataSrc = unitArtifactPath(unitRoot, "data.parquet");
  const jsonlSrc = unitArtifactPath(unitRoot, "data.jsonl");
  const dataDest = path.join(root, paths.dataRel);
  const jsonlDest = path.join(root, paths.jsonlRel);
  hardlinkOrCopy(dataSrc, dataDest);
  if (fs.existsSync(jsonlSrc)) {
    hardlinkOrCopy(jsonlSrc, jsonlDest);
  }
  for (const videoKey of videoKeysForStation(stationId)) {
    const rel = `videos/${videoKey}.mp4`;
    const src = unitArtifactPath(unitRoot, rel);
    const dest = path.join(root, paths.videoRel(videoKey));
    if (!fs.existsSync(src)) {
      throw new Error(`unit video missing: ${rel}`);
    }
    hardlinkOrCopy(src, dest);
  }
  appendJournalEvent(root, {
    event: "published",
    session_id: sessionId,
    episode_index: episodeIndex,
    from_index: episodeMeta?.from_index ?? 0,
    to_index: episodeMeta?.to_index ?? Number(unitJson.frames || 0) - 1,
  });
  return { episodeIndex, sessionId, paths };
}

export function updateInfoFromManifest(root, manifest) {
  const infoPath = path.join(root, "meta", "info.json");
  const info = readJson(infoPath, {});
  const episodes = manifest?.episodes || [];
  const totalFrames = Number(manifest?.total_frames || 0);
  info.total_episodes = Math.max(1, episodes.length);
  info.total_frames = totalFrames;
  info.ingest_row_count = totalFrames;
  if (totalFrames > 0) {
    info.frame_index_min = 0;
    info.frame_index_max = totalFrames - 1;
  }
  if (!info.splits) info.splits = { train: `0:${Math.max(1, episodes.length)}` };
  else info.splits.train = `0:${Math.max(1, episodes.length)}`;
  info.episode_provenance = episodes.map((ep) => ({
    episode_index: ep.episode_index,
    session_id: ep.session_id,
    source_session_id: ep.source_session_id || ep.session_id,
    video_codec: ep.video_codec || "jpeg",
    pose_ready: Boolean(ep.pose_ready),
  }));
  info.asset_layers = {
    raw_mcap: "sensors_only",
    stream_lerobot: "preview_no_real_pose",
    corpus: "convert_hamer_pose",
  };
  writeJsonAtomic(infoPath, info);
  return info;
}

/** Refresh Viewer HTTP snapshot + chunks manifest after unit L2 publish. */
export function refreshViewerFromManifest(root, stationId, manifest) {
  const infoPath = path.join(root, "meta", "info.json");
  const info = readJson(infoPath, {});
  writeJsonAtomic(path.join(root, "meta", "info.viewer.json"), info);
  const episodes = manifest?.episodes || [];
  const totalFrames = Number(manifest?.total_frames || info.total_frames || 0);
  const now = new Date().toISOString();
  const publish = {
    "meta/info.json": { status: "finished", frames: totalFrames, updatedAt: now },
    "meta/episodes/chunk-000/file-000.parquet": {
      status: "finished",
      frames: episodes.length,
      updatedAt: now,
    },
  };
  for (const ep of episodes) {
    const paths = episodeChunkPaths(ep.episode_index);
    const frames = Number(ep.frames || 0);
    publish[paths.dataRel] = { status: "finished", frames, updatedAt: now };
    publish[paths.jsonlRel] = { status: "finished", frames, updatedAt: now };
    for (const videoKey of videoKeysForStation(stationId)) {
      publish[paths.videoRel(videoKey)] = { status: "finished", frames, updatedAt: now };
    }
  }
  writeJsonAtomic(path.join(root, "live", "chunks.json"), {
    revision: Date.now(),
    viewerTotalFrames: totalFrames,
    layout: "unit",
    publish,
  });
  const markerPath = path.join(root, "live", "parquet_sync.json");
  const marker = readJson(markerPath, {});
  marker.viewer_scaffold = false;
  marker.unit_layout = true;
  marker.total_frames = totalFrames;
  marker.episodes_count = episodes.length;
  writeJsonAtomic(markerPath, marker);
  try {
    syncUnitEpisodesMeta(root);
    writeJsonAtomic(path.join(root, "meta", "info.viewer.json"), readJson(infoPath, info));
  } catch (err) {
    console.warn("[publisher] syncUnitEpisodesMeta failed:", err?.message || err);
  }
  return { totalFrames, episodes: episodes.length };
}

/** Rebuild entire L2 view from derived units + manifest ordering. */
export function rebuildView(root, stationId) {
  const manifest = rebuildManifestFromDisk(root, stationId);
  for (const ep of manifest.episodes) {
    publishUnitEpisode(root, stationId, ep.session_id, ep.episode_index, ep);
  }
  updateInfoFromManifest(root, manifest);
  writeMuxValidatedFromManifest(root, stationId, manifest);
  refreshViewerFromManifest(root, stationId, manifest);
  return manifest;
}

export function publishSessionIfNeeded(root, stationId, sessionId) {
  const manifest = rebuildManifestFromDisk(root, stationId);
  const ep = (manifest.episodes || []).find((e) => e.session_id === sessionId);
  if (!ep) {
    throw new Error(`session ${sessionId} missing from manifest after unit derive`);
  }
  publishUnitEpisode(root, stationId, sessionId, ep.episode_index, ep);
  updateInfoFromManifest(root, manifest);
  return { published: true, episodeIndex: ep.episode_index, replay: false };
}
