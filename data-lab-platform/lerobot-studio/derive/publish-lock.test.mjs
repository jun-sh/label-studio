import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { withPublishLock } from "./publish-lock.mjs";

function tmpRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "publish-lock-"));
}

describe("withPublishLock", () => {
  it("releases the lock after fn and after a throw", () => {
    const root = tmpRoot();
    assert.equal(withPublishLock(root, "w1", () => 7), 7);
    assert.throws(() => withPublishLock(root, "w1", () => { throw new Error("boom"); }), /boom/);
    assert.equal(fs.existsSync(path.join(root, ".locks", "publish.lock")), false);
  });

  it("rejects while a fresh lock is held", () => {
    const root = tmpRoot();
    withPublishLock(root, "w1", () => {
      assert.throws(() => withPublishLock(root, "w2", () => 1), (e) => e.code === "PUBLISH_LOCK_BUSY");
    });
  });

  it("takes over a stale lock left by a killed process", () => {
    const root = tmpRoot();
    const lp = path.join(root, ".locks", "publish.lock");
    fs.mkdirSync(path.dirname(lp), { recursive: true });
    fs.writeFileSync(lp, "1\ndead\n0\n");
    const old = new Date(Date.now() - 3600_000);
    fs.utimesSync(lp, old, old);
    assert.equal(withPublishLock(root, "w2", () => "ok"), "ok");
  });
});
