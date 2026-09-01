import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, it } from "node:test";

import {
  isDeriveFailureRetryable,
  maxDeriveRetryAttempts,
  prepareSessionDeriveRetry,
  structuredDeriveFailure,
} from "./session-retry.mjs";
import { markSessionFailed, SESSION_MARKERS } from "../session-markers.mjs";

function tmpRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ego-session-retry-"));
}

describe("derive session retry policy", () => {
  it("blocks retry for non-retryable gate codes", () => {
    assert.equal(isDeriveFailureRetryable({ code: "PARQUET_INDEX_GAP" }), false);
    assert.equal(isDeriveFailureRetryable({ code: "MUX_FRAME_MISMATCH" }), false);
    assert.equal(isDeriveFailureRetryable({ code: "DERIVE_TRANSIENT" }), true);
  });

  it("prepareSessionDeriveRetry clears FAILED marker", () => {
    const root = tmpRoot();
    const sessionId = "sess_retry_clear";
    markSessionFailed(root, sessionId, { code: "DERIVE_TRANSIENT", message: "boom" });
    const out = prepareSessionDeriveRetry(root, sessionId);
    assert.ok(out.actions.includes("cleared_failed"));
    assert.equal(fs.existsSync(path.join(root, "state", "sessions", sessionId, SESSION_MARKERS.FAILED)), false);
  });

  it("structuredDeriveFailure preserves code and category", () => {
    const reason = structuredDeriveFailure({ code: "G1", message: "gap", category: "derive" });
    assert.equal(reason.code, "G1");
    assert.equal(reason.category, "derive");
  });

  it("maxDeriveRetryAttempts defaults to 2", () => {
    const prev = process.env.DERIVE_MAX_RETRY_ATTEMPTS;
    delete process.env.DERIVE_MAX_RETRY_ATTEMPTS;
    assert.equal(maxDeriveRetryAttempts(), 2);
    if (prev !== undefined) process.env.DERIVE_MAX_RETRY_ATTEMPTS = prev;
  });
});
