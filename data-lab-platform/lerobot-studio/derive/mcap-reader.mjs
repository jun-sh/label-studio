/**
 * MCAP archive reader for unit derive (sourceFormat=mcap).
 */

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { ensureDir } from "./io.mjs";
import { readJsonl } from "./io.mjs";
import { readSegmentManifest, stagingFrameName } from "../ingest/frame-index.mjs";
import { videoKeysForStation } from "../ingest/staging-materialize.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MATERIALIZE_SCRIPT = path.join(__dirname, "mcap-materialize.py");

const CAMERA_KEY_TO_VIDEO = {
  front_left: "observation.images.camera_front_left",
  front_right: "observation.images.camera_front_right",
  rear_left: "observation.images.camera_rear_left",
  rear_right: "observation.images.camera_rear_right",
};

function resolvePython() {
  for (const bin of ["python3", "python"]) {
    const res = spawnSync(bin, ["--version"], { encoding: "utf8" });
    if (res.status === 0) return bin;
  }
  return "python3";
}

function parseJsonStdout(stdout) {
  const raw = String(stdout || "").trim();
  const line = raw.split("\n").filter(Boolean).pop() || "{}";
  return JSON.parse(line);
}

export function summarizeMcapArchive(archivePath) {
  const py = resolvePython();
  const res = spawnSync(py, [MATERIALIZE_SCRIPT, archivePath, "--summary-only"], {
    encoding: "utf8",
  });
  const parsed = parseJsonStdout(res.stdout || res.stderr);
  if (res.status !== 0 && parsed.ok !== false) {
    throw new Error(String(parsed.error || res.stderr || "mcap summary failed").slice(0, 500));
  }
  return parsed;
}

export function materializeMcapExtract(archivePath, extractDir) {
  ensureDir(extractDir);
  const py = resolvePython();
  const res = spawnSync(py, [MATERIALIZE_SCRIPT, archivePath, extractDir], {
    encoding: "utf8",
  });
  const parsed = parseJsonStdout(res.stdout || res.stderr);
  if (res.status !== 0 || !parsed.ok) {
    throw new Error(String(parsed.error || res.stderr || "mcap materialize failed").slice(0, 500));
  }
  return parsed;
}

function unitFramesDir(tmpRoot, videoKey) {
  return path.join(tmpRoot, "frames", videoKey.replace(/\./g, "_"));
}

/**
 * Copy JPEGs from MCAP extract dir into unit mux frame dirs.
 */
export function materializeMcapUnitFrames(tmpRoot, extractDir, stationId) {
  const manifest = readSegmentManifest(extractDir);
  const rows = readJsonl(path.join(extractDir, "rows.jsonl"));
  const videoKeys = videoKeysForStation(stationId);
  let written = 0;

  for (const row of rows) {
    const frameIndex = Number(row.frame_index ?? row.frameIndex ?? -1);
    if (!Number.isInteger(frameIndex) || frameIndex < 0) continue;
    for (const [camKey, videoKey] of Object.entries(CAMERA_KEY_TO_VIDEO)) {
      if (!videoKeys.includes(videoKey)) continue;
      const src = path.join(extractDir, "frames", camKey, `${String(frameIndex).padStart(8, "0")}.jpg`);
      if (!fs.existsSync(src)) continue;
      const outDir = unitFramesDir(tmpRoot, videoKey);
      ensureDir(outDir);
      fs.copyFileSync(src, path.join(outDir, stagingFrameName(frameIndex)));
      written += 1;
    }
  }

  return {
    written,
    frameCount: Number(manifest?.frame_count || rows.length),
  };
}
