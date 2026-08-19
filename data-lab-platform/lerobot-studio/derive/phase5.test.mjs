import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

import { writeFrameMap } from "./frame-map.mjs";
import { writeJsonAtomic, writeJsonlAtomic } from "./io.mjs";
import { mainJsonlPath, mainParquetPath } from "./parquet-writer.mjs";
import {
  runReadyGate,
  checkMainTableContinuity,
  checkMp4FrameCoverage,
  checkMuxValidated,
  checkImuRawParquet,
  checkImuMainAlignment,
} from "./ready-gate.mjs";
import {
  evaluateStagingGc,
  runLifecycleGc,
  purgeAllStagingJpgs,
  FAILED_STAGING_RETENTION_MS,
} from "./lifecycle-gc.mjs";
import {
  markSessionReady,
  markSessionFailed,
  markSessionDeriving,
  readSessionMarker,
  writeSessionMarker,
  SESSION_MARKERS,
} from "../session-markers.mjs";
import { videoKeysForStation } from "../ingest/staging-materialize.mjs";

const STATION_ID = "ego-001";

function tmpRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ego-phase5-test-"));
}

function stagingDir(root, videoKey) {
  return path.join(root, "_staging", videoKey.replace(/\./g, "_"));
}

function writeStagingJpg(root, stationId, globalIndex = 0) {
  for (const key of videoKeysForStation(stationId)) {
    const dir = stagingDir(root, key);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(
      path.join(dir, `frame_${String(globalIndex).padStart(6, "0")}.jpg`),
      Buffer.from([0xff, 0xd8, 0xff, 0xd9]),
    );
  }
}

function writeFrameMapState(root, length) {
  const frameMap = {
    episode_index: 0,
    length,
    frame_index_min: 0,
    frame_index_max: length > 0 ? length - 1 : -1,
    sessions: [{ session_id: "sess_a", uploaded_at: "2026-01-01T00:00:00.000Z", segment_count: 1 }],
    frame_segments: [
      {
        session_id: "sess_a",
        segment_id: "seg_000001",
        local_frame_min: 0,
        local_frame_max: length > 0 ? length - 1 : -1,
        global_start: 0,
        global_end: length > 0 ? length - 1 : -1,
        frame_count: length,
      },
    ],
    mapping_version: 1,
    updated_at: new Date().toISOString(),
  };
  writeFrameMap(root, frameMap);
  return frameMap;
}

function writeMainRows(root, count) {
  const rows = [];
  for (let i = 0; i < count; i += 1) {
    rows.push({
      frame_index: i,
      timestamp_ns: i * 33_000_000,
      task: "t",
      "observation.imu_accel": [1, 0, 0],
      "observation.imu_gyro": [0, 1, 0],
      "observation.imu_timestamp": [i * 0.033],
    });
  }
  writeJsonlAtomic(mainJsonlPath(root), rows);
}

function writeInfoJson(root) {
  writeJsonAtomic(path.join(root, "meta", "info.json"), {
    codebase_version: "v3.0",
    features: {
      "observation.imu_accel": { dtype: "float32", shape: [3] },
      "observation.imu_gyro": { dtype: "float32", shape: [3] },
      "observation.imu_timestamp": { dtype: "float64", shape: [1] },
      "observation.images.camera_front_left": { dtype: "video" },
    },
    sensor_raw: { imu: { path: "sensor_raw/imu/chunk-000/file-000.parquet", rate_hz: 200 } },
  });
}

function writeImuParquet(root) {
  const imuJsonl = path.join(root, "imu_raw.jsonl");
  fs.writeFileSync(
    imuJsonl,
    `${JSON.stringify({ ts_ns: 0, sensor: "accel", x: 1, y: 0, z: 0 })}\n${JSON.stringify({ ts_ns: 0, sensor: "gyro", x: 0, y: 1, z: 0 })}\n`,
  );
  const script = path.join(path.dirname(new URL(import.meta.url).pathname), "imu", "ingest-raw.py");
  const res = spawnSync(
    "python3",
    [script, root, "--imu-jsonl", imuJsonl, "--segment-id", "seg_000001", "--session-id", "sess_a", "--replace"],
    { encoding: "utf8" },
  );
  assert.equal(res.status, 0, res.stderr);
}

function writeMuxValidated(root, sessionId, expected) {
  writeJsonAtomic(path.join(root, "live", "derive", "mux_validated.json"), {
    ok: true,
    session_id: sessionId,
    expected_frames: expected,
    frames: Object.fromEntries(
      videoKeysForStation(STATION_ID).map((k) => [k, expected]),
    ),
    source: "frame_map",
  });
}

