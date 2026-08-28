/**
 * READY gate checks G1–G6 (Phase0 §7).
 * MCAP sourceFormat sessions use the same thresholds; unit layout runs G1–G3 inline
 * and defers G4–G6 to publish-time checks on the LeRobot layout tree.
 */

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

import { readJson, readJsonl } from "./io.mjs";
import { readFrameMap } from "./frame-map.mjs";
import { mainJsonlPath, mainParquetPath } from "./parquet-writer.mjs";
import { videoKeysForStation } from "../ingest/staging-materialize.mjs";
import { probeMp4FrameCount } from "../mux-exec.mjs";
import { DEFAULT_FPS } from "./station-context.mjs";

const SENSOR_RAW_IMU = "sensor_raw/imu/chunk-000/file-000.parquet";
const IMU_FEATURE_KEYS = [
  "observation.imu_accel",
  "observation.imu_gyro",
  "observation.imu_timestamp",
];
const VIDEO_KEY_FALLBACK = [
  "observation.images.camera_front_left",
  "observation.images.camera_front_right",
  "observation.images.camera_rear_left",
  "observation.images.camera_rear_right",
];

export const READY_GATE_CHECKS_TOTAL = 6;

function fail(checkId, code, message, category = "derive") {
  return {
    ok: false,
    checkId,
    reason: { code, message: String(message).slice(0, 500), category },
  };
}

function pass(checkId) {
  return { ok: true, checkId };
}

function videoOutPath(root, videoKey) {
  return path.join(root, "videos", videoKey, "chunk-000", "file-000.mp4");
}

export function readMainTableFrameIndices(root) {
  const jsonlPath = mainJsonlPath(root);
  if (fs.existsSync(jsonlPath)) {
    return readJsonl(jsonlPath).map((row) => Number(row.frame_index));
  }
  const parquetPath = mainParquetPath(root);
  if (!fs.existsSync(parquetPath)) return [];
  const res = spawnSync(
    "python3",
    [
      "-c",
      `import pyarrow.parquet as pq; t=pq.read_table(${JSON.stringify(parquetPath)}); print(",".join(str(int(x)) for x in t.column("frame_index").to_pylist()))`,
    ],
    { encoding: "utf8" },
  );
  if (res.status !== 0 || !String(res.stdout).trim()) return [];
  return String(res.stdout)
    .trim()
    .split(",")
    .filter(Boolean)
    .map((n) => Number(n));
}

/** G1: main table frame_index continuous (Phase0 §7.2). */
export function checkMainTableContinuity(root) {
  const map = readFrameMap(root);
  if (!map || !map.length) {
    return fail("G1", "PARQUET_INDEX_GAP", "frame_map missing or empty", "derive");
  }
  const rows = readMainTableFrameIndices(root);
  const expected = map.length;
  const unique = new Set(rows);
  if (unique.size !== rows.length) {
    return fail("G1", "PARQUET_INDEX_GAP", "duplicate frame_index", "derive");
  }
  if (rows.length !== expected) {
    return fail("G1", "PARQUET_INDEX_GAP", `row count ${rows.length} != length ${expected}`, "derive");
  }
  for (let i = map.frame_index_min; i <= map.frame_index_max; i += 1) {
    if (!unique.has(i)) {
      return fail("G1", "PARQUET_INDEX_GAP", `missing index ${i}`, "derive");
    }
  }
  return pass("G1");
}

/** G2: four MP4 streams cover main table row count (Phase0 §7.3). */
export function checkMp4FrameCoverage(root, stationId) {
  const map = readFrameMap(root);
  const mainTableRows = map?.length || readMainTableFrameIndices(root).length;
  if (mainTableRows <= 0) {
    return fail("G2", "MUX_FRAME_MISMATCH", "main table empty", "mux");
  }
  const keys = stationId ? videoKeysForStation(stationId) : VIDEO_KEY_FALLBACK;
  for (const key of keys) {
    const mp4 = videoOutPath(root, key);
    const mp4Frames = fs.existsSync(mp4) ? probeMp4FrameCount(mp4, { defaultFps: DEFAULT_FPS }) : 0;
    if (mp4Frames < mainTableRows) {
      return fail(
        "G2",
        "MUX_FRAME_MISMATCH",
        `${key}: ${mp4Frames} < ${mainTableRows}`,
        "mux",
      );
    }
  }
  return pass("G2");
}

/** G3: mux_validated.json exists and ok=true. */
export function checkMuxValidated(root, sessionId) {
  const muxVal = readJson(path.join(root, "live", "derive", "mux_validated.json"), null);
  if (!muxVal) {
    return fail("G3", "MUX_VALIDATION_FAILED", "mux_validated.json missing", "mux");
  }
  if (muxVal.session_id && muxVal.session_id !== sessionId) {
    return fail("G3", "MUX_VALIDATION_FAILED", `session mismatch ${muxVal.session_id}`, "mux");
  }
  if (muxVal.ok !== true) {
    return fail("G3", "MUX_VALIDATION_FAILED", "mux_validated ok=false", "mux");
  }
  return pass("G3");
}

