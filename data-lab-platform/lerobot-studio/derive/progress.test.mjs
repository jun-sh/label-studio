import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  DERIVE_PROGRESS_PHASES,
  deriveProgressPercent,
  readDeriveProgress,
  writeDeriveProgress,
  isDeriveProgressStale,
} from "./progress.mjs";

describe("derive progress", () => {
  it("writes and reads crash-safe progress atomically", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "ego-progress-"));
    const sessionId = "sess_test_progress";
    writeDeriveProgress(root, sessionId, {
      stationId: "ego-001",
      phase: DERIVE_PROGRESS_PHASES.EXTRACT,
      done: 2,
      total: 5,
    });
    const loaded = readDeriveProgress(root, sessionId);
    assert.equal(loaded.phase, DERIVE_PROGRESS_PHASES.EXTRACT);
    assert.equal(loaded.done, 2);
    assert.equal(loaded.total, 5);
    assert.ok(loaded.updatedAt);
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("computes percent from done/total", () => {
    assert.equal(
      deriveProgressPercent({ phase: DERIVE_PROGRESS_PHASES.MUX_ENCODE, done: 3, total: 4 }),
      75,
    );
    assert.equal(deriveProgressPercent({ phase: DERIVE_PROGRESS_PHASES.READY, done: 1, total: 1 }), 100);
  });

  it("detects stale heartbeat", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "ego-progress-stale-"));
    const sessionId = "sess_stale";
    writeDeriveProgress(root, sessionId, { phase: DERIVE_PROGRESS_PHASES.MUX_MERGE, done: 0, total: 1 });
    const p = readDeriveProgress(root, sessionId);
    p.updatedAt = new Date(Date.now() - 2_000_000).toISOString();
    fs.writeFileSync(
      path.join(root, "live", "derive", "progress", `${sessionId}.json`),
      `${JSON.stringify(p, null, 2)}\n`,
    );
    assert.equal(isDeriveProgressStale(root, sessionId, { timeoutSec: 900 }), true);
    fs.rmSync(root, { recursive: true, force: true });
  });
});
