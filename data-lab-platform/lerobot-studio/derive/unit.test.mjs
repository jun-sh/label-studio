import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, it } from "node:test";

import { appendJournalEvent, rebuildManifestFromDisk, readJournal } from "./manifest.mjs";
import { episodeChunkPaths } from "./unit-paths.mjs";
import { buildUnitFrameMap } from "./unit.mjs";
import { chunkSortedIndices } from "./encode-pool.mjs";
import { hardlinkOrCopy } from "./publisher.mjs";
import { deriveLayout } from "./station-context.mjs";

function tmpRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ego-unit-test-"));
}

describe("P2 unit paths", () => {
  it("maps episode indices to chunk/file paths", () => {
    const p0 = episodeChunkPaths(0);
    assert.equal(p0.dataRel, "data/chunk-000/file-000.parquet");
    const p1001 = episodeChunkPaths(1001);
    assert.equal(p1001.chunkIndex, 1);
    assert.equal(p1001.fileIndex, 1);
  });
});

describe("P2 manifest", () => {
  it("appends journal events and rebuilds manifest from units", () => {
    const root = tmpRoot();
    const sessionA = "sess_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const sessionB = "sess_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const derived = path.join(root, "derived");
    for (const [sid, frames, started] of [
      [sessionA, 10, "2026-08-20T01:00:00Z"],
      [sessionB, 5, "2026-08-20T02:00:00Z"],
    ]) {
      const udir = path.join(derived, sid);
      fs.mkdirSync(udir, { recursive: true });
      fs.writeFileSync(
        path.join(udir, "unit.json"),
        `${JSON.stringify({
          session_id: sid,
          frames,
          derive: { started_at: started },
        })}\n`,
      );
    }
    appendJournalEvent(root, { event: "derive_started", session_id: sessionA, attempt: 1 });
    appendJournalEvent(root, { event: "unit_ready", session_id: sessionA, frames: 10 });
    assert.equal(readJournal(root).length, 2);
    const manifest = rebuildManifestFromDisk(root, "ego-001");
    assert.equal(manifest.episodes.length, 2);
    assert.equal(manifest.total_frames, 15);
    assert.equal(manifest.episodes[0].session_id, sessionA);
    assert.equal(manifest.episodes[1].from_index, 10);
  });
});

describe("P2 unit frame map", () => {
  it("builds local 0..N-1 indices for one session", () => {
    const map = buildUnitFrameMap("sess_x", [
      { segmentId: "seg_000001", frameCount: 3 },
      { segmentId: "seg_000002", frameCount: 2 },
    ]);
    assert.equal(map.length, 5);
    assert.equal(map.frame_segments[1].global_start, 3);
    assert.equal(map.frame_index_max, 4);
  });
});

describe("P2 encode pool", () => {
  it("chunks sorted frame indices", () => {
    const indices = Array.from({ length: 300 }, (_, i) => i);
    const chunks = chunkSortedIndices(indices, 128);
    assert.equal(chunks.length, 3);
    assert.equal(chunks[0].length, 128);
    assert.equal(chunks[2].length, 44);
  });
});

describe("P2 publisher", () => {
  it("hardlinkOrCopy duplicates file content", () => {
    const root = tmpRoot();
    const src = path.join(root, "src.txt");
    const dest = path.join(root, "nested", "dest.txt");
    fs.writeFileSync(src, "hello-unit");
    hardlinkOrCopy(src, dest);
    assert.equal(fs.readFileSync(dest, "utf8"), "hello-unit");
  });
});

describe("P2 layout flag", () => {
  it("defaults to unit", () => {
    const prev = process.env.DERIVE_LAYOUT;
    delete process.env.DERIVE_LAYOUT;
    assert.equal(deriveLayout(), "unit");
    process.env.DERIVE_LAYOUT = "legacy";
    assert.equal(deriveLayout(), "legacy");
    if (prev === undefined) delete process.env.DERIVE_LAYOUT;
    else process.env.DERIVE_LAYOUT = prev;
  });
});
