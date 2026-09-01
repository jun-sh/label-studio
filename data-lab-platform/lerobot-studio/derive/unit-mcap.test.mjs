import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

import { SEGMENT_INGEST_STATUS, transitionSegmentState } from "../ingest/segment-state.mjs";
import { rawMcapArchivePath } from "../ingest/io.mjs";
import { deriveUnit, runUnitReadyGate } from "./unit.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const GOLDEN = path.join(__dirname, "..", "..", "fixtures", "mcap", "golden-seg.mcap");

function tmpRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ego-unit-mcap-"));
}

function zstdPack(src, dest) {
  const res = spawnSync("zstd", ["-o", dest, src]);
  assert.equal(res.status, 0, "zstd pack failed");
}

describe("unit derive mcap", () => {
  it("derives golden mcap.zst session to unit READY", async () => {
    if (!fs.existsSync(GOLDEN)) return;
    if (spawnSync("ffmpeg", ["-version"]).status !== 0) return;

    const stationId = "ego-mcap-pilot";
    const sessionId = "sess_golden_fixture";
    const segmentId = "seg_000001";
    const root = path.join(tmpRoot(), stationId);
    fs.mkdirSync(root, { recursive: true });
    fs.mkdirSync(path.join(root, "meta"), { recursive: true });
    fs.writeFileSync(
      path.join(root, "meta", "info.json"),
      `${JSON.stringify({ features: {}, total_frames: 0 }, null, 2)}\n`,
    );

    const archivePath = rawMcapArchivePath(root, sessionId, segmentId);
    fs.mkdirSync(path.dirname(archivePath), { recursive: true });
    zstdPack(GOLDEN, archivePath);

    transitionSegmentState(root, sessionId, segmentId, SEGMENT_INGEST_STATUS.DERIVE_PENDING, {
      sourceFormat: "mcap",
      frame_count: 3,
      raw_archive: path.relative(root, archivePath),
    });

    const out = await deriveUnit(stationId, root, sessionId);
    assert.equal(out.ok, true, JSON.stringify(out.gate));
    assert.ok(fs.existsSync(path.join(out.unitDir, "unit.json")));
    assert.ok(fs.existsSync(path.join(out.unitDir, "data.parquet")));
    assert.ok(fs.existsSync(path.join(out.unitDir, "imu.parquet")));
    const gate = runUnitReadyGate(out.unitDir, stationId, 3);
    assert.equal(gate.ok, true, JSON.stringify(gate));
  });
});
