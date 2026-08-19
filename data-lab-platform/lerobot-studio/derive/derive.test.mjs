import { describe, it, before } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import {
  buildFrameMap,
  buildFrameMapForDerive,
  validateFrameMapContinuity,
  validateFrameMapPrerequisites,
  writeFrameMap,
} from "./frame-map.mjs";
import { transitionSegmentState, SEGMENT_INGEST_STATUS } from "../ingest/segment-state.mjs";
import { writeSessionMarker, SESSION_MARKERS } from "../session-markers.mjs";
import { remapSegmentRows, writeMainJsonl } from "./parquet-writer.mjs";
import { writeMuxValidatedSnapshot, runFourCameraMux } from "./mux-exec.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const STUDIO = path.join(__dirname, "..");

function tmpRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ego-derive-test-"));
}

function writeSegmentState(root, sessionId, segmentId, frameCount) {
  transitionSegmentState(root, sessionId, segmentId, SEGMENT_INGEST_STATUS.DERIVE_PENDING, {
    frame_count: frameCount,
    received_at: new Date().toISOString(),
  });
}

describe("derive frame-map", () => {
  it("builds continuous global indices across two sessions", () => {
    const root = tmpRoot();
    writeSessionMarker(root, "sess_a", SESSION_MARKERS.DONE_UPLOAD, {
      at: "2026-08-18T10:00:00.000Z",
    });
    writeSessionMarker(root, "sess_b", SESSION_MARKERS.DONE_UPLOAD, {
      at: "2026-08-18T12:00:00.000Z",
    });
    writeSegmentState(root, "sess_a", "seg_000001", 2);
    writeSegmentState(root, "sess_a", "seg_000002", 3);
    writeSegmentState(root, "sess_b", "seg_000001", 2);

    const frameMap = buildFrameMap(root);
    const check = validateFrameMapContinuity(frameMap);
    assert.equal(check.ok, true, check.issues.join("; "));
    assert.equal(frameMap.length, 7);
    assert.equal(frameMap.frame_segments[0].global_start, 0);
    assert.equal(frameMap.frame_segments[0].global_end, 1);
    assert.equal(frameMap.frame_segments[1].global_start, 2);
    assert.equal(frameMap.frame_segments[1].global_end, 4);
    assert.equal(frameMap.frame_segments[2].global_start, 5);
    assert.equal(frameMap.frame_segments[2].global_end, 6);
    assert.equal(frameMap.sessions[0].session_id, "sess_a");
    assert.equal(frameMap.sessions[1].session_id, "sess_b");
  });

  it("returns not-ready when DONE_UPLOAD exists but segment not DERIVE_PENDING", () => {
    const root = tmpRoot();
    writeSessionMarker(root, "sess_a", SESSION_MARKERS.DONE_UPLOAD, {
      at: "2026-08-18T10:00:00.000Z",
    });
    const rawDir = path.join(root, "raw", "segments", "sess_a");
    fs.mkdirSync(rawDir, { recursive: true });
    fs.writeFileSync(path.join(rawDir, "seg_000001.tar.zst"), Buffer.alloc(8));
    const prereq = validateFrameMapPrerequisites(root);
    assert.equal(prereq.ok, false);
    assert.equal(prereq.code, "FRAME_MAP_NOT_READY");
  });

  it("builds merged frame map once all segments are DERIVE_PENDING", () => {
    const root = tmpRoot();
    writeSessionMarker(root, "sess_a", SESSION_MARKERS.DONE_UPLOAD, {
      at: "2026-08-18T10:00:00.000Z",
    });
    writeSessionMarker(root, "sess_b", SESSION_MARKERS.DONE_UPLOAD, {
      at: "2026-08-18T12:00:00.000Z",
    });
    writeSegmentState(root, "sess_a", "seg_000001", 2);
    writeSegmentState(root, "sess_b", "seg_000001", 3);
    for (const sid of ["sess_a", "sess_b"]) {
      const rawDir = path.join(root, "raw", "segments", sid);
      fs.mkdirSync(rawDir, { recursive: true });
      fs.writeFileSync(path.join(rawDir, "seg_000001.tar.zst"), Buffer.alloc(8));
    }
    const built = buildFrameMapForDerive(root);
    assert.equal(built.ok, true);
    assert.equal(built.frameMap.length, 5);
  });
});

