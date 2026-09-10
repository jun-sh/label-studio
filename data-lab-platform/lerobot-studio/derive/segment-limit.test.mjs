import { describe, it, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  countSessionSegments,
  deriveMaxSegmentsAction,
  deriveMaxSegmentsPerSession,
  evaluateSessionSegmentLimit,
  filterSessionsWithinSegmentLimit,
} from "./segment-limit.mjs";

describe("segment limit (P1)", () => {
  const prevMax = process.env.DERIVE_MAX_SEGMENTS_PER_SESSION;
  const prevAction = process.env.DERIVE_MAX_SEGMENTS_ACTION;

  afterEach(() => {
    if (prevMax === undefined) delete process.env.DERIVE_MAX_SEGMENTS_PER_SESSION;
    else process.env.DERIVE_MAX_SEGMENTS_PER_SESSION = prevMax;
    if (prevAction === undefined) delete process.env.DERIVE_MAX_SEGMENTS_ACTION;
    else process.env.DERIVE_MAX_SEGMENTS_ACTION = prevAction;
  });

  it("defaults to disabled limit", () => {
    delete process.env.DERIVE_MAX_SEGMENTS_PER_SESSION;
    assert.equal(deriveMaxSegmentsPerSession(), 0);
    assert.equal(deriveMaxSegmentsAction(), "defer");
  });

  it("counts tar.zst segments under raw/segments/{session}", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "seg-limit-"));
    const sessionId = "sess_test";
    const rawDir = path.join(root, "raw", "segments", sessionId);
    fs.mkdirSync(rawDir, { recursive: true });
    fs.writeFileSync(path.join(rawDir, "seg_a.tar.zst"), "a");
    fs.writeFileSync(path.join(rawDir, "seg_b.tar.zst"), "b");
    assert.equal(countSessionSegments(root, sessionId), 2);
  });

  it("defers when count exceeds max", () => {
    process.env.DERIVE_MAX_SEGMENTS_PER_SESSION = "5";
    process.env.DERIVE_MAX_SEGMENTS_ACTION = "defer";
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "seg-limit-"));
    const sessionId = "sess_big";
    const rawDir = path.join(root, "raw", "segments", sessionId);
    fs.mkdirSync(rawDir, { recursive: true });
    for (let i = 0; i < 6; i += 1) {
      fs.writeFileSync(path.join(rawDir, `seg_${i}.tar.zst`), "x");
    }
    const result = evaluateSessionSegmentLimit(root, sessionId);
    assert.equal(result.ok, false);
    assert.equal(result.action, "defer");
    assert.equal(result.reason.code, "SEGMENT_LIMIT_EXCEEDED");
  });

  it("filters pending list to within-limit sessions", () => {
    process.env.DERIVE_MAX_SEGMENTS_PER_SESSION = "2";
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "seg-limit-"));
    for (const [sessionId, count] of [
      ["sess_small", 1],
      ["sess_big", 3],
    ]) {
      const rawDir = path.join(root, "raw", "segments", sessionId);
      fs.mkdirSync(rawDir, { recursive: true });
      for (let i = 0; i < count; i += 1) {
        fs.writeFileSync(path.join(rawDir, `seg_${i}.tar.zst`), "x");
      }
    }
    const filtered = filterSessionsWithinSegmentLimit(root, ["sess_small", "sess_big"]);
    assert.deepEqual(filtered, ["sess_small"]);
  });
});
