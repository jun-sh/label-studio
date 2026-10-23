import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, it } from "node:test";

import {
  getTrackedUsageBytes,
  recordStationDiskDelta,
  recordStationUsageReconcile,
} from "./disk-accounting.mjs";

function mkStationRoot() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "disk-acct-"));
  fs.mkdirSync(path.join(root, "live"), { recursive: true });
  return root;
}

describe("disk-accounting", () => {
  let root = null;
  afterEach(() => {
    if (root) fs.rmSync(root, { recursive: true, force: true });
    root = null;
  });

  it("adopts reconcile baseline then applies incremental delta", () => {
    root = mkStationRoot();
    recordStationUsageReconcile(root, "ego-test", 1_000_000);
    assert.equal(getTrackedUsageBytes(root), 1_000_000);
    recordStationDiskDelta(root, "ego-test", 500, { reason: "ingest_mcap" });
    assert.equal(getTrackedUsageBytes(root), 1_000_500);
    recordStationDiskDelta(root, "ego-test", -200, { reason: "purge" });
    assert.equal(getTrackedUsageBytes(root), 1_000_300);
  });

  it("clamps usage at zero", () => {
    root = mkStationRoot();
    recordStationDiskDelta(root, "ego-test", 100);
    recordStationDiskDelta(root, "ego-test", -10_000);
    assert.equal(getTrackedUsageBytes(root), 0);
  });
});