describe("derive imu ingest-raw", () => {
  it("converts imu_raw.jsonl to sensor_raw parquet losslessly", () => {
    const root = tmpRoot();
    const imuPath = path.join(root, "imu_raw.jsonl");
    const lines = [
      { ts_ns: 1_000_000, sensor: "accel", x: 1, y: 2, z: 3 },
      { ts_ns: 1_000_000, sensor: "gyro", x: 4, y: 5, z: 6 },
      { ts_ns: 6_000_000, sensor: "gyro", x: 7, y: 8, z: 9 },
    ];
    fs.writeFileSync(imuPath, `${lines.map((l) => JSON.stringify(l)).join("\n")}\n`);

    const script = path.join(__dirname, "imu", "ingest-raw.py");
    const res = spawnSync(
      "python3",
      [script, root, "--imu-jsonl", imuPath, "--segment-id", "seg_000001", "--session-id", "sess_a", "--replace"],
      { encoding: "utf8" },
    );
    assert.equal(res.status, 0, res.stderr || res.stdout);

    const parquetPath = path.join(root, "sensor_raw/imu/chunk-000/file-000.parquet");
    assert.ok(fs.existsSync(parquetPath));
    const probe = spawnSync(
      "python3",
      [
        "-c",
        `import pyarrow.parquet as pq; t=pq.read_table("${parquetPath}"); print(t.num_rows, t.column("segment_id")[0].as_py())`,
      ],
      { encoding: "utf8" },
    );
    assert.match(probe.stdout, /1 seg_000001/);
  });
});

describe("derive imu align-main", () => {
  it("aligns nearest IMU and warns above 5ms threshold", () => {
    const root = tmpRoot();
    const imuPath = path.join(root, "imu_raw.jsonl");
    const imuLines = [
      { ts_ns: 0, sensor: "accel", x: 1, y: 0, z: 0 },
      { ts_ns: 0, sensor: "gyro", x: 0, y: 1, z: 0 },
      { ts_ns: 10_000_000, sensor: "accel", x: 2, y: 0, z: 0 },
      { ts_ns: 10_000_000, sensor: "gyro", x: 0, y: 2, z: 0 },
    ];
    fs.writeFileSync(imuPath, `${imuLines.map((l) => JSON.stringify(l)).join("\n")}\n`);
    spawnSync(
      "python3",
      [path.join(__dirname, "imu", "ingest-raw.py"), root, "--imu-jsonl", imuPath, "--segment-id", "seg_000001", "--session-id", "sess_a", "--replace"],
      { encoding: "utf8" },
    );

    const jsonlPath = path.join(root, "data/chunk-000/file-000.jsonl");
    fs.mkdirSync(path.dirname(jsonlPath), { recursive: true });
    fs.writeFileSync(
      jsonlPath,
      `${JSON.stringify({ frame_index: 0, timestamp_ns: 20_000_000, task: "t" })}\n`,
    );

    const res = spawnSync(
      "python3",
      [path.join(__dirname, "imu", "align-main.py"), root, "--jsonl", jsonlPath],
      { encoding: "utf8" },
    );
    assert.equal(res.status, 0, res.stderr || res.stdout);
    assert.ok(String(res.stderr).includes("imu_align_warn"));

    const row = JSON.parse(fs.readFileSync(jsonlPath, "utf8").trim());
    assert.deepEqual(row["observation.imu_accel"], [2, 0, 0]);
    assert.deepEqual(row["observation.imu_gyro"], [0, 2, 0]);
  });

  it("writes null (not NaN literals) for missing gyro in parquet", () => {
    const root = tmpRoot();
    const imuParquet = path.join(root, "sensor_raw/imu/chunk-000/file-000.parquet");
    fs.mkdirSync(path.dirname(imuParquet), { recursive: true });
    const pyWrite = spawnSync(
      "python3",
      [
        "-c",
        `import pyarrow as pa, pyarrow.parquet as pq, math
table = pa.table({
  "episode_index": pa.array([0], type=pa.int32()),
  "segment_id": pa.array(["seg_000001"], type=pa.string()),
  "imu_timestamp": pa.array([0.02], type=pa.float64()),
  "accel": pa.array([[1.0, 0.0, 0.0]], type=pa.list_(pa.float32())),
  "gyro": pa.array([[float("nan"), float("nan"), float("nan")]], type=pa.list_(pa.float32())),
  "mag": pa.array([[0.0, 0.0, 0.0]], type=pa.list_(pa.float32())),
})
pq.write_table(table, ${JSON.stringify(imuParquet)})`,
      ],
      { encoding: "utf8" },
    );
    assert.equal(pyWrite.status, 0, pyWrite.stderr);

    const jsonlPath = path.join(root, "data/chunk-000/file-000.jsonl");
    fs.mkdirSync(path.dirname(jsonlPath), { recursive: true });
    fs.writeFileSync(
      jsonlPath,
      `${JSON.stringify({ frame_index: 0, timestamp_ns: 20_000_000, task: "t" })}\n`,
    );

    const res = spawnSync(
      "python3",
      [path.join(__dirname, "imu", "align-main.py"), root, "--jsonl", jsonlPath],
      { encoding: "utf8" },
    );
    assert.equal(res.status, 0, res.stderr || res.stdout);

    const raw = fs.readFileSync(jsonlPath, "utf8").trim();
    assert.doesNotMatch(raw, /NaN/);
    const row = JSON.parse(raw);
    assert.equal(row["observation.imu_gyro"], null);
    assert.deepEqual(row["observation.imu_accel"], [1, 0, 0]);
  });

  it("writes null when parquet gyro has partial NaN components", () => {
    const root = tmpRoot();
    const imuParquet = path.join(root, "sensor_raw/imu/chunk-000/file-000.parquet");
    fs.mkdirSync(path.dirname(imuParquet), { recursive: true });
    const pyWrite = spawnSync(
      "python3",
      [
        "-c",
        `import pyarrow as pa, pyarrow.parquet as pq, math
table = pa.table({
  "episode_index": pa.array([0], type=pa.int32()),
  "segment_id": pa.array(["seg_000001"], type=pa.string()),
  "imu_timestamp": pa.array([0.02], type=pa.float64()),
  "accel": pa.array([[1.0, 0.0, 0.0]], type=pa.list_(pa.float32())),
  "gyro": pa.array([[float("nan"), 0.0, 0.0]], type=pa.list_(pa.float32())),
  "mag": pa.array([[0.0, 0.0, 0.0]], type=pa.list_(pa.float32())),
})
pq.write_table(table, ${JSON.stringify(imuParquet)})`,
      ],
      { encoding: "utf8" },
    );
    assert.equal(pyWrite.status, 0, pyWrite.stderr);

    const jsonlPath = path.join(root, "data/chunk-000/file-000.jsonl");
    fs.mkdirSync(path.dirname(jsonlPath), { recursive: true });
    fs.writeFileSync(
      jsonlPath,
      `${JSON.stringify({ frame_index: 0, timestamp_ns: 20_000_000, task: "t" })}\n`,
    );

    const res = spawnSync(
      "python3",
      [path.join(__dirname, "imu", "align-main.py"), root, "--jsonl", jsonlPath],
      { encoding: "utf8" },
    );
    assert.equal(res.status, 0, res.stderr || res.stdout);

    const row = JSON.parse(fs.readFileSync(jsonlPath, "utf8").trim());
    assert.doesNotMatch(fs.readFileSync(jsonlPath, "utf8"), /NaN/);
    assert.equal(row["observation.imu_gyro"], null);
  });
});

