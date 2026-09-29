#!/usr/bin/env node
/**
 * Smoke: MaxInsights-aligned perception module registry + delivery-status shape.
 */
import assert from "node:assert/strict";

import { loadPerceptionModuleConfig, getPerceptionModuleStatus } from "../perception-modules.mjs";

function testConfigShape() {
  const config = loadPerceptionModuleConfig();
  assert.equal(config.schema_version, "maxinsights_aligned@1.0");
  assert.equal(config.modules.length, 9, "expected 9 perception modules");
  assert.equal(config.production_loop.length, 5, "expected 5 production loop steps");
  const ids = config.modules.map((m) => m.id);
  assert.ok(ids.includes("hand_poses"));
  assert.ok(ids.includes("localization"));
  console.log("✓ config shape");
}

function testModuleProbe() {
  const rows = getPerceptionModuleStatus({
    root: "/tmp/unused",
    stationId: "ego-001",
    sessionId: "sess_test",
    slug: "ego_001",
    preview_ready: true,
    pose_ready: false,
    qc_approved: false,
    features: { "observation.head_pose": {} },
  });
  assert.equal(rows.length, 9);
  const video = rows.find((r) => r.id === "video_understanding");
  assert.equal(video.status, "active");
  const tactile = rows.find((r) => r.id === "tactile_force");
  assert.equal(tactile.status, "skipped");
  const tracking = rows.find((r) => r.id === "object_tracking");
  assert.equal(tracking.status, "planned");
  console.log("✓ module probe");
}

testConfigShape();
testModuleProbe();
console.log("\nMaxInsights pipeline smoke OK");
