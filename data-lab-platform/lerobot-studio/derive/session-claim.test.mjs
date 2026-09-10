import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Worker } from "node:worker_threads";
import { fileURLToPath } from "node:url";

import { SESSION_MARKERS, listSessionsPendingDerive, writeSessionMarker } from "../session-markers.mjs";
import {
  claimSession,
  clearSessionClaim,
  isSessionClaimActive,
  isSessionClaimedByOther,
  readSessionClaim,
  releaseSessionClaim,
  renewSessionClaim,
  sessionClaimPath,
} from "./session-claim.mjs";

const workerScript = fileURLToPath(new URL("./session-claim-worker.mjs", import.meta.url));

function makeStationRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ego-claim-"));
}

function seedPendingSession(root, sessionId) {
  fs.mkdirSync(path.join(root, "state", "sessions", sessionId), { recursive: true });
  writeSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD, { test: true });
}

function runClaimInWorker(root, sessionId, workerId, leaseSec = 600) {
  return new Promise((resolve, reject) => {
    const worker = new Worker(workerScript, {
      workerData: { root, sessionId, workerId, leaseSec },
    });
    worker.on("message", resolve);
    worker.on("error", reject);
    worker.on("exit", (code) => {
      if (code !== 0) reject(new Error(`claim worker exited ${code}`));
    });
  });
}

describe("session claim (Q1-1)", () => {
  it("claims exclusively with wx create", () => {
    const root = makeStationRoot();
    const sessionId = "sess_claim_a";
    const first = claimSession(root, sessionId, "worker-a", { leaseSec: 120 });
    assert.equal(first.ok, true);
    assert.equal(first.claim.workerId, "worker-a");
    assert.ok(isSessionClaimActive(first.claim));
    assert.ok(fs.existsSync(sessionClaimPath(root, sessionId)));

    const second = claimSession(root, sessionId, "worker-b", { leaseSec: 120 });
    assert.equal(second.ok, false);
    assert.equal(second.reason, "claimed_by_other");
    assert.equal(second.claim.workerId, "worker-a");

    fs.rmSync(root, { recursive: true, force: true });
  });

  it("same worker renews an active claim", () => {
    const root = makeStationRoot();
    const sessionId = "sess_renew";
    const first = claimSession(root, sessionId, "worker-a", { leaseSec: 120 });
    const renewed = renewSessionClaim(root, sessionId, "worker-a", { leaseSec: 240 });
    assert.equal(renewed.ok, true);
    assert.ok(Date.parse(renewed.claim.leaseExpiresAt) > Date.parse(first.claim.leaseExpiresAt));
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("reclaims expired claim for a new worker", () => {
    const root = makeStationRoot();
    const sessionId = "sess_expired";
    const claimPath = sessionClaimPath(root, sessionId);
    fs.mkdirSync(path.dirname(claimPath), { recursive: true });
    fs.writeFileSync(
      claimPath,
      `${JSON.stringify(
        {
          version: 1,
          sessionId,
          workerId: "worker-old",
          claimedAt: "2020-01-01T00:00:00.000Z",
          leaseExpiresAt: "2020-01-01T00:10:00.000Z",
          pid: 1,
        },
        null,
        2,
      )}\n`,
    );
    const next = claimSession(root, sessionId, "worker-new", { leaseSec: 120 });
    assert.equal(next.ok, true);
    assert.equal(next.claim.workerId, "worker-new");
    assert.equal(next.reclaimed, true);
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("releaseSessionClaim is owner-only", () => {
    const root = makeStationRoot();
    const sessionId = "sess_release";
    claimSession(root, sessionId, "worker-a", { leaseSec: 120 });
    const denied = releaseSessionClaim(root, sessionId, "worker-b");
    assert.equal(denied.ok, false);
    assert.equal(denied.reason, "not_owner");
    const ok = releaseSessionClaim(root, sessionId, "worker-a");
    assert.equal(ok.ok, true);
    assert.equal(readSessionClaim(root, sessionId), null);
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("listSessionsPendingDerive skips sessions with active foreign claims", () => {
    const root = makeStationRoot();
    seedPendingSession(root, "sess_open");
    seedPendingSession(root, "sess_claimed");
    claimSession(root, "sess_claimed", "worker-a", { leaseSec: 600 });
    const pending = listSessionsPendingDerive(root);
    assert.deepEqual(pending, ["sess_open"]);
    assert.equal(isSessionClaimedByOther(root, "sess_claimed", "worker-b"), true);
    assert.equal(isSessionClaimedByOther(root, "sess_open", "worker-b"), false);
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("concurrent claim: exactly one worker wins", async () => {
    const root = makeStationRoot();
    const sessionId = "sess_race";
    seedPendingSession(root, sessionId);
    const workerIds = ["worker-1", "worker-2", "worker-3", "worker-4", "worker-5"];
    const results = await Promise.all(workerIds.map((workerId) => runClaimInWorker(root, sessionId, workerId)));

    const winners = results.filter((r) => r.ok);
    assert.equal(winners.length, 1, `expected 1 winner, got ${JSON.stringify(results)}`);
    const losers = results.filter((r) => !r.ok);
    assert.equal(losers.length, workerIds.length - 1);
    assert.ok(losers.every((r) => r.reason === "claimed_by_other"));
    assert.equal(listSessionsPendingDerive(root).length, 0);
    clearSessionClaim(root, sessionId);
    assert.deepEqual(listSessionsPendingDerive(root), [sessionId]);
    fs.rmSync(root, { recursive: true, force: true });
  });
});
