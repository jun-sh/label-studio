import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import {
  SEGMENT_INGEST_STATUS,
  readSegmentState,
} from "./segment-state.mjs";
import { rawMcapArchivePath, rawSegmentArchivePath } from "./io.mjs";
import { ingestMcapArchive } from "./receive-mcap.mjs";
import { validateMcapArchive } from "./mcap-validator.mjs";
import { hasSessionMarker } from "../session-markers.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const GOLDEN = path.join(__dirname, "..", "..", "fixtures", "mcap", "golden-seg.mcap");

function tmpRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ego-mcap-ingest-"));
}

function zstdPack(src, dest) {
  const res = spawnSync("zstd", ["-o", dest, src]);
  assert.equal(res.status, 0, "zstd pack failed");
}

describe("mcap ingest", () => {
  it("validates golden fixture", async () => {
    if (!fs.existsSync(GOLDEN)) {
      return;
    }
    const result = await validateMcapArchive(GOLDEN);
    assert.equal(result.ok, true, JSON.stringify(result.issues));
    assert.ok(result.topics["/ego/camera/front_left"] >= 1);
  });

  it("ingests mcap.zst archive to raw/segments", async () => {
    if (!fs.existsSync(GOLDEN)) {
      return;
    }
    const stationId = "ego-mcap-pilot";
    const stationRootDir = tmpRoot();
    const prev = process.env.STREAM_DATA_ROOT;
    process.env.STREAM_DATA_ROOT = stationRootDir;
    const root = path.join(stationRootDir, stationId);
    fs.mkdirSync(root, { recursive: true });

    const sessionId = "sess_golden_fixture";
    const segmentId = "seg_000001";
    const work = tmpRoot();
    const archivePath = path.join(work, `${segmentId}.mcap.zst`);
    zstdPack(GOLDEN, archivePath);

    try {
      const out = await ingestMcapArchive(stationId, {
        archivePath,
        sessionId,
        segmentId,
        expectedSegmentTotal: 1,
        source: "test",
      });
      assert.equal(out.status, SEGMENT_INGEST_STATUS.DERIVE_PENDING);
      assert.equal(out.sourceFormat, "mcap");
      const rawPath = rawMcapArchivePath(root, sessionId, segmentId);
      assert.ok(fs.existsSync(rawPath));
      const state = readSegmentState(root, sessionId, segmentId);
      assert.equal(state.status, SEGMENT_INGEST_STATUS.DERIVE_PENDING);
      assert.equal(state.sourceFormat, "mcap");
      assert.ok(Array.isArray(state.mcapTopics));
      assert.equal(hasSessionMarker(root, sessionId, "session.DONE_UPLOAD"), true);
    } finally {
      if (prev === undefined) delete process.env.STREAM_DATA_ROOT;
      else process.env.STREAM_DATA_ROOT = prev;
    }
  });

  it("purges stale tar.zst when mcap ingest succeeds", async () => {
    if (!fs.existsSync(GOLDEN)) {
      return;
    }
    const stationId = "ego-mcap-track2";
    const stationRootDir = tmpRoot();
    const prev = process.env.STREAM_DATA_ROOT;
    process.env.STREAM_DATA_ROOT = stationRootDir;
    const root = path.join(stationRootDir, stationId);
    fs.mkdirSync(root, { recursive: true });

    const sessionId = "sess_tar_purge";
    const segmentId = "seg_000001";
    const work = tmpRoot();
    const archivePath = path.join(work, `${segmentId}.mcap.zst`);
    zstdPack(GOLDEN, archivePath);
    const tarStale = rawSegmentArchivePath(root, sessionId, segmentId);
    fs.mkdirSync(path.dirname(tarStale), { recursive: true });
    fs.writeFileSync(tarStale, "stale-tar-artifact");

    try {
      await ingestMcapArchive(stationId, {
        archivePath,
        sessionId,
        segmentId,
        expectedSegmentTotal: 1,
        source: "test",
      });
      assert.equal(fs.existsSync(tarStale), false, "stale tar should be purged");
      assert.ok(fs.existsSync(rawMcapArchivePath(root, sessionId, segmentId)));
    } finally {
      if (prev === undefined) delete process.env.STREAM_DATA_ROOT;
      else process.env.STREAM_DATA_ROOT = prev;
    }
  });
});
