import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, it } from "node:test";

import {
  evaluateSessionUploadGate,
  maybeMarkSessionDoneUpload,
  reconcileSessionUploadMarkers,
} from "./session-coordinator.mjs";
import { SEGMENT_INGEST_STATUS, transitionSegmentState } from "./segment-state.mjs";
import { hasSessionMarker, SESSION_MARKERS, writeSessionMarker } from "../session-markers.mjs";

function tmpRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ego-session-coord-"));
}

describe("session-coordinator upload gate", () => {
  it("does not mark DONE_UPLOAD when a segment ingest failed", () => {
    const root = tmpRoot();
    const sessionId = "sess_coord_fail";
    transitionSegmentState(root, sessionId, "seg_000001", SEGMENT_INGEST_STATUS.DERIVE_PENDING, {
      frame_count: 10,
    });
    transitionSegmentState(root, sessionId, "seg_000002", SEGMENT_INGEST_STATUS.INGEST_FAILED, {
      error: { code: "MCAP_VALIDATION_FAILED", message: "bad" },
    });
    const gate = evaluateSessionUploadGate(root, sessionId);
    assert.equal(gate.failedCount, 1);
    const marked = maybeMarkSessionDoneUpload(root, sessionId);
    assert.equal(marked.marked, false);
    assert.equal(hasSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD), false);
  });

  it("reconciles stale DONE_UPLOAD when ingest incomplete", () => {
    const root = tmpRoot();
    const sessionId = "sess_coord_stale";
    writeSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD, { test: true });
    transitionSegmentState(root, sessionId, "seg_000001", SEGMENT_INGEST_STATUS.INGESTING, {});
    const result = reconcileSessionUploadMarkers(root, sessionId);
    assert.ok(result.actions.includes("cleared_stale_done_upload"));
    assert.equal(hasSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD), false);
  });
});
