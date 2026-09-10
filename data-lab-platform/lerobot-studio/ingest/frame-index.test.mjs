import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { resolveFrameIndex } from "./frame-index.mjs";

describe("frame-index", () => {
  it("uses session-global frame_index when manifest declares start_frame_index", () => {
    const manifest = { start_frame_index: 1800, manifest_schema_version: 2 };
    assert.equal(resolveFrameIndex({ frame_index: 1800 }, manifest, 1800), 1800);
    assert.equal(resolveFrameIndex({ frame_index: 3599 }, manifest, 1800), 3599);
  });

  it("remaps legacy segment-local rows via global_start", () => {
    assert.equal(resolveFrameIndex({ frame_index: 0 }, null, 3), 3);
    assert.equal(resolveFrameIndex({ frame_index: 1 }, null, 3), 4);
  });

  it("keeps zero-based single-segment rows", () => {
    const manifest = { start_frame_index: 0 };
    assert.equal(resolveFrameIndex({ frame_index: 0 }, manifest, 0), 0);
    assert.equal(resolveFrameIndex({ frame_index: 242 }, manifest, 0), 242);
  });

  it("remaps second-segment local rows when start_frame_index is zero", () => {
    const manifest = { start_frame_index: 0, manifest_schema_version: 2 };
    assert.equal(resolveFrameIndex({ frame_index: 0 }, manifest, 709), 709);
    assert.equal(resolveFrameIndex({ frame_index: 10 }, manifest, 709), 719);
  });
});
