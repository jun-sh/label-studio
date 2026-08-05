import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  BONE_COLOR,
  FINGER_JOINT_COLORS,
  HAND_ID_LABELS,
  HAND_MASK_COLORS,
  buildRichHandDrawPlan,
  convexHull,
  expandBbox,
  scalePointsToCanvas,
} from "./overlay-hand-render-rich.mjs";

function squarePts(size, offsetX = 0, offsetY = 0) {
  return [
    { x: offsetX, y: offsetY },
    { x: offsetX + size, y: offsetY },
    { x: offsetX + size, y: offsetY + size },
    { x: offsetX, y: offsetY + size },
    { x: offsetX + size / 2, y: offsetY + size / 2 },
  ];
}

describe("overlay-hand-render-rich", () => {
  it("exports SenseXperience finger colors (thumb white, pinky black)", () => {
    assert.deepEqual(FINGER_JOINT_COLORS[1], [255, 255, 255]);
    assert.deepEqual(FINGER_JOINT_COLORS[5], [255, 0, 0]);
    assert.deepEqual(FINGER_JOINT_COLORS[9], [0, 200, 0]);
    assert.deepEqual(FINGER_JOINT_COLORS[13], [0, 80, 255]);
    assert.deepEqual(FINGER_JOINT_COLORS[17], [0, 0, 0]);
    assert.deepEqual(BONE_COLOR, [255, 255, 255]);
  });

  it("exports mask colors and ID labels", () => {
    assert.equal(HAND_MASK_COLORS.left[3], 0.35);
    assert.equal(HAND_MASK_COLORS.right[0], 59);
    assert.equal(HAND_ID_LABELS.left, "ID:1 L");
    assert.equal(HAND_ID_LABELS.right, "ID:2 R");
  });

  it("convexHull returns monotone hull for scattered points", () => {
    const hull = convexHull(squarePts(100));
    assert.equal(hull.length, 4);
    const xs = hull.map((p) => p.x).sort((a, b) => a - b);
    assert.deepEqual(xs, [0, 0, 100, 100]);
  });

  it("expandBbox pads bbox by ratio and minimum px", () => {
    const expanded = expandBbox({ x: 100, y: 100, width: 200, height: 100 }, 4, 0.03);
    assert.ok(expanded.x < 100);
    assert.ok(expanded.y < 100);
    assert.ok(expanded.width > 200);
    assert.ok(expanded.height > 100);
  });

  it("scalePointsToCanvas maps flat kp2d to canvas coordinates", () => {
    const flat = Array(42).fill(0);
    flat[0] = 10;
    flat[1] = 20;
    flat[2] = 30;
    flat[3] = 40;
    const pts = scalePointsToCanvas(flat, 2, 2);
    assert.equal(pts[0].x, 20);
    assert.equal(pts[0].y, 40);
    assert.equal(pts[0].ok, true);
    assert.equal(pts[1].ok, true);
  });

  it("buildRichHandDrawPlan includes mask, bbox, bones, joints, label", () => {
    const flat = Array(42).fill(0);
    for (let j = 0; j < 21; j += 1) {
      flat[j * 2] = 100 + j * 8;
      flat[j * 2 + 1] = 200 + j * 4;
    }
    const pts = scalePointsToCanvas(flat, 1, 1);
    const plan = buildRichHandDrawPlan(pts, "left", 1, 0.9, 1);
    assert.equal(plan.side, "left");
    assert.equal(plan.handId, 1);
    assert.equal(plan.labelText, "ID:1 L");
    assert.ok(plan.maskPath.length >= 3);
    assert.ok(plan.bbox.width > 0);
    assert.ok(plan.edges.length > 0);
    assert.equal(plan.joints.length, 21);
    assert.ok(plan.styles.maskFill.includes("rgba"));
    assert.ok(plan.styles.boneColor.includes("255"));
  });
});
