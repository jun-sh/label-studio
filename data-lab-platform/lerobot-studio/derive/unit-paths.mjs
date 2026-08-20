/** P2 derive unit + manifest path helpers. */

import path from "node:path";

export const UNIT_PIPELINE_VERSION = "p2.0";
export const MANIFEST_VERSION = 1;
export const UNIT_JSON_VERSION = 1;

export function derivedRoot(root) {
  return path.join(root, "derived");
}

export function manifestRoot(root) {
  return path.join(root, "manifest");
}

export function journalPath(root) {
  return path.join(manifestRoot(root), "journal.jsonl");
}

export function manifestPath(root) {
  return path.join(manifestRoot(root), "manifest.json");
}

export function unitDir(root, sessionId) {
  return path.join(derivedRoot(root), sessionId);
}

export function unitJsonPath(root, sessionId) {
  return path.join(unitDir(root, sessionId), "unit.json");
}

export function tmpUnitDir(root, attemptId) {
  return path.join(derivedRoot(root), `_tmp.${attemptId}`);
}

export function episodeChunkPaths(episodeIndex) {
  const chunkIndex = Math.floor(episodeIndex / 1000);
  const fileIndex = episodeIndex % 1000;
  return {
    chunkIndex,
    fileIndex,
    dataRel: `data/chunk-${String(chunkIndex).padStart(3, "0")}/file-${String(fileIndex).padStart(3, "0")}.parquet`,
    jsonlRel: `data/chunk-${String(chunkIndex).padStart(3, "0")}/file-${String(fileIndex).padStart(3, "0")}.jsonl`,
    videoRel: (videoKey) =>
      `videos/${videoKey}/chunk-${String(chunkIndex).padStart(3, "0")}/file-${String(fileIndex).padStart(3, "0")}.mp4`,
  };
}
