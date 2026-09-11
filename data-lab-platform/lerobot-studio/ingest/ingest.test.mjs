import { describe, it, before } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import {
  SEGMENT_INGEST_STATUS,
  listSessionSegmentStates,
  readSegmentState,
  transitionSegmentState,
} from "./segment-state.mjs";
import {
  evaluateSessionUploadGate,
  maybeMarkSessionDoneUpload,
  touchUploadActivity,
} from "./session-coordinator.mjs";
import {
  makeTestSessionSeal,
  reconcileSealWithSegmentStates,
  writeServerSessionSeal,
} from "./session-seal.mjs";
import { atomicMoveFile, rawSegmentArchivePath } from "./io.mjs";
import { validateTarZstArchive } from "./tar-validator.mjs";
import { ingestTarZstArchive } from "./receive-tar.mjs";
import { hasSessionMarker } from "../session-markers.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");

function tmpRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ego-ingest-test-"));
}

function packFrameBin(cameraJpegs) {
  const keys = Object.keys(cameraJpegs);
  const parts = [Buffer.from("DLB1"), Buffer.from([1, keys.length])];
  for (const key of keys) {
    const keyBuf = Buffer.from(key, "utf8");
    const jpeg = cameraJpegs[key];
    const header = Buffer.alloc(6);
    header.writeUInt16LE(keyBuf.length, 0);
    header.writeUInt32LE(jpeg.length, 2);
    parts.push(header, keyBuf, jpeg);
  }
  return Buffer.concat(parts);
}

function writeValidSegmentDir(dir, { sessionId = "sess_test", segmentId = "seg_000001" } = {}) {
  fs.mkdirSync(path.join(dir, "frames"), { recursive: true });
  const manifest = {
    segment_id: segmentId,
    session_id: sessionId,
    start_frame_index: 0,
    end_frame_index: 0,
    frame_count: 1,
    status: "CLOSED",
    manifest_schema_version: 2,
  };
  fs.writeFileSync(path.join(dir, "manifest.json"), `${JSON.stringify(manifest)}\n`);
  fs.writeFileSync(
    path.join(dir, "rows.jsonl"),
    `${JSON.stringify({ frame_index: 0, timestamp_ns: 1, task: "t", "observation.state": [0, 0, 0, 0, 0, 0] })}\n`,
  );
  fs.writeFileSync(
    path.join(dir, "imu_raw.jsonl"),
    `${JSON.stringify({ ts_ns: 1, sensor: "gyro", x: 0, y: 0, z: 0 })}\n`,
  );
  const jpegs = {
    "observation.images.camera_front_left": Buffer.from([0xff, 0xd8, 0xff, 0xd9]),
    "observation.images.camera_front_right": Buffer.from([0xff, 0xd8, 0xff, 0xd9]),
    "observation.images.camera_rear_left": Buffer.from([0xff, 0xd8, 0xff, 0xd9]),
    "observation.images.camera_rear_right": Buffer.from([0xff, 0xd8, 0xff, 0xd9]),
  };
  fs.writeFileSync(path.join(dir, "frames", "00000000.bin"), packFrameBin(jpegs));
}

function buildTarZst(segmentDir, outPath) {
  const res = spawnSync("tar", ["-C", segmentDir, "-cf", "-", "."], { encoding: "buffer" });
  assert.equal(res.status, 0, "tar create failed");
  const z = spawnSync("zstd", ["-o", outPath, "-"], { input: res.stdout });
  assert.equal(z.status, 0, "zstd failed");
}

describe("ingest segment-state", () => {
  it("transitions RECEIVED → INGESTING → DERIVE_PENDING", () => {
    const root = tmpRoot();
    const sessionId = "sess_a";
    const segmentId = "seg_000001";
    transitionSegmentState(root, sessionId, segmentId, SEGMENT_INGEST_STATUS.RECEIVED);
    transitionSegmentState(root, sessionId, segmentId, SEGMENT_INGEST_STATUS.INGESTING);
    transitionSegmentState(root, sessionId, segmentId, SEGMENT_INGEST_STATUS.DERIVE_PENDING, {
      frame_count: 1,
    });
    const state = readSegmentState(root, sessionId, segmentId);
    assert.equal(state.status, SEGMENT_INGEST_STATUS.DERIVE_PENDING);
    assert.equal(state.frame_count, 1);
  });
});