/** G4: LeRobot schema / info.json features valid. */
export function checkLeRobotSchema(root) {
  const infoPath = path.join(root, "meta", "info.json");
  if (!fs.existsSync(infoPath)) {
    return fail("G4", "SCHEMA_INVALID", "meta/info.json missing", "schema");
  }
  const info = readJson(infoPath, {});
  if (!info.codebase_version) {
    return fail("G4", "SCHEMA_INVALID", "codebase_version missing", "schema");
  }
  const features = info.features || {};
  for (const key of IMU_FEATURE_KEYS) {
    if (!features[key]) {
      return fail("G4", "SCHEMA_INVALID", `missing feature ${key}`, "schema");
    }
  }
  const hasVideo = Object.values(features).some((spec) => spec?.dtype === "video");
  if (!hasVideo) {
    return fail("G4", "SCHEMA_INVALID", "no video features registered", "schema");
  }
  const dataOk = fs.existsSync(mainParquetPath(root)) || fs.existsSync(mainJsonlPath(root));
  if (!dataOk) {
    return fail("G4", "SCHEMA_INVALID", "main data parquet/jsonl missing", "schema");
  }
  if (!info.sensor_raw?.imu) {
    return fail("G4", "SCHEMA_INVALID", "sensor_raw.imu missing in info.json", "schema");
  }
  return pass("G4");
}

/** G5: sensor_raw IMU parquet exists and non-empty. */
export function checkImuRawParquet(root) {
  const imuPath = path.join(root, SENSOR_RAW_IMU);
  if (!fs.existsSync(imuPath)) {
    return fail("G5", "IMU_RAW_MISSING", `${SENSOR_RAW_IMU} missing`, "imu");
  }
  const res = spawnSync(
    "python3",
    [
      "-c",
      `import pyarrow.parquet as pq; print(pq.read_metadata(${JSON.stringify(imuPath)}).num_rows)`,
    ],
    { encoding: "utf8" },
  );
  if (res.status !== 0) {
    return fail("G5", "IMU_RAW_MISSING", "cannot read imu parquet", "imu");
  }
  const rows = Number(String(res.stdout).trim());
  if (!Number.isFinite(rows) || rows <= 0) {
    return fail("G5", "IMU_RAW_MISSING", "imu parquet empty", "imu");
  }
  return pass("G5");
}

function isNullishImuValue(value) {
  if (value === null || value === undefined) return true;
  if (Array.isArray(value)) {
    return value.some((v) => v === null || v === undefined || (typeof v === "number" && Number.isNaN(v)));
  }
  return typeof value === "number" && Number.isNaN(value);
}

/** G6: main table IMU aligned; null/missing ratio < 1%. */
export function checkImuMainAlignment(root) {
  const jsonlPath = mainJsonlPath(root);
  if (!fs.existsSync(jsonlPath)) {
    return fail("G6", "IMU_ALIGN_NULL", "main jsonl missing", "imu");
  }
  const rows = readJsonl(jsonlPath);
  if (!rows.length) {
    return fail("G6", "IMU_ALIGN_NULL", "main jsonl empty", "imu");
  }
  let missingCount = 0;
  for (const row of rows) {
    for (const key of ["observation.imu_accel", "observation.imu_gyro"]) {
      const val = row[key];
      if (val === null || val === undefined) {
        missingCount += 1;
        continue;
      }
      if (Array.isArray(val) && val.some((v) => isNullishImuValue(v))) {
        missingCount += 1;
      }
    }
    const ts = row["observation.imu_timestamp"];
    if (isNullishImuValue(ts)) {
      missingCount += 1;
    }
  }
  const nullRatio = missingCount / Math.max(1, rows.length * 3);
  if (nullRatio >= 0.01) {
    return fail(
      "G6",
      "IMU_ALIGN_NULL",
      `null ratio ${(nullRatio * 100).toFixed(2)}% >= 1%`,
      "imu",
    );
  }
  return pass("G6");
}

const CHECK_FNS = [
  { id: "G1", run: (root) => checkMainTableContinuity(root) },
  { id: "G2", run: (root, stationId) => checkMp4FrameCoverage(root, stationId) },
  { id: "G3", run: (root, _stationId, sessionId) => checkMuxValidated(root, sessionId) },
  { id: "G4", run: (root) => checkLeRobotSchema(root) },
  { id: "G5", run: (root) => checkImuRawParquet(root) },
  { id: "G6", run: (root) => checkImuMainAlignment(root) },
];

/**
 * Run all READY gate checks. Stops at first failure.
 * Top-level catch ensures GATE_INTERNAL_ERROR → structured failure (no silent deadlock).
 */
function runReadyGateChecks(root, stationId, sessionId) {
  const checks = [];
  for (const { id, run } of CHECK_FNS) {
    const result = run(root, stationId, sessionId);
    checks.push(result);
    if (!result.ok) {
      return {
        ok: false,
        checks,
        checksPassed: checks.filter((c) => c.ok).length,
        checksTotal: READY_GATE_CHECKS_TOTAL,
        reason: result.reason,
        failedCheckId: id,
      };
    }
  }
  return {
    ok: true,
    checks,
    checksPassed: READY_GATE_CHECKS_TOTAL,
    checksTotal: READY_GATE_CHECKS_TOTAL,
    reason: null,
    failedCheckId: null,
  };
}

export function runReadyGate(root, stationId, sessionId) {
  try {
    return runReadyGateChecks(root, stationId, sessionId);
  } catch (err) {
    const message = String(err?.message || err).slice(0, 500);
    const stack = String(err?.stack || err).slice(0, 2000);
    console.error(`[ready-gate] GATE_INTERNAL_ERROR station=${stationId} session=${sessionId} ${message}\n${stack}`);
    return {
      ok: false,
      checks: [],
      checksPassed: 0,
      checksTotal: READY_GATE_CHECKS_TOTAL,
      reason: {
        code: "GATE_INTERNAL_ERROR",
        message,
        category: "derive",
      },
      failedCheckId: "GATE",
      internalError: { stack },
    };
  }
}
