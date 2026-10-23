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
import { makeTestSessionSeal, writeServerSessionSeal } from "./session-seal.mjs";
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

  it("ignores failed ingest states for segments outside the complete seal", () => {
    const root = tmpRoot();
    const sessionId = "sess_coord_stray";
    const segs = ["seg_000001", "seg_000002"];
    for (const seg of segs) {
      transitionSegmentState(root, sessionId, seg, SEGMENT_INGEST_STATUS.DERIVE_PENDING, {
        frame_count: 10,
      });
    }
    transitionSegmentState(root, sessionId, "seg_probe", SEGMENT_INGEST_STATUS.INGEST_FAILED, {
      error: { code: "mcap_invalid", message: "probe" },
    });
    writeServerSessionSeal(root, sessionId, makeTestSessionSeal(sessionId, segs));
    const gate = evaluateSessionUploadGate(root, sessionId);
    assert.equal(gate.failedCount, 0);
    assert.equal(gate.complete, true);
    assert.equal(maybeMarkSessionDoneUpload(root, sessionId).marked, true);
  });

  it("marks DONE_UPLOAD when seal lists CORRUPT segment skipped at edge", () => {
    const root = tmpRoot();
    const sessionId = "sess_coord_corrupt";
    transitionSegmentState(root, sessionId, "seg_000002", SEGMENT_INGEST_STATUS.DERIVE_PENDING, {
      frame_count: 10,
    });
    const seal = makeTestSessionSeal(sessionId, ["seg_000001", "seg_000002"]);
    seal.segments[0].status = "CORRUPT";
    writeServerSessionSeal(root, sessionId, seal);
    const gate = evaluateSessionUploadGate(root, sessionId);
    assert.equal(gate.complete, true);
    assert.equal(gate.sealReconcile.deliveryQuality.gaps.length, 1);
    assert.equal(maybeMarkSessionDoneUpload(root, sessionId).marked, true);
  });

  it("still blocks DONE_UPLOAD when a sealed segment failed", () => {
    const root = tmpRoot();
    const sessionId = "sess_coord_sealed_fail";
    const segs = ["seg_000001", "seg_000002"];
    transitionSegmentState(root, sessionId, segs[0], SEGMENT_INGEST_STATUS.DERIVE_PENDING, {
      frame_count: 10,
    });
    transitionSegmentState(root, sessionId, segs[1], SEGMENT_INGEST_STATUS.INGEST_FAILED, {
      error: { code: "mcap_invalid", message: "bad" },
    });
    writeServerSessionSeal(root, sessionId, makeTestSessionSeal(sessionId, segs));
    assert.equal(maybeMarkSessionDoneUpload(root, sessionId).marked, false);
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