function writeMp4s(root, stationId, frameCount) {
  if (spawnSync("ffmpeg", ["-version"]).status !== 0) return false;
  for (const key of videoKeysForStation(stationId)) {
    const dir = stagingDir(root, key);
    fs.mkdirSync(dir, { recursive: true });
    for (let i = 0; i < frameCount; i += 1) {
      const jpg = path.join(dir, `frame_${String(i).padStart(6, "0")}.jpg`);
      fs.writeFileSync(jpg, Buffer.from([0xff, 0xd8, 0xff, 0xd9]));
    }
    const mp4 = path.join(root, "videos", key, "chunk-000", "file-000.mp4");
    fs.mkdirSync(path.dirname(mp4), { recursive: true });
    const res = spawnSync(
      "ffmpeg",
      [
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-framerate",
        "30",
        "-i",
        path.join(dir, "frame_%06d.jpg"),
        "-frames:v",
        String(frameCount),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        mp4,
      ],
      { encoding: "utf8" },
    );
    if (res.status !== 0) return false;
  }
  return true;
}

function writeFullReadyState(root, sessionId, frameCount = 2) {
  writeFrameMapState(root, frameCount);
  writeMainRows(root, frameCount);
  writeInfoJson(root);
  writeImuParquet(root);
  writeMuxValidated(root, sessionId, frameCount);
  writeStagingJpg(root, STATION_ID, 0);
}

describe("ready-gate G1 main table continuity", () => {
  it("passes for continuous indices", () => {
    const root = tmpRoot();
    writeFrameMapState(root, 3);
    writeMainRows(root, 3);
    assert.equal(checkMainTableContinuity(root).ok, true);
  });

  it("fails on index gap with PARQUET_INDEX_GAP", () => {
    const root = tmpRoot();
    writeFrameMapState(root, 3);
    writeJsonlAtomic(mainJsonlPath(root), [
      { frame_index: 0, timestamp_ns: 1 },
      { frame_index: 2, timestamp_ns: 2 },
    ]);
    const result = checkMainTableContinuity(root);
    assert.equal(result.ok, false);
    assert.equal(result.reason.code, "PARQUET_INDEX_GAP");
  });

  it("validates multi-session merged frame map continuity", () => {
    const root = tmpRoot();
    writeFrameMap(root, {
      episode_index: 0,
      length: 5,
      frame_index_min: 0,
      frame_index_max: 4,
      sessions: [
        { session_id: "sess_a", uploaded_at: "2026-01-01T00:00:00.000Z", segment_count: 2 },
        { session_id: "sess_b", uploaded_at: "2026-01-02T00:00:00.000Z", segment_count: 1 },
      ],
      frame_segments: [
        { session_id: "sess_a", segment_id: "seg_001", local_frame_min: 0, local_frame_max: 1, global_start: 0, global_end: 1, frame_count: 2 },
        { session_id: "sess_a", segment_id: "seg_002", local_frame_min: 0, local_frame_max: 1, global_start: 2, global_end: 3, frame_count: 2 },
        { session_id: "sess_b", segment_id: "seg_001", local_frame_min: 0, local_frame_max: 0, global_start: 4, global_end: 4, frame_count: 1 },
      ],
      mapping_version: 1,
    });
    writeMainRows(root, 5);
    const gate = runReadyGate(root, STATION_ID, "sess_b");
    assert.equal(checkMainTableContinuity(root).ok, true);
    assert.notEqual(gate.failedCheckId, "G1");
  });
});

describe("ready-gate G2-G6 individual checks", () => {
  it("G2 fails MUX_FRAME_MISMATCH when mp4 frames insufficient", () => {
    const root = tmpRoot();
    writeFrameMapState(root, 2);
    writeMainRows(root, 2);
    const result = checkMp4FrameCoverage(root, STATION_ID);
    assert.equal(result.ok, false);
    assert.equal(result.reason.code, "MUX_FRAME_MISMATCH");
  });

  it("G3 fails when mux_validated missing", () => {
    const root = tmpRoot();
    const result = checkMuxValidated(root, "sess_a");
    assert.equal(result.ok, false);
    assert.equal(result.reason.code, "MUX_VALIDATION_FAILED");
  });

  it("G5 fails IMU_RAW_MISSING without sensor_raw parquet", () => {
    const root = tmpRoot();
    const result = checkImuRawParquet(root);
    assert.equal(result.ok, false);
    assert.equal(result.reason.code, "IMU_RAW_MISSING");
  });

  it("G6 fails IMU_ALIGN_NULL when null ratio exceeds 1%", () => {
    const root = tmpRoot();
    writeJsonlAtomic(mainJsonlPath(root), [{ frame_index: 0, "observation.imu_accel": null }]);
    const result = checkImuMainAlignment(root);
    assert.equal(result.ok, false);
    assert.equal(result.reason.code, "IMU_ALIGN_NULL");
    assert.match(result.reason.message, /null ratio/);
  });

  it("G6 passes when null ratio is below 1%", () => {
    const root = tmpRoot();
    const rows = [];
    for (let i = 0; i < 100; i += 1) {
      rows.push({
        frame_index: i,
        "observation.imu_accel": [1, 0, 0],
        "observation.imu_gyro": i === 0 ? null : [0, 1, 0],
        "observation.imu_timestamp": [i * 0.033],
      });
    }
    writeJsonlAtomic(mainJsonlPath(root), rows);
    const result = checkImuMainAlignment(root);
    assert.equal(result.ok, true);
  });
});

