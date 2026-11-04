import assert from "node:assert/strict";
import { describe, it } from "node:test";

function parseEpisodeIndex(label) {
  const m = String(label || "").match(/(?:选择 Episode|Select Episode)\s*(\d+)/i);
  return m ? parseInt(m[1], 10) : null;
}

function isEpisodeSelectLabel(label) {
  return /^(选择 Episode|Select Episode)\s+\d+/i.test(label || "");
}

describe("overlay-episode-delete helpers", () => {
  it("parseEpisodeIndex from zh/en aria labels", () => {
    assert.equal(parseEpisodeIndex("选择 Episode 12 foo"), 12);
    assert.equal(parseEpisodeIndex("Select Episode 31"), 31);
    assert.equal(parseEpisodeIndex("other"), null);
  });

  it("isEpisodeSelectLabel", () => {
    assert.equal(isEpisodeSelectLabel("Select Episode 0"), true);
    assert.equal(isEpisodeSelectLabel("Edit"), false);
  });
});
