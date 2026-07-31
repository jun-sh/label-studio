/**
 * Reorder ego OAK 4P camera dockview panels to front row then rear row.
 */
import {
  OVERLAY_CAMERA_LAYOUT_VERSION,
  EGO_OAK_4P_CAMERA_ORDER,
  applyEgoCameraLayoutFix,
  matchCameraFromBlob,
  normalizeEgoCameraKey,
  renameDepthLeftPanelLabels,
  rowMajorCameraOrder,
  cameraOrdersEqual,
} from "./overlay-camera-layout-lib.mjs";

const g = globalThis;
if (!g.__DATALAB_CAMERA_LAYOUT__) {
  g.__DATALAB_CAMERA_LAYOUT__ = true;

  const state = {
    fixed: false,
    observer: null,
    timer: 0,
  };

  function panelBlob(groupview) {
    const tab = groupview.querySelector(".dv-tabs-and-actions-container");
    const title = groupview.getAttribute("data-panel-id") || groupview.getAttribute("title") || "";
    return `${title} ${tab ? tab.textContent : ""} ${groupview.textContent || ""}`;
  }

  function collectVideoPanels() {
    const seen = new Set();
    const out = [];
    for (const gv of document.querySelectorAll(".dv-groupview")) {
      if (!gv.querySelector("video")) continue;
      const cam = matchCameraFromBlob(panelBlob(gv));
      if (!cam || seen.has(cam)) continue;
      seen.add(cam);
      const rect = gv.getBoundingClientRect();
      out.push({ cam, node: gv, top: rect.top, left: rect.left });
    }
    return out;
  }

  function hasFullEgoSet(panels) {
    if (panels.length !== 4) return false;
    const wanted = new Set(EGO_OAK_4P_CAMERA_ORDER);
    const have = new Set();
    for (const p of panels) {
      have.add(normalizeEgoCameraKey(p.cam));
    }
    for (const key of wanted) {
      if (!have.has(key)) return false;
    }
    return true;
  }

  function applyLayoutFix() {
    renameDepthLeftPanelLabels();
    if (state.fixed) return true;
    const panels = collectVideoPanels();
    if (!hasFullEgoSet(panels)) return false;

    const normalized = panels.map((p) => ({
      ...p,
      cam: normalizeEgoCameraKey(p.cam),
    }));
    const current = rowMajorCameraOrder(normalized);
    if (cameraOrdersEqual(current, EGO_OAK_4P_CAMERA_ORDER)) {
      state.fixed = true;
      document.documentElement.setAttribute("data-datalab-camera-layout", "ok");
      document.documentElement.setAttribute(
        "data-datalab-camera-layout-version",
        String(OVERLAY_CAMERA_LAYOUT_VERSION),
      );
      return true;
    }

    const result = applyEgoCameraLayoutFix(panels);
    if (!result.ok) return false;

    renameDepthLeftPanelLabels();
    state.fixed = true;
    document.documentElement.setAttribute("data-datalab-camera-layout", result.changed ? "fixed" : "ok");
    document.documentElement.setAttribute(
      "data-datalab-camera-layout-version",
      String(OVERLAY_CAMERA_LAYOUT_VERSION),
    );
    return true;
  }

  function scheduleLayoutFix() {
    if (state.fixed) return;
    if (state.timer) return;
    state.timer = g.setTimeout(() => {
      state.timer = 0;
      applyLayoutFix();
    }, 120);
  }

  function installObserver() {
    if (state.observer) return;
    state.observer = new MutationObserver(() => scheduleLayoutFix());
    state.observer.observe(document.documentElement, { childList: true, subtree: true });
    scheduleLayoutFix();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", installObserver, { once: true });
  } else {
    installObserver();
  }
}
