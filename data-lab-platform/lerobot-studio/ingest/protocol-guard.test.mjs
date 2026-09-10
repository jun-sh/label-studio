import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { transitionSegmentState, SEGMENT_INGEST_STATUS } from "./segment-state.mjs";
import {
  assertSourceFormatCompatible,
  collectSessionSourceFormats,
  resolveSessionSourceFormat,
  SOURCE_FORMAT,
} from "./protocol-guard.mjs";

function tmpRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ego-protocol-guard-"));
}

describe("protocol-guard", () => {
  it("locks session to first source format", () => {
    const root = tmpRoot();
    const sessionId = "sess_a";
    transitionSegmentState(root, sessionId, "seg_0001", SEGMENT_INGEST_STATUS.DERIVE_PENDING, {
      sourceFormat: SOURCE_FORMAT.MCAP,
    });
    assert.equal(resolveSessionSourceFormat(root, sessionId), SOURCE_FORMAT.MCAP);
    assert.throws(
      () => assertSourceFormatCompatible("ego-test", root, sessionId, SOURCE_FORMAT.TARZST),
      (err) => err.statusCode === 409 && err.reason?.code === "PROTOCOL_MIXED",
    );
  });

  it("detects mixed segment source formats", () => {
    const root = tmpRoot();
    const sessionId = "sess_b";
    transitionSegmentState(root, sessionId, "seg_0001", SEGMENT_INGEST_STATUS.DERIVE_PENDING, {
      sourceFormat: SOURCE_FORMAT.TARZST,
    });
    transitionSegmentState(root, sessionId, "seg_0002", SEGMENT_INGEST_STATUS.DERIVE_PENDING, {
      sourceFormat: SOURCE_FORMAT.MCAP,
    });
    assert.equal(resolveSessionSourceFormat(root, sessionId), "mixed");
  });
});
