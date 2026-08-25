/**
 * Materialize staging JPEGs from extracted segment dir (no purge — Phase5 GC).
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { unpackFrameBin } from "../frame_bin_codec.mjs";
import { frameBinName, readSegmentManifest, resolveFrameIndex, stagingFrameName } from "./frame-index.mjs";
import { ensureDir } from "./io.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const TOPOLOGY_PATH = path.join(__dirname, "..", "config", "station-topology.json");

function loadTopology() {
  return JSON.parse(fs.readFileSync(TOPOLOGY_PATH, "utf8"));
}

export function videoKeysForStation(stationId) {
  const topo = loadTopology();
  const topologyId = topo.stations?.[stationId]?.topology_id || "ego-standard";
  const block = topo[topologyId] || topo["ego-standard"];
  return block?.video_keys || [];
}

function stagingDir(root, videoKey) {
  return path.join(root, "_staging", videoKey.replace(/\./g, "_"));
}

function readRows(extractDir) {
  const rowsPath = path.join(extractDir, "rows.jsonl");
  if (!fs.existsSync(rowsPath)) return [];
  return fs
    .readFileSync(rowsPath, "utf8")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

export function materializeStagingFromExtractDir(root, extractDir, { stationId }) {
  const manifest = readSegmentManifest(extractDir);
  if (!manifest) {
    throw new Error("missing manifest.json in extract dir");
  }
  const videoKeys = videoKeysForStation(stationId);
  for (const key of videoKeys) {
    ensureDir(stagingDir(root, key));
  }

  const rows = readRows(extractDir);
  let framesWritten = 0;
  for (const row of rows) {
    const frameIndex = resolveFrameIndex(row, manifest, 0);
    if (frameIndex < 0) continue;
    const binPath = path.join(extractDir, "frames", frameBinName(frameIndex));
    if (!fs.existsSync(binPath)) {
      throw new Error(`missing frame bin for index ${frameIndex}`);
    }
    const cameraJpegs = unpackFrameBin(fs.readFileSync(binPath));
    for (const videoKey of videoKeys) {
      const jpeg = cameraJpegs[videoKey];
      if (!jpeg) continue;
      const out = path.join(stagingDir(root, videoKey), stagingFrameName(frameIndex));
      fs.writeFileSync(out, jpeg);
    }
    framesWritten += 1;
  }

  return {
    frameCount: Number(manifest.frame_count || framesWritten),
    framesWritten,
    manifest,
  };
}
