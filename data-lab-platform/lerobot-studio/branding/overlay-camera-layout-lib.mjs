/**
 * Ego OAK 4P camera panel order for LeRobot dockview (2x2 grid).
 */
import { featureMatchesText } from "./overlay-hand-keypoints-lib.mjs";
import {
  DISPLAY_PANEL_LABEL,
  DISPLAY_VIDEO_KEY,
} from "./overlay-depth-preview-lib.mjs";

export const OVERLAY_CAMERA_LAYOUT_VERSION = 3;

export const DEPTH_LEFT_VIDEO_KEY = "observation.images.camera_depth_left";

/** Canonical 2x2 order: front row then rear row. */
export const EGO_OAK_4P_CAMERA_ORDER = [
  "observation.images.camera_front_left",
  "observation.images.camera_front_right",
  "observation.images.camera_rear_left",
  "observation.images.camera_rear_right",
];

/** Wrong row-major order observed when ego-standard layout maps onto V0 keys. */
export const EGO_OAK_4P_WRONG_ROW_MAJOR = [
  "observation.images.camera_front_left",
  "observation.images.camera_rear_left",
  "observation.images.camera_front_right",
  "observation.images.camera_rear_right",
];

/** ego-standard datasets still keyed as camera_depth_left (same physical slot as rear_left). */
export const EGO_OAK_4P_DEPTH_LEFT_ROW_MAJOR = [
  "observation.images.camera_front_left",
  DEPTH_LEFT_VIDEO_KEY,
  "observation.images.camera_front_right",
  "observation.images.camera_rear_right",
];

export function normalizeEgoCameraKey(cam) {
  if (cam === DEPTH_LEFT_VIDEO_KEY) {
    return "observation.images.camera_rear_left";
  }
  return cam;
}

export function rowMajorCameraOrder(panels) {
  return [...panels]
    .sort((a, b) => {
      const dy = a.top - b.top;
      if (Math.abs(dy) > 20) return dy;
      return a.left - b.left;
    })
    .map((p) => p.cam);
}

export function cameraOrdersEqual(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

export function needsEgoCameraLayoutFix(currentOrder) {
  if (cameraOrdersEqual(currentOrder, EGO_OAK_4P_CAMERA_ORDER)) return false;
  if (cameraOrdersEqual(currentOrder, EGO_OAK_4P_WRONG_ROW_MAJOR)) return true;
  return cameraOrdersEqual(currentOrder, EGO_OAK_4P_DEPTH_LEFT_ROW_MAJOR);
}

export function swapDomNodes(a, b) {
  if (!a || !b || a === b) return false;
  const aParent = a.parentNode;
  const bParent = b.parentNode;
  if (!aParent || !bParent) return false;
  if (aParent === bParent) {
    const afterB = b.nextSibling;
    aParent.replaceChild(b, a);
    aParent.insertBefore(a, afterB);
    return true;
  }
  const bNext = b.nextSibling;
  aParent.replaceChild(b, a);
  bParent.insertBefore(a, bNext);
  return true;
}

/**
 * Fix FL|RL / FR|RR by swapping front_right and rear_left panels.
 *
 * @param {Array<{ cam: string, node: unknown }>} panels
 */
export function applyEgoCameraLayoutFix(panels) {
  if (!panels || panels.length !== 4) return { ok: false, reason: "need_four_panels" };
  const rects = panels.map((p) => ({
    cam: p.cam,
    node: p.node,
    top: Number(p.top) || 0,
    left: Number(p.left) || 0,
  }));
  const current = rowMajorCameraOrder(rects);
  if (cameraOrdersEqual(current, EGO_OAK_4P_CAMERA_ORDER)) {
    return { ok: true, changed: false, reason: "already_correct" };
  }
  if (!needsEgoCameraLayoutFix(current)) {
    return { ok: false, reason: "unsupported_order", current };
  }
  const fr = rects.find((p) => p.cam === "observation.images.camera_front_right");
  const rl = rects.find(
    (p) =>
      p.cam === "observation.images.camera_rear_left" || p.cam === DEPTH_LEFT_VIDEO_KEY,
  );
  if (!fr?.node || !rl?.node) {
    return { ok: false, reason: "missing_swap_targets" };
  }
  if (!swapDomNodes(fr.node, rl.node)) {
    return { ok: false, reason: "swap_failed" };
  }
  return { ok: true, changed: true, reason: "swapped_front_right_rear_left" };
}

const PANEL_LABEL_REPLACEMENTS = [
  [/camera_depth_left/g, "camera_rear_left"],
  [/深度左/g, "后左"],
];

export function renameDepthLeftPanelLabels(root = globalThis.document) {
  if (!root?.querySelectorAll) return 0;
  let changed = 0;
  for (const gv of root.querySelectorAll(".dv-groupview")) {
    const tab = gv.querySelector(".dv-tab, .dv-default-tab");
    if (tab?.textContent) {
      let next = tab.textContent;
      for (const [pattern, replacement] of PANEL_LABEL_REPLACEMENTS) {
        next = next.replace(pattern, replacement);
      }
      if (next !== tab.textContent) {
        tab.textContent = next;
        changed += 1;
      }
    }
    for (const attr of ["title", "data-panel-id", "aria-label"]) {
      const value = gv.getAttribute(attr);
      if (!value || value.indexOf("camera_depth_left") < 0) continue;
      gv.setAttribute(attr, value.replace(/camera_depth_left/g, "camera_rear_left"));
      changed += 1;
    }
  }
  return changed;
}

export function matchCameraFromBlob(blob) {
  if (!blob) return null;
  // Depth overlay renames the front_right slot label to depth_front_left.
  if (blob.indexOf(DISPLAY_PANEL_LABEL) >= 0) {
    for (const other of [
      "camera_front_left",
      "camera_rear_left",
      "camera_rear_right",
      "camera_front_right",
    ]) {
      if (other === "camera_front_right") continue;
      if (blob.indexOf(other) >= 0) return null;
    }
    return DISPLAY_VIDEO_KEY;
  }
  if (featureMatchesText(blob, DEPTH_LEFT_VIDEO_KEY)) {
    return "observation.images.camera_rear_left";
  }
  for (const key of EGO_OAK_4P_CAMERA_ORDER) {
    if (featureMatchesText(blob, key)) return key;
  }
  return null;
}
