/**
 * Main table jsonl/parquet writer using frame-map global indices (Phase0 §5 + §4.3).
 */

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { ensureDir, readJsonl, writeJsonlAtomic } from "./io.mjs";
import { findSegmentEntry } from "./frame-map.mjs";
import { readSegmentManifest, resolveFrameIndex } from "../ingest/frame-index.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ALIGN_SCRIPT = path.join(__dirname, "imu", "align-main.py");
const SYNC_SCRIPT = path.join(__dirname, "..", "scripts", "sync-stream-parquet.py");

export function mainJsonlPath(root) {
  return path.join(root, "data", "chunk-000", "file-000.jsonl");
}

export function mainParquetPath(root) {
  return path.join(root, "data", "chunk-000", "file-000.parquet");
}

function resolvePython() {
  for (const bin of ["python3", "python"]) {
    const res = spawnSync(bin, ["--version"], { encoding: "utf8" });
    if (res.status === 0) return bin;
  }
  return "python3";
}

function readSegmentRows(extractDir) {
  const rowsPath = path.join(extractDir, "rows.jsonl");
  if (!fs.existsSync(rowsPath)) return [];
  return readJsonl(rowsPath);
}

export function remapSegmentRows(frameMap, sessionId, segmentId, extractDir) {
  const entry = findSegmentEntry(frameMap, sessionId, segmentId);
  if (!entry) {
    throw new Error(`frame_segments entry missing for ${sessionId}/${segmentId}`);
  }
  const rows = readSegmentRows(extractDir);
  const manifest = readSegmentManifest(extractDir);
  return rows.map((row) => {
    const globalIndex = resolveFrameIndex(row, manifest, entry.global_start);
    if (globalIndex < 0) {
      throw new Error(`invalid frame_index in ${segmentId}`);
    }
    return {
      ...row,
      frame_index: globalIndex,
      episode_index: frameMap.episode_index ?? 0,
    };
  });
}

export function buildMainTableRows(frameMap, segmentExtracts) {
  const allRows = [];
  for (const { sessionId, segmentId, extractDir } of segmentExtracts) {
    const rows = remapSegmentRows(frameMap, sessionId, segmentId, extractDir);
    allRows.push(...rows);
  }
  allRows.sort((a, b) => Number(a.frame_index) - Number(b.frame_index));
  return allRows;
}

export function writeMainJsonl(root, rows) {
  const jsonlPath = mainJsonlPath(root);
  ensureDir(path.dirname(jsonlPath));
  writeJsonlAtomic(jsonlPath, rows);
  return jsonlPath;
}

export function alignMainTableImu(root, jsonlPath = null) {
  const py = resolvePython();
  const target = jsonlPath || mainJsonlPath(root);
  const res = spawnSync(
    py,
    [ALIGN_SCRIPT, root, "--jsonl", target],
    { encoding: "utf8" },
  );
  const raw = String(res.stdout || "").trim();
  if (res.status !== 0) {
    throw new Error(String(res.stderr || raw || "align-main failed").slice(0, 500));
  }
  try {
    return JSON.parse(raw.split("\n").filter(Boolean).pop() || "{}");
  } catch {
    return { ok: true };
  }
}

export function syncMainParquet(root, stationId) {
  const py = resolvePython();
  const res = spawnSync(py, [SYNC_SCRIPT, root], {
    encoding: "utf8",
  });
  if (res.status !== 0) {
    throw new Error(String(res.stderr || res.stdout || "sync-stream-parquet failed").slice(0, 500));
  }
  return { ok: true, path: mainParquetPath(root) };
}

export function updateInfoFrameMetrics(root, frameMap) {
  const infoPath = path.join(root, "meta", "info.json");
  let info = {};
  if (fs.existsSync(infoPath)) {
    info = JSON.parse(fs.readFileSync(infoPath, "utf8"));
  }
  info.total_frames = frameMap.length;
  info.frame_index_min = frameMap.frame_index_min;
  info.frame_index_max = frameMap.frame_index_max;
  info.ingest_row_count = frameMap.length;
  ensureDir(path.dirname(infoPath));
  const tmp = `${infoPath}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(info, null, 2)}\n`);
  fs.renameSync(tmp, infoPath);
}

export function writeMainTableFromSegments(root, stationId, frameMap, segmentExtracts, options = {}) {
  const { append = false } = options;
  const newRows = buildMainTableRows(frameMap, segmentExtracts);
  let rows = newRows;
  if (append) {
    const jsonlPath = mainJsonlPath(root);
    const existing = fs.existsSync(jsonlPath) ? readJsonl(jsonlPath) : [];
    const byIndex = new Map(existing.map((row) => [Number(row.frame_index), row]));
    for (const row of newRows) {
      byIndex.set(Number(row.frame_index), row);
    }
    rows = Array.from(byIndex.values()).sort(
      (a, b) => Number(a.frame_index) - Number(b.frame_index),
    );
  }
  const jsonlPath = writeMainJsonl(root, rows);
  const alignReport = alignMainTableImu(root, jsonlPath);
  updateInfoFrameMetrics(root, frameMap);
  syncMainParquet(root, stationId);
  return {
    rows: rows.length,
    rowsAdded: newRows.length,
    jsonlPath,
    alignReport,
  };
}
