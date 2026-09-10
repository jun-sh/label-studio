import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  acquireDeriverLock,
  isDeriverLockActive,
  isDeriverLockHeldByOther,
  listActiveDeriverLocks,
  readDeriverLock,
  releaseDeriverLock,
  renewDeriverLock,
} from "../derive-lock.mjs";

function makeRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ego-deriver-lock-"));
}

describe("derive lock (Q1-2 + Q1-4)", () => {
  it("writes workerId and leaseExpiresAt on acquire", () => {
    const root = makeRoot();
    const lock = acquireDeriverLock(root, {
      stationId: "ego-001",
      sessionId: "sess_a",
      workerId: "worker-test-1",
    });
    assert.equal(lock.workerId, "worker-test-1");
    assert.equal(lock.version, 2);
    assert.ok(lock.leaseExpiresAt);
    assert.ok(isDeriverLockActive(lock, { workerId: "worker-test-1" }));
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("allows parallel locks for different sessions", () => {
    const root = makeRoot();
    const lockA = acquireDeriverLock(root, { sessionId: "sess_a", workerId: "worker-a" });
    const lockB = acquireDeriverLock(root, { sessionId: "sess_b", workerId: "worker-b" });
    assert.equal(lockA.sessionId, "sess_a");
    assert.equal(lockB.sessionId, "sess_b");
    assert.equal(listActiveDeriverLocks(root).length, 2);
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("rejects acquire when another worker holds the same session lock", () => {
    const root = makeRoot();
    acquireDeriverLock(root, { sessionId: "sess_a", workerId: "worker-a" });
    assert.throws(
      () => acquireDeriverLock(root, { sessionId: "sess_a", workerId: "worker-b" }),
      (err) => err.code === "DERIVER_LOCKED",
    );
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("legacy pid-only lock is never active (container pid=1 reuse)", () => {
    const root = makeRoot();
    const p = path.join(root, "state", "deriver.lock");
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(
      p,
      `${JSON.stringify(
        {
          pid: 1,
          startedAt: new Date().toISOString(),
          sessionId: "sess_legacy",
          stationId: "ego-001",
        },
        null,
        2,
      )}\n`,
    );
    assert.equal(isDeriverLockActive(readDeriverLock(root)), false);
    const lock = acquireDeriverLock(root, { sessionId: "sess_new", workerId: "worker-new" });
    assert.equal(lock.version, 2);
    assert.equal(lock.workerId, "worker-new");
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("does not treat container pid=1 reuse as alive when lease expired", () => {
    const root = makeRoot();
    const p = path.join(root, "state", "sessions", "sess_orphan", "deriver.lock");
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(
      p,
      `${JSON.stringify(
        {
          version: 2,
          pid: 1,
          workerId: "worker-old",
          sessionId: "sess_orphan",
          startedAt: "2020-01-01T00:00:00.000Z",
          leaseExpiresAt: "2020-01-01T00:15:00.000Z",
        },
        null,
        2,
      )}\n`,
    );
    const stale = readDeriverLock(root, "sess_orphan");
    assert.equal(isDeriverLockActive(stale), false);
    assert.equal(isDeriverLockHeldByOther(root, "worker-new"), false);
    const lock = acquireDeriverLock(root, { sessionId: "sess_new", workerId: "worker-new" });
    assert.equal(lock.workerId, "worker-new");
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("renewDeriverLock extends lease for owner only", () => {
    const root = makeRoot();
    acquireDeriverLock(root, { sessionId: "sess_a", workerId: "worker-a", muxOnly: false });
    const before = readDeriverLock(root, "sess_a");
    const renewed = renewDeriverLock(root, { workerId: "worker-a", sessionId: "sess_a" });
    assert.equal(renewed.ok, true);
    assert.ok(Date.parse(renewed.lock.leaseExpiresAt) >= Date.parse(before.leaseExpiresAt));
    const denied = renewDeriverLock(root, { workerId: "worker-b", sessionId: "sess_a" });
    assert.equal(denied.ok, false);
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("releaseDeriverLock respects owner unless forced", () => {
    const root = makeRoot();
    acquireDeriverLock(root, { sessionId: "sess_a", workerId: "worker-a" });
    assert.equal(releaseDeriverLock(root, { sessionId: "sess_a", workerId: "worker-b" }).released, false);
    assert.equal(releaseDeriverLock(root, { sessionId: "sess_a", workerId: "worker-a" }).released, true);
    acquireDeriverLock(root, { sessionId: "sess_b", workerId: "worker-b" });
    assert.equal(releaseDeriverLock(root, { sessionId: "sess_b", force: true }).released, true);
    fs.rmSync(root, { recursive: true, force: true });
  });
});
