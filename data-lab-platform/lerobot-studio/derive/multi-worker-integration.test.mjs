import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Worker } from "node:worker_threads";
import { fileURLToPath } from "node:url";

import { SESSION_MARKERS, hasSessionMarker, markSessionReady, writeSessionMarker } from "../session-markers.mjs";
import { readDeriverLock, listActiveDeriverLocks } from "../derive-lock.mjs";
import { readSessionClaim } from "./session-claim.mjs";
import { kickDeriveSession } from "./watch-kick.mjs";

const workerScript = fileURLToPath(new URL("./watch-kick-worker.mjs", import.meta.url));

function makeRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ego-multi-worker-"));
}

function seedPending(root, sessionId) {
  fs.mkdirSync(path.join(root, "state", "sessions", sessionId), { recursive: true });
  writeSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD, { test: true });
}

function runKickWorker(root, stationId, workerId, sessionId) {
  return new Promise((resolve, reject) => {
    const worker = new Worker(workerScript, {
      workerData: { root, stationId, workerId, sessionId },
    });
    worker.on("message", resolve);
    worker.on("error", reject);
  });
}

describe("multi-worker integration (Q1-4)", () => {
  it("parallel workers derive distinct sessions without duplicate claims", async () => {
    const root = makeRoot();
    const stationId = "ego-test";
    const sessions = ["sess_mw_a", "sess_mw_b", "sess_mw_c"];
    for (const sid of sessions) seedPending(root, sid);

    const results = await Promise.all([
      runKickWorker(root, stationId, "derive-worker-1", "sess_mw_a"),
      runKickWorker(root, stationId, "derive-worker-2", "sess_mw_b"),
      runKickWorker(root, stationId, "derive-worker-3", "sess_mw_c"),
    ]);

    assert.ok(results.every((r) => r.ok), JSON.stringify(results));
    assert.deepEqual(
      results.map((r) => r.sessionId).sort(),
      sessions.sort(),
    );
    for (const sid of sessions) {
      assert.equal(hasSessionMarker(root, sid, SESSION_MARKERS.READY), true);
      assert.equal(readSessionClaim(root, sid), null);
    }
    assert.equal(listActiveDeriverLocks(root).length, 0);
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("two workers racing same session: only one succeeds", async () => {
    const root = makeRoot();
    const stationId = "ego-test";
    const sessionId = "sess_mw_race";
    seedPending(root, sessionId);

    const results = await Promise.all([
      kickDeriveSession(stationId, root, {
        workerId: "derive-worker-1",
        sessionId,
        runPipeline: async (_s, { sessionId: sid }) => {
          markSessionReady(root, sid, { total: 1, mp4Ok: true });
          return { ok: true, phase: "READY", sessionId: sid };
        },
      }),
      kickDeriveSession(stationId, root, {
        workerId: "derive-worker-2",
        sessionId,
        runPipeline: async (_s, { sessionId: sid }) => {
          markSessionReady(root, sid, { total: 1, mp4Ok: true });
          return { ok: true, phase: "READY", sessionId: sid };
        },
      }),
    ]);

    const winners = results.filter((r) => r.ok);
    assert.equal(winners.length, 1);
    fs.rmSync(root, { recursive: true, force: true });
  });
});