describe("ready-gate runReadyGate", () => {
  it("passes all G1-G6 and session READY marker payload includes ready_gate", () => {
    const root = tmpRoot();
    const sessionId = "sess_ready";
    writeFullReadyState(root, sessionId, 2);
    if (!writeMp4s(root, STATION_ID, 2)) {
      return;
    }
    const gate = runReadyGate(root, STATION_ID, sessionId);
    assert.equal(gate.ok, true);
    assert.equal(gate.checksPassed, 6);
    markSessionReady(root, sessionId, { total: 2, gate });
    const marker = readSessionMarker(root, sessionId, SESSION_MARKERS.READY);
    assert.equal(marker.phase, "READY");
    assert.equal(marker.ready_gate.checks_passed, 6);
    assert.equal(marker.mp4Ok, true);
  });

  it("failure produces structured reason.code on session.FAILED", () => {
    const root = tmpRoot();
    const sessionId = "sess_fail";
    writeFrameMapState(root, 2);
    writeMainRows(root, 1);
    const gate = runReadyGate(root, STATION_ID, sessionId);
    assert.equal(gate.ok, false);
    assert.equal(gate.reason.code, "PARQUET_INDEX_GAP");
    markSessionFailed(root, sessionId, gate.reason);
    const marker = readSessionMarker(root, sessionId, SESSION_MARKERS.FAILED);
    assert.equal(marker.reason.code, "PARQUET_INDEX_GAP");
    assert.equal(marker.phase, "FAILED");
    assert.equal(marker.mp4Ok, false);
  });

  it("returns GATE_INTERNAL_ERROR when jsonl contains invalid NaN literals", () => {
    const root = tmpRoot();
    const sessionId = "sess_gate_crash";
    writeFrameMapState(root, 1);
    writeInfoJson(root);
    writeImuParquet(root);
    writeMuxValidated(root, sessionId, 1);
    const jsonlPath = mainJsonlPath(root);
    fs.mkdirSync(path.dirname(jsonlPath), { recursive: true });
    fs.writeFileSync(
      jsonlPath,
      '{"frame_index":0,"observation.imu_gyro":[NaN,NaN,NaN],"observation.imu_accel":[1,0,0],"observation.imu_timestamp":[0]}\n',
    );
    const gate = runReadyGate(root, STATION_ID, sessionId);
    assert.equal(gate.ok, false);
    assert.equal(gate.reason.code, "GATE_INTERNAL_ERROR");
    assert.equal(gate.failedCheckId, "GATE");
  });
});

describe("lifecycle-gc staging purge policy", () => {
  it("purges staging after READY when artifacts present", () => {
    const root = tmpRoot();
    const sessionId = "sess_gc_ready";
    writeFullReadyState(root, sessionId, 1);
    writeMp4s(root, STATION_ID, 1);
    writeStagingJpg(root, STATION_ID, 0);
    markSessionReady(root, sessionId, { total: 1, gate: { checksPassed: 6, checksTotal: 6 } });
    const gc = runLifecycleGc(root, STATION_ID, sessionId);
    assert.equal(gc.purged, true);
    assert.ok(gc.removed > 0);
  });

  it("does not purge staging while session is DERIVING", () => {
    const root = tmpRoot();
    const sessionId = "sess_gc_deriving";
    writeStagingJpg(root, STATION_ID, 0);
    markSessionDeriving(root, sessionId);
    const gc = runLifecycleGc(root, STATION_ID, sessionId);
    assert.equal(gc.purged, false);
    assert.equal(gc.reason, "session_deriving");
  });

  it("retains staging for FAILED within 24h", () => {
    const root = tmpRoot();
    const sessionId = "sess_gc_failed_recent";
    writeStagingJpg(root, STATION_ID, 0);
    markSessionFailed(root, sessionId, { code: "PARQUET_INDEX_GAP", message: "gap", category: "derive" });
    const gate = evaluateStagingGc(root, sessionId, STATION_ID);
    assert.equal(gate.allowed, false);
    assert.equal(gate.reason, "failed_retention_24h");
  });

  it("purges staging for FAILED after 24h retention", () => {
    const root = tmpRoot();
    const sessionId = "sess_gc_failed_old";
    writeStagingJpg(root, STATION_ID, 0);
    writeSessionMarker(root, sessionId, SESSION_MARKERS.FAILED, {
      phase: "FAILED",
      at: new Date(Date.now() - FAILED_STAGING_RETENTION_MS - 1000).toISOString(),
      reason: { code: "PARQUET_INDEX_GAP", message: "gap", category: "derive" },
    });
    const gc = runLifecycleGc(root, STATION_ID, sessionId);
    assert.equal(gc.purged, true);
  });
});