describe("ingest session-coordinator", () => {
  it("marks DONE_UPLOAD only when all segments DERIVE_PENDING", () => {
    const root = tmpRoot();
    const sessionId = "sess_gate";
    touchUploadActivity(root, { sessionId, expectedSegmentTotal: 2 });
    writeServerSessionSeal(
      root,
      sessionId,
      makeTestSessionSeal(sessionId, ["seg_000001", "seg_000002"]),
      { source: "test" },
    );
    transitionSegmentState(root, sessionId, "seg_000001", SEGMENT_INGEST_STATUS.DERIVE_PENDING);
    let gate = evaluateSessionUploadGate(root, sessionId);
    assert.equal(gate.complete, false);
    let done = maybeMarkSessionDoneUpload(root, sessionId);
    assert.equal(done.marked, false);
    assert.equal(hasSessionMarker(root, sessionId, "session.DONE_UPLOAD"), false);

    transitionSegmentState(root, sessionId, "seg_000002", SEGMENT_INGEST_STATUS.DERIVE_PENDING);
    gate = evaluateSessionUploadGate(root, sessionId);
    assert.equal(gate.complete, true);
    done = maybeMarkSessionDoneUpload(root, sessionId);
    assert.equal(done.marked, true);
    assert.equal(hasSessionMarker(root, sessionId, "session.DONE_UPLOAD"), true);
  });

  it("does not mark DONE_UPLOAD without complete server seal", () => {
    const root = tmpRoot();
    const sessionId = "sess_no_seal";
    touchUploadActivity(root, { sessionId, expectedSegmentTotal: 1 });
    transitionSegmentState(root, sessionId, "seg_000001", SEGMENT_INGEST_STATUS.DERIVE_PENDING);
    const gate = evaluateSessionUploadGate(root, sessionId);
    assert.equal(gate.complete, false);
    assert.equal(gate.sealReconcile.reason, "no_complete_seal");
    const done = maybeMarkSessionDoneUpload(root, sessionId);
    assert.equal(done.marked, false);
  });

  it("does not mark DONE_UPLOAD for partial multi-segment upload without declared total", () => {
    const root = tmpRoot();
    const sessionId = "sess_multi";
    transitionSegmentState(root, sessionId, "seg_000001", SEGMENT_INGEST_STATUS.DERIVE_PENDING);
    transitionSegmentState(root, sessionId, "seg_000002", SEGMENT_INGEST_STATUS.DERIVE_PENDING);
    const gate = evaluateSessionUploadGate(root, sessionId);
    assert.equal(gate.complete, false);
    const done = maybeMarkSessionDoneUpload(root, sessionId);
    assert.equal(done.marked, false);
  });
});

describe("ingest io raw landing", () => {
  it("atomicMoveFile lands raw archive at final path", () => {
    const root = tmpRoot();
    const src = path.join(root, "incoming.tar.zst");
    fs.writeFileSync(src, Buffer.from("tar-payload"));
    const dest = rawSegmentArchivePath(root, "sess_raw", "seg_000001");
    atomicMoveFile(src, dest);
    assert.ok(fs.existsSync(dest));
    assert.ok(!fs.existsSync(src));
  });
});

describe("ingest tar-validator", () => {
  let archivePath = "";
  let badArchivePath = "";

  before(() => {
    const segDir = tmpRoot();
    writeValidSegmentDir(segDir);
    archivePath = path.join(segDir, "good.tar.zst");
    buildTarZst(segDir, archivePath);

    const badDir = tmpRoot();
    fs.mkdirSync(badDir, { recursive: true });
    fs.writeFileSync(path.join(badDir, "manifest.json"), "{}");
    badArchivePath = path.join(badDir, "bad.tar.zst");
    buildTarZst(badDir, badArchivePath);
  });

  it("accepts valid ego segment archive", async () => {
    const result = await validateTarZstArchive(archivePath);
    assert.equal(result.ok, true, JSON.stringify(result.issues));
    assert.equal(result.frame_count, 1);
  });

  it("rejects archive missing required members", async () => {
    const result = await validateTarZstArchive(badArchivePath);
    assert.equal(result.ok, false);
    assert.ok(result.issues.length > 0);
  });
});