describe("derive parquet-writer", () => {
  it("remaps local frame_index to global frame_map indices", () => {
    const root = tmpRoot();
    const extractDir = path.join(root, "extract");
    fs.mkdirSync(path.join(extractDir, "frames"), { recursive: true });
    fs.writeFileSync(
      path.join(extractDir, "rows.jsonl"),
      `${JSON.stringify({ frame_index: 0, timestamp_ns: 1, task: "t" })}\n${JSON.stringify({ frame_index: 1, timestamp_ns: 2, task: "t" })}\n`,
    );

    const frameMap = {
      episode_index: 0,
      length: 5,
      frame_index_min: 0,
      frame_index_max: 4,
      frame_segments: [
        {
          session_id: "sess_a",
          segment_id: "seg_000002",
          local_frame_min: 0,
          local_frame_max: 1,
          global_start: 3,
          global_end: 4,
          frame_count: 2,
        },
      ],
    };

    const rows = remapSegmentRows(frameMap, "sess_a", "seg_000002", extractDir);
    assert.deepEqual(rows.map((r) => r.frame_index), [3, 4]);
    writeMainJsonl(root, rows);
    const persisted = fs.readFileSync(path.join(root, "data/chunk-000/file-000.jsonl"), "utf8").trim().split("\n");
    assert.equal(JSON.parse(persisted[0]).frame_index, 3);
  });
});

describe("derive mux-exec", () => {
  it("writes mux_validated.json for four-camera output", async () => {
    const root = tmpRoot();
    const stationId = "ego-001";
    const frameMap = { length: 1, frame_index_min: 0, frame_index_max: 0 };
    const keys = [
      "observation.images.camera_front_left",
      "observation.images.camera_front_right",
      "observation.images.camera_rear_left",
      "observation.images.camera_rear_right",
    ];
    const jpeg = Buffer.from([0xff, 0xd8, 0xff, 0xd9]);
    for (const key of keys) {
      const dir = path.join(root, "_staging", key.replace(/\./g, "_"));
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, "frame_000000.jpg"), jpeg);
    }

    if (spawnSync("ffmpeg", ["-version"]).status !== 0) {
      return;
    }

    const mux = await runFourCameraMux(stationId, root, frameMap);
    assert.equal(mux.results.length, 4);
    const validated = writeMuxValidatedSnapshot(root, "sess_mux", frameMap, stationId);
    assert.ok(fs.existsSync(path.join(root, "live/derive/mux_validated.json")));
    assert.equal(validated.expected_frames, 1);
    assert.equal(Object.keys(validated.frames).length, 4);
  });
});
