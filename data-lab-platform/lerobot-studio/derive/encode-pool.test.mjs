import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  deriveMuxMergeMode,
  chunkSortedIndices,
} from "./encode-pool.mjs";
import { writeMp4PathsConcatList } from "../mux-exec.mjs";

describe("encode-pool M1 merge mode", () => {
  const prev = process.env.DERIVE_MUX_MERGE_MODE;

  afterEach(() => {
    if (prev === undefined) delete process.env.DERIVE_MUX_MERGE_MODE;
    else process.env.DERIVE_MUX_MERGE_MODE = prev;
  });

  it("defaults to oneshot merge mode", () => {
    delete process.env.DERIVE_MUX_MERGE_MODE;
    assert.equal(deriveMuxMergeMode(), "oneshot");
  });

  it("supports pairwise rollback mode", () => {
    process.env.DERIVE_MUX_MERGE_MODE = "pairwise";
    assert.equal(deriveMuxMergeMode(), "pairwise");
  });
});

describe("writeMp4PathsConcatList", () => {
  it("writes ffconcat with all chunk paths", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ego-concat-"));
    const listPath = path.join(tmp, "all.concat.txt");
    writeMp4PathsConcatList(["/tmp/a.mp4", "/tmp/b.mp4", "/tmp/c.mp4"], listPath);
    const text = fs.readFileSync(listPath, "utf8");
    assert.match(text, /ffconcat version 1\.0/);
    assert.match(text, /file '\/tmp\/a\.mp4'/);
    assert.match(text, /file '\/tmp\/b\.mp4'/);
    assert.match(text, /file '\/tmp\/c\.mp4'/);
    fs.rmSync(tmp, { recursive: true, force: true });
  });
});

describe("chunkSortedIndices", () => {
  it("splits indices into fixed-size chunks", () => {
    const indices = Array.from({ length: 10 }, (_, i) => i);
    const chunks = chunkSortedIndices(indices, 4);
    assert.equal(chunks.length, 3);
    assert.deepEqual(chunks[0], [0, 1, 2, 3]);
    assert.deepEqual(chunks[2], [8, 9]);
  });
});
