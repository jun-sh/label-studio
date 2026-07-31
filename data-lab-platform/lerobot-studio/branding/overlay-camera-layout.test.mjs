import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  DISPLAY_PANEL_LABEL,
  DISPLAY_VIDEO_KEY,
} from "./overlay-depth-preview-lib.mjs";
import {
  applyEgoCameraLayoutFix,
  cameraOrdersEqual,
  DEPTH_LEFT_VIDEO_KEY,
  EGO_OAK_4P_CAMERA_ORDER,
  EGO_OAK_4P_DEPTH_LEFT_ROW_MAJOR,
  EGO_OAK_4P_WRONG_ROW_MAJOR,
  matchCameraFromBlob,
  needsEgoCameraLayoutFix,
  rowMajorCameraOrder,
  swapDomNodes,
} from "./overlay-camera-layout-lib.mjs";

describe("overlay-camera-layout-lib", () => {
  it("detects wrong row-major ego layout", () => {
    assert.equal(needsEgoCameraLayoutFix(EGO_OAK_4P_WRONG_ROW_MAJOR), true);
    assert.equal(needsEgoCameraLayoutFix(EGO_OAK_4P_DEPTH_LEFT_ROW_MAJOR), true);
    assert.equal(needsEgoCameraLayoutFix(EGO_OAK_4P_CAMERA_ORDER), false);
  });

  it("sorts panels row-major by bounding rect", () => {
    const order = rowMajorCameraOrder([
      { cam: "observation.images.camera_front_left", top: 0, left: 0 },
      { cam: "observation.images.camera_rear_left", top: 0, left: 400 },
      { cam: "observation.images.camera_front_right", top: 200, left: 0 },
      { cam: "observation.images.camera_rear_right", top: 200, left: 400 },
    ]);
    assert.deepEqual(order, EGO_OAK_4P_WRONG_ROW_MAJOR);
  });

  function mockNode(name) {
    const node = {
      name,
      parentNode: null,
      nextSibling: null,
    };
    return node;
  }

  function mockParent(children) {
    const parent = {
      children: [...children],
      insertBefore(child, ref) {
        const idx = this.children.indexOf(child);
        if (idx >= 0) this.children.splice(idx, 1);
        const refIdx = ref ? this.children.indexOf(ref) : this.children.length;
        this.children.splice(refIdx, 0, child);
        child.parentNode = this;
        for (let i = 0; i < this.children.length; i += 1) {
          this.children[i].nextSibling = this.children[i + 1] || null;
        }
      },
      replaceChild(newChild, oldChild) {
        const idx = this.children.indexOf(oldChild);
        if (idx < 0) return oldChild;
        if (newChild.parentNode && newChild.parentNode !== this) {
          const other = newChild.parentNode;
          const otherIdx = other.children.indexOf(newChild);
          if (otherIdx >= 0) other.children.splice(otherIdx, 1);
        }
        const dupIdx = this.children.indexOf(newChild);
        if (dupIdx >= 0 && dupIdx !== idx) this.children.splice(dupIdx, 1);
        const adjIdx = this.children.indexOf(oldChild);
        this.children[adjIdx] = newChild;
        oldChild.parentNode = null;
        newChild.parentNode = this;
        for (let i = 0; i < this.children.length; i += 1) {
          this.children[i].nextSibling = this.children[i + 1] || null;
        }
        if (newChild.parentNode !== this && newChild.parentNode) {
          for (let i = 0; i < newChild.parentNode.children.length; i += 1) {
            newChild.parentNode.children[i].nextSibling =
              newChild.parentNode.children[i + 1] || null;
          }
        }
        return oldChild;
      },
    };
    for (const child of children) child.parentNode = parent;
    for (let i = 0; i < children.length; i += 1) {
      children[i].nextSibling = children[i + 1] || null;
    }
    return parent;
  }

  it("swaps front_right and rear_left groupviews across parents", () => {
    const fl = mockNode("fl");
    const fr = mockNode("fr");
    const rl = mockNode("rl");
    const rr = mockNode("rr");
    const left = mockParent([fl, fr]);
    const right = mockParent([rl, rr]);

    const panels = [
      { cam: "observation.images.camera_front_left", node: fl, top: 0, left: 0 },
      { cam: "observation.images.camera_rear_left", node: rl, top: 0, left: 400 },
      { cam: "observation.images.camera_front_right", node: fr, top: 200, left: 0 },
      { cam: "observation.images.camera_rear_right", node: rr, top: 200, left: 400 },
    ];

    const result = applyEgoCameraLayoutFix(panels);
    assert.equal(result.ok, true);
    assert.equal(result.changed, true);
    assert.deepEqual(left.children.map((n) => n.name), ["fl", "rl"]);
    assert.deepEqual(right.children.map((n) => n.name), ["fr", "rr"]);
  });

  it("swapDomNodes works for sibling nodes", () => {
    const a = mockNode("a");
    const b = mockNode("b");
    const parent = mockParent([a, b]);
    swapDomNodes(a, b);
    assert.deepEqual(parent.children.map((n) => n.name), ["b", "a"]);
  });

  it("matchCameraFromBlob maps depth_front_left label to front_right slot", () => {
    assert.equal(matchCameraFromBlob("depth_front_left"), DISPLAY_VIDEO_KEY);
    assert.equal(
      matchCameraFromBlob("observation.images.camera_front_left"),
      "observation.images.camera_front_left",
    );
  });

  it("matchCameraFromBlob maps camera_depth_left to rear_left slot", () => {
    assert.equal(
      matchCameraFromBlob("observation.images.camera_depth_left"),
      "observation.images.camera_rear_left",
    );
  });

  it("swaps front_right and depth_left panels for ego-standard row-major", () => {
    const fl = mockNode("fl");
    const dl = mockNode("dl");
    const fr = mockNode("fr");
    const rr = mockNode("rr");
    const left = mockParent([fl, fr]);
    const right = mockParent([dl, rr]);

    const panels = [
      { cam: "observation.images.camera_front_left", node: fl, top: 0, left: 0 },
      { cam: DEPTH_LEFT_VIDEO_KEY, node: dl, top: 0, left: 400 },
      { cam: "observation.images.camera_front_right", node: fr, top: 200, left: 0 },
      { cam: "observation.images.camera_rear_right", node: rr, top: 200, left: 400 },
    ];

    const result = applyEgoCameraLayoutFix(panels);
    assert.equal(result.ok, true);
    assert.equal(result.changed, true);
    assert.deepEqual(left.children.map((n) => n.name), ["fl", "dl"]);
    assert.deepEqual(right.children.map((n) => n.name), ["fr", "rr"]);
  });
});
