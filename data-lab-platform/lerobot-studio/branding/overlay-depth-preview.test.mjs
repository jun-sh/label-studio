import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  DISPLAY_PANEL_LABEL,
  DISPLAY_VIDEO_KEY,
  containedVideoLayout,
  displayVideoKeyCandidates,
  featureMatchesText,
  frameUrl,
  resolveEpisodeEntry,
  resolveVideoKeyCandidates,
} from "./overlay-depth-preview-lib.mjs";

describe("overlay-depth-preview-lib", () => {
  it("builds frame URLs from template", () => {
    const payload = {
      frame_url_template:
        "/lerobot/api/sample/ego_214_hand_pose/depth-preview/{episode}/frame_{frame:06d}.png",
      episodes: { "000000": { frames: 10 } },
    };
    const url = frameUrl(payload, "000000", 42);
    assert.equal(
      url,
      "/lerobot/api/sample/ego_214_hand_pose/depth-preview/000000/frame_000042.png",
    );
  });

  it("resolves padded episode keys", () => {
    const payload = { episodes: { "000001": { frames: 3 } } };
    const resolved = resolveEpisodeEntry(payload, "1");
    assert.equal(resolved.episodeKey, "000001");
  });

  it("rejects shared layout text that lists multiple cameras", () => {
    const text =
      "camera_front_left camera_front_right camera_rear_left camera_rear_right";
    assert.equal(
      featureMatchesText(text, "observation.images.camera_front_right"),
      false,
    );
    assert.equal(
      featureMatchesText(text, "observation.images.camera_front_left"),
      false,
    );
  });

  it("accepts panel text for a single camera", () => {
    const text = "observation.images.camera_front_right";
    assert.equal(featureMatchesText(text, DISPLAY_VIDEO_KEY), true);
    assert.equal(featureMatchesText(DISPLAY_PANEL_LABEL, DISPLAY_VIDEO_KEY), true);
  });

  it("includes legacy head_right aliases for display slot lookup", () => {
    const keys = resolveVideoKeyCandidates(DISPLAY_VIDEO_KEY);
    assert.ok(keys.includes("observation.images.camera_front_right"));
    assert.ok(keys.includes("observation.images.camera_head_right"));
  });

  it("displayVideoKeyCandidates defaults to front_right", () => {
    const keys = displayVideoKeyCandidates({});
    assert.equal(keys[0], DISPLAY_VIDEO_KEY);
  });

  it("computes letterboxed video layout", () => {
    const rect = containedVideoLayout(1280, 800, 640, 400);
    assert.ok(rect);
    assert.equal(rect.width, 640);
    assert.equal(rect.height, 400);
    assert.equal(rect.offsetX, 0);
    assert.equal(rect.offsetY, 0);
  });
});
