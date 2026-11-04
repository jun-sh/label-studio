import assert from "node:assert/strict";
import { describe, it } from "node:test";

function parseEpisodeIndex(label) {
  const m = String(label || "").match(/(?:选择 Episode|Select Episode)\s*(\d+)/i);
  return m ? parseInt(m[1], 10) : null;
}

function isRowDeleteTitle(title) {
  return /delete episode|删除 episode|删除 Episode|エピソードを削除/i.test(String(title || "").trim());
}

function isBulkDeleteLabel(label) {
  return /^(批量删除|Delete selected|選択を削除)$/i.test(String(label || "").replace(/\s+/g, " ").trim());
}

describe("overlay-episode-delete helpers", () => {
  it("parseEpisodeIndex from zh/en aria labels", () => {
    assert.equal(parseEpisodeIndex("选择 Episode 12 foo"), 12);
    assert.equal(parseEpisodeIndex("Select Episode 31"), 31);
    assert.equal(parseEpisodeIndex("other"), null);
  });

  it("isRowDeleteTitle matches LeRobot i18n", () => {
    assert.equal(isRowDeleteTitle("删除 Episode"), true);
    assert.equal(isRowDeleteTitle("Delete episode"), true);
    assert.equal(isRowDeleteTitle("Edit task"), false);
  });

  it("isBulkDeleteLabel", () => {
    assert.equal(isBulkDeleteLabel("批量删除"), true);
    assert.equal(isBulkDeleteLabel("Delete selected"), true);
    assert.equal(isBulkDeleteLabel("删除"), false);
  });
});
