import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { describe, it, before, after } from "node:test";

import {
  checkG2bMp4Decode,
  checkG7BrowserPlayable,
  commercialGateEnabled,
  probeMp4DecodeHealth,
  reconcileExceedsSlack,
  remuxUsesExactFrameCount,
} from "./mp4-playback-gate.mjs";

function tmpRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ego-mp4-gate-"));
}

function hasFfmpeg() {
  return spawnSync("ffmpeg", ["-version"]).status === 0;
}

function makeTinyMp4(dest) {
  const res = spawnSync(
    "ffmpeg",
    [
      "-y",
      "-hide_banner",
      "-loglevel",
      "error",
      "-f",
      "lavfi",
      "-i",
      "testsrc=size=64x48:rate=10",
      "-frames:v",
      "3",
      "-c:v",
      "libx264",
      "-pix_fmt",
      "yuv420p",
      dest,
    ],
    { encoding: "utf8" },
  );
  assert.equal(res.status, 0, res.stderr);
}

describe("mp4-playback-gate", () => {
  const prevCommercial = process.env.EGO_DERIVE_COMMERCIAL_GATE;
  const prevExact = process.env.DERIVE_FFPROBE_COUNT_FRAMES;

  after(() => {
    if (prevCommercial === undefined) delete process.env.EGO_DERIVE_COMMERCIAL_GATE;
    else process.env.EGO_DERIVE_COMMERCIAL_GATE = prevCommercial;
    if (prevExact === undefined) delete process.env.DERIVE_FFPROBE_COUNT_FRAMES;
    else process.env.DERIVE_FFPROBE_COUNT_FRAMES = prevExact;
  });

  it("commercial gate defaults on", () => {
    delete process.env.EGO_DERIVE_COMMERCIAL_GATE;
    assert.equal(commercialGateEnabled(), true);
    process.env.EGO_DERIVE_COMMERCIAL_GATE = "0";
    assert.equal(commercialGateEnabled(), false);
  });

  it("remux forces exact ffprobe", () => {
    delete process.env.DERIVE_FFPROBE_COUNT_FRAMES;
    assert.equal(remuxUsesExactFrameCount("remux"), true);
    assert.equal(remuxUsesExactFrameCount("encode"), false);
  });

  it("reconcileExceedsSlack detects block threshold", () => {
    assert.equal(reconcileExceedsSlack({ trimmed: 10, slack: 5 }), true);
    assert.equal(reconcileExceedsSlack({ trimmed: 5, slack: 5 }), false);
    assert.equal(reconcileExceedsSlack(null), false);
  });

  it("probeMp4DecodeHealth rejects missing file", () => {
    const health = probeMp4DecodeHealth("/nonexistent/file.mp4");
    assert.equal(health.ok, false);
  });

  it("G2b and G7 pass on valid tiny mp4", { skip: !hasFfmpeg() }, () => {
    const root = tmpRoot();
    const stationId = "ego-001";
    const keys = [
      "observation.images.camera_front_left",
      "observation.images.camera_front_right",
      "observation.images.camera_rear_left",
      "observation.images.camera_rear_right",
    ];
    for (const key of keys) {
      const dest = path.join(root, "videos", `${key}.mp4`);
      fs.mkdirSync(path.dirname(dest), { recursive: true });
      makeTinyMp4(dest);
    }
    const g2b = checkG2bMp4Decode(root, stationId);
    assert.equal(g2b.ok, true, JSON.stringify(g2b));
    const g7 = checkG7BrowserPlayable(root, stationId, 3, { exact: true });
    assert.equal(g7.ok, true, JSON.stringify(g7));
  });

  it("G2b fails on truncated/corrupt bitstream", { skip: !hasFfmpeg() }, () => {
    const root = tmpRoot();
    const stationId = "ego-001";
    const key = "observation.images.camera_front_left";
    const dest = path.join(root, "videos", `${key}.mp4`);
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    makeTinyMp4(dest);
    const buf = fs.readFileSync(dest);
    fs.writeFileSync(dest, buf.subarray(0, Math.floor(buf.length * 0.5)));
    const g2b = checkG2bMp4Decode(root, stationId);
    assert.equal(g2b.ok, false);
    assert.equal(g2b.reason?.code, "MUX_DECODE_FAILED");
  });
});
