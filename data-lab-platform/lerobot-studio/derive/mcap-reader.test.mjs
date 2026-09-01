import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

import {
  materializeMcapExtract,
  materializeMcapUnitFrames,
  summarizeMcapArchive,
} from "./mcap-reader.mjs";
import { readJsonl } from "./io.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const GOLDEN = path.join(__dirname, "..", "..", "fixtures", "mcap", "golden-seg.mcap");

function tmpRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ego-mcap-reader-"));
}

function zstdPack(src, dest) {
  const res = spawnSync("zstd", ["-o", dest, src]);
  assert.equal(res.status, 0, "zstd pack failed");
}

describe("mcap-reader", () => {
  it("summarizes golden fixture frame count", () => {
    if (!fs.existsSync(GOLDEN)) return;
    const summary = summarizeMcapArchive(GOLDEN);
    assert.equal(summary.ok, true);
    assert.equal(summary.frame_count, 3);
    assert.ok(summary.topics["/ego/camera/front_left"] >= 3);
  });

  it("materializes extract dir with rows, imu, and camera jpegs", () => {
    if (!fs.existsSync(GOLDEN)) return;
    const work = tmpRoot();
    const extractDir = path.join(work, "extract");
    const out = materializeMcapExtract(GOLDEN, extractDir);
    assert.equal(out.frame_count, 3);
    const rows = readJsonl(path.join(extractDir, "rows.jsonl"));
    assert.equal(rows.length, 3);
    assert.ok(fs.existsSync(path.join(extractDir, "imu_raw.jsonl")));
    assert.ok(fs.existsSync(path.join(extractDir, "frames", "front_left", "00000000.jpg")));
    const unitTmp = path.join(work, "unit");
    fs.mkdirSync(unitTmp, { recursive: true });
    const frames = materializeMcapUnitFrames(unitTmp, extractDir, "ego-mcap-pilot");
    assert.equal(frames.frameCount, 3);
    assert.ok(frames.written >= 12);
  });

  it("materializes .mcap.zst archives", () => {
    if (!fs.existsSync(GOLDEN)) return;
    const work = tmpRoot();
    const archive = path.join(work, "seg_000001.mcap.zst");
    zstdPack(GOLDEN, archive);
    const extractDir = path.join(work, "extract");
    const out = materializeMcapExtract(archive, extractDir);
    assert.equal(out.frame_count, 3);
    assert.equal(readJsonl(path.join(extractDir, "rows.jsonl")).length, 3);
  });
});