describe("ingest receive-tar integration", () => {
  it("ingests tar.zst to raw + DERIVE_PENDING + session DONE_UPLOAD", async () => {
    const stationRootDir = tmpRoot();
    const prev = process.env.STREAM_DATA_ROOT;
    process.env.STREAM_DATA_ROOT = stationRootDir;
    const stationId = "ego-test-tar";
    const sessionId = "sess_ingest";
    const segmentId = "seg_000001";
    const root = path.join(stationRootDir, stationId);

    const segDir = tmpRoot();
    writeValidSegmentDir(segDir, { sessionId, segmentId });
    const archivePath = path.join(segDir, "upload.tar.zst");
    buildTarZst(segDir, archivePath);

    try {
      const out = await ingestTarZstArchive(stationId, {
        archivePath,
        sessionId,
        segmentId,
        expectedSegmentTotal: 1,
        sessionSeal: makeTestSessionSeal(sessionId, [segmentId]),
        source: "test",
      });
      assert.equal(out.status, SEGMENT_INGEST_STATUS.DERIVE_PENDING);
      const rawPath = rawSegmentArchivePath(root, sessionId, segmentId);
      assert.ok(fs.existsSync(rawPath));
      const state = readSegmentState(root, sessionId, segmentId);
      assert.equal(state.status, SEGMENT_INGEST_STATUS.DERIVE_PENDING);
      assert.equal(hasSessionMarker(root, sessionId, "session.DONE_UPLOAD"), true);
      const stagingDir = path.join(root, "_staging", "observation_images_camera_front_left");
      assert.ok(fs.existsSync(path.join(stagingDir, "frame_000000.jpg")));
    } finally {
      if (prev === undefined) delete process.env.STREAM_DATA_ROOT;
      else process.env.STREAM_DATA_ROOT = prev;
    }
  });
});

describe("ingest session-seal", () => {
  it("seal first then segments completes DONE_UPLOAD", () => {
    const root = tmpRoot();
    const sessionId = "sess_seal_first";
    writeServerSessionSeal(
      root,
      sessionId,
      makeTestSessionSeal(sessionId, ["seg_000001", "seg_000002"]),
      { source: "test" },
    );
    transitionSegmentState(root, sessionId, "seg_000001", SEGMENT_INGEST_STATUS.DERIVE_PENDING);
    assert.equal(evaluateSessionUploadGate(root, sessionId).complete, false);
    transitionSegmentState(root, sessionId, "seg_000002", SEGMENT_INGEST_STATUS.DERIVE_PENDING);
    const gate = evaluateSessionUploadGate(root, sessionId);
    assert.equal(gate.complete, true);
    const done = maybeMarkSessionDoneUpload(root, sessionId);
    assert.equal(done.marked, true);
  });

  it("segments without seal stay incomplete", () => {
    const root = tmpRoot();
    const sessionId = "sess_segments_no_seal";
    transitionSegmentState(root, sessionId, "seg_000001", SEGMENT_INGEST_STATUS.DERIVE_PENDING);
    assert.equal(reconcileSealWithSegmentStates(root, sessionId).ok, false);
    assert.equal(maybeMarkSessionDoneUpload(root, sessionId).marked, false);
  });

  it("rejects sha256 conflict in seal", () => {
    const root = tmpRoot();
    const sessionId = "sess_sha_conflict";
    const seal = makeTestSessionSeal(sessionId, ["seg_000001"], {
      sha256BySegment: { seg_000001: "aaa" },
    });
    writeServerSessionSeal(root, sessionId, seal, { source: "test" });
    transitionSegmentState(root, sessionId, "seg_000001", SEGMENT_INGEST_STATUS.DERIVE_PENDING, {
      sha256: "bbb",
    });
    const rec = reconcileSealWithSegmentStates(root, sessionId);
    assert.equal(rec.ok, false);
    assert.ok(rec.issues.some((i) => i.code === "sha256_mismatch"));
  });

  it("duplicate segment ack does not bypass missing seal entries", () => {
    const root = tmpRoot();
    const sessionId = "sess_missing_seg";
    writeServerSessionSeal(
      root,
      sessionId,
      makeTestSessionSeal(sessionId, ["seg_000001", "seg_000002"]),
      { source: "test" },
    );
    transitionSegmentState(root, sessionId, "seg_000001", SEGMENT_INGEST_STATUS.DERIVE_PENDING);
    const rec = reconcileSealWithSegmentStates(root, sessionId);
    assert.equal(rec.ok, false);
    assert.ok(rec.issues.some((i) => i.code === "missing_segment"));
  });
});
