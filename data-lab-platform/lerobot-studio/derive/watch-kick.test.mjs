import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Worker } from "node:worker_threads";
import { fileURLToPath } from "node:url";

import { SESSION_MARKERS, hasSessionMarker, writeSessionMarker, markSessionReady } from "../session-markers.mjs";
import { readDeriverLock } from "../derive-lock.mjs";
import { readSessionClaim } from "./session-claim.mjs";
import { kickDeriveSession } from "./watch-kick.mjs";

const workerScript = fileURLToPath(new URL("./watch-kick-worker.mjs", import.meta.url));

function makeRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ego-watch-kick-"));
}

function seedPending(root, sessionId) {
  fs.mkdirSync(path.join(root, "state", "sessions", sessionId), { recursive: true });
  writeSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD, { test: true });
}

function runKickInWorker(root, stationId, workerId, sessionId) {
  return new Promise((resolve, reject) => {
    const worker = new Worker(workerScript, {
      workerData: { root, stationId, workerId, sessionId },
    });
    worker.on("message", resolve);
    worker.on("error", reject);
  });
}

describe("watch kick (Q1-3)", () => {
  it("runs claim → lock → deriving → pipeline → release claim", async () => {
    const root = makeRoot();
    const stationId = "ego-test";
    const sessionId = "sess_kick_flow";
    seedPending(root, sessionId);

    const phases = [];
    const result = await kickDeriveSession(stationId, root, {
      workerId: "worker-a",
      sessionId,
      runPipeline: async (_stationId, { sessionId: sid }) => {
        phases.push("pipeline");
        assert.equal(hasSessionMarker(root, sid, SESSION_MARKERS.DERIVING), true);
        assert.ok(readDeriverLock(root, sid)?.workerId, "worker-a");
        assert.ok(readSessionClaim(root, sid)?.workerId, "worker-a");
        markSessionReady(root, sid, { total: 1, mp4Ok: true, source: "watch_kick_test" });
        return { ok: true, phase: "READY", sessionId: sid };
      },
    });

    assert.equal(result.ok, true);
    assert.deepEqual(phases, ["pipeline"]);
    assert.equal(readSessionClaim(root, sessionId), null);
    assert.equal(readDeriverLock(root, sessionId), null);
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("releases claim when lock acquire fails", async () => {
    const root = makeRoot();
    const stationId = "ego-test";
    const sessionId = "sess_lock_fail";
    seedPending(root, sessionId);
    fs.mkdirSync(path.join(root, "state", "sessions", sessionId), { recursive: true });
    fs.writeFileSync(
      path.join(root, "state", "sessions", sessionId, "deriver.lock"),
      `${JSON.stringify(
        {
          version: 2,
          workerId: "worker-other",
          sessionId,
          startedAt: new Date().toISOString(),
          leaseExpiresAt: new Date(Date.now() + 600_000).toISOString(),
          pid: 999999,
        },
        null,
        2,
      )}\n`,
    );

    const result = await kickDeriveSession(stationId, root, {
      workerId: "worker-a",
      sessionId,
      runPipeline: async () => ({ ok: true, phase: "READY" }),
    });

    assert.equal(result.ok, false);
    assert.equal(result.reason, "lock_failed");
    assert.equal(readSessionClaim(root, sessionId), null);
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("concurrent multi-worker kick: only one wins the same session", async () => {
    const root = makeRoot();
    const stationId = "ego-test";
    const sessionId = "sess_multi_worker";
    seedPending(root, sessionId);
    const workerIds = ["worker-1", "worker-2", "worker-3", "worker-4"];

    const results = await Promise.all(
      workerIds.map((workerId) => runKickInWorker(root, stationId, workerId, sessionId)),
    );

    const winners = results.filter((r) => r.ok);
    assert.equal(winners.length, 1, JSON.stringify(results));
    assert.equal(winners[0].sessionId, sessionId);

    const losers = results.filter((r) => !r.ok);
    assert.equal(losers.length, workerIds.length - 1);
    assert.ok(
      losers.every((r) =>
        ["claim_failed", "lock_failed", "lock_active", "no_pending"].includes(r.reason),
      ),
      JSON.stringify(losers),
    );

    assert.equal(readSessionClaim(root, sessionId), null);
    assert.equal(readDeriverLock(root, sessionId), null);
    fs.rmSync(root, { recursive: true, force: true });
  });
});
