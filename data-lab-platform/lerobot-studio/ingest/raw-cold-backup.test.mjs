import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const cold = fs.mkdtempSync(path.join(os.tmpdir(), "cold-"));
process.env.STREAM_COLD_ARCHIVE_ROOT = cold;
const { backupRawMcapToCold, coldBackupDestination, coldBackupMarkerPath } = await import("./raw-cold-backup.mjs");

describe("backupRawMcapToCold", () => {
  it("copies, verifies sha and is idempotent", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "hot-"));
    const src = path.join(root, "raw", "segments", "sess_a", "seg_000001.mcap");
    fs.mkdirSync(path.dirname(src), { recursive: true });
    fs.writeFileSync(src, Buffer.alloc(4096, 7));

    const first = await backupRawMcapToCold("ego-t", root, "sess_a", "seg_000001", src);
    assert.equal(first.skipped, false);
    const dest = coldBackupDestination("ego-t", root, src);
    assert.equal(dest, path.join(cold, "ego-t", "raw", "segments", "sess_a", "seg_000001.mcap"));
    assert.deepEqual(fs.readFileSync(dest), fs.readFileSync(src));
    assert.ok(fs.existsSync(coldBackupMarkerPath(dest)));
    assert.equal(fs.existsSync(`${dest}.partial`), false);

    const second = await backupRawMcapToCold("ego-t", root, "sess_a", "seg_000001", src);
    assert.equal(second.skipped, true);
  });
});
