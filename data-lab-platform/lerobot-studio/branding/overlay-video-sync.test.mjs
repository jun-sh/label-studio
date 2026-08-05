import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { frameIndexFromVideoTime } from "./overlay-video-sync.mjs";

describe("overlay-video-sync", () => {
  it("maps video time to frame index at fps", () => {
    assert.equal(frameIndexFromVideoTime({ currentTime: 0 }, 30), 0);
    assert.equal(frameIndexFromVideoTime({ currentTime: 1 / 30 }, 30), 1);
    assert.equal(frameIndexFromVideoTime({ currentTime: 19.7 }, 30), 591);
  });
});
