import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, it } from "node:test";

import { readJsonl } from "./io.mjs";
import { appendJournalEvent, rebuildManifestFromDisk, readJournal } from "./manifest.mjs";
import { episodeChunkPaths } from "./unit-paths.mjs";
import { appendH264AnnexBStream, buildUnitFrameMap, sliceAnnexBFromFirstIdr } from "./unit.mjs";
import { chunkSortedIndices } from "./encode-pool.mjs";
import { hardlinkOrCopy, publishUnitEpisode } from "./publisher.mjs";
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

describe("P2 unit h264 multi-segment", () => {
  it("appendH264AnnexBStream concatenates segment streams", () => {
    const root = tmpRoot();
    const dest = path.join(root, "front_left.h264");
    const seg1 = path.join(root, "seg1.h264");
    const seg2 = path.join(root, "seg2.h264");
    fs.writeFileSync(seg1, "segment1");
    fs.writeFileSync(seg2, "segment2");
    assert.equal(appendH264AnnexBStream(dest, seg1), true);
    assert.equal(appendH264AnnexBStream(dest, seg2), true);
    assert.equal(fs.readFileSync(dest, "utf8"), "segment1segment2");
  });

  it("sliceAnnexBFromFirstIdr drops leading P-slice prefix", () => {
    const sps = Buffer.from([0, 0, 0, 1, 0x67, 0x01]);
    const pps = Buffer.from([0, 0, 0, 1, 0x68, 0x01]);
    const idr = Buffer.from([0, 0, 0, 1, 0x65, 0x99]);
    const pframe = Buffer.from([0, 0, 0, 1, 0x41, 0x01]);
    const seg = Buffer.concat([pframe, sps, pps, idr, Buffer.from([0, 0, 0, 1, 0x41, 0x02])]);
    const sliced = sliceAnnexBFromFirstIdr(seg);
    assert.ok(sliced.indexOf(pframe) < 0);
    assert.ok(sliced.indexOf(idr) >= 0);
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

  it("publishUnitEpisode rewrites episode_index in published table", () => {
    const root = tmpRoot();
    const stationId = "ego-001";
    const sessionId = "sess_publish_ep_idx_test00000001";
    const unitRoot = path.join(root, "derived", sessionId);
    fs.mkdirSync(path.join(root, "meta"), { recursive: true });
    fs.writeFileSync(path.join(root, "meta", "info.json"), `${JSON.stringify({ features: {} }, null, 2)}\n`);
    fs.mkdirSync(path.join(unitRoot, "videos"), { recursive: true });
    const rows = [
      { frame_index: 0, episode_index: 0, task_index: 0 },
      { frame_index: 1, episode_index: 0, task_index: 0 },
    ];
    fs.writeFileSync(path.join(unitRoot, "data.jsonl"), `${rows.map((r) => JSON.stringify(r)).join("\n")}\n`);
    fs.writeFileSync(
      path.join(unitRoot, "unit.json"),
      `${JSON.stringify({ session_id: sessionId, frames: 2 }, null, 2)}\n`,
    );
    for (const key of [
      "observation.images.camera_front_left",
      "observation.images.camera_front_right",
      "observation.images.camera_rear_left",
      "observation.images.camera_rear_right",
    ]) {
      fs.writeFileSync(path.join(unitRoot, "videos", `${key}.mp4`), "not-a-real-mp4");
    }
    publishUnitEpisode(root, stationId, sessionId, 3);
    const published = readJsonl(path.join(root, "data/chunk-000/file-003.jsonl"));
    assert.equal(published.length, 2);
    assert.ok(published.every((r) => r.episode_index === 3));
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
