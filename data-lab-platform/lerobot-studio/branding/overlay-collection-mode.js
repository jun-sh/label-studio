/**
 * Collection station embed: replay/live mode pill + JPG preview overlays.
 */
(function () {
  "use strict";

  var g = typeof globalThis !== "undefined" ? globalThis : window;
  if (g.__DATALAB_COLLECTION_MODE__) return;

  function pageLang() {
    var q = "";
    try {
      q = new URLSearchParams(g.location.search).get("lang") || "";
    } catch (e) {
      q = "";
    }
    var html = (document.documentElement && document.documentElement.lang) || "";
    return (q || html || "en").toLowerCase();
  }

  function isCollectionStationEmbed() {
    try {
      var params = new URLSearchParams(g.location.search);
      if (params.get("datalab_collection") === "1") return true;
      if ((params.get("url") || "").indexOf("/api/stream/") >= 0) return true;
    } catch (e) {
      /* ignore */
    }
    try {
      if (g.parent !== g) {
        var parentPath = g.parent.location.pathname || "";
        if (parentPath.indexOf("/collection") < 0) return false;
        return Boolean(new URLSearchParams(g.parent.location.search || "").get("station"));
      }
    } catch (e2) {
      return false;
    }
    return false;
  }

  if (!isCollectionStationEmbed()) return;
  g.__DATALAB_COLLECTION_MODE__ = true;

  var collectionModeUi = {
    mode: "dataset",
    online: true,
    stationId: null,
    chromeRoot: null,
    pillRoot: null,
    statusEl: null,
    segPreview: null,
    segDataset: null,
  };

  var collectionOnlineSyncInFlight = false;
  var collectionOnlineLastSyncAt = 0;
  var ONLINE_SYNC_MIN_MS = 15_000;
  var tickScheduled = false;
  var tickDebounceTimer = null;
  var TICK_DEBOUNCE_MS = 250;
  var pillInstalled = false;

  var COLLECTION_PREVIEW_FEATURES = [
    { feature: "observation.images.camera_front_left", cam: "front_left" },
    { feature: "observation.images.camera_front_right", cam: "front_right" },
    { feature: "observation.images.camera_rear_left", cam: "rear_left" },
    { feature: "observation.images.camera_rear_right", cam: "rear_right" },
  ];

  var collectionPreviewSlots = new Map();
  var previewSnapshotTimer = null;
  var PREVIEW_SNAPSHOT_MS = 250;

  function isParentChromeMode() {
    return document.documentElement.getAttribute("data-datalab-parent-chrome") === "1";
  }

  function stationIdFromContext() {
    try {
      var params = new URLSearchParams(g.location.search || "");
      var raw = params.get("url") || "";
      var m = raw.match(/\/lerobot\/api\/stream\/([^/]+)\/?/);
      if (m) return decodeURIComponent(m[1]);
      if (g.parent !== g) {
        var sid = new URLSearchParams(g.parent.location.search || "").get("station");
        if (sid) return sid;
      }
    } catch (e) {
      /* ignore */
    }
    return null;
  }

  function syncCollectionStationOnline(force) {
    if (!isCollectionStationEmbed()) return;
    var now = Date.now();
    if (!force && now - collectionOnlineLastSyncAt < ONLINE_SYNC_MIN_MS) {
      applyCollectionModeChromeVisual();
      return;
    }
    var stationId = collectionModeUi.stationId || stationIdFromContext();
    if (!stationId) {
      collectionModeUi.online = true;
      applyCollectionModeChromeVisual();
      return;
    }
    if (collectionOnlineSyncInFlight) return;
    collectionOnlineSyncInFlight = true;
    collectionOnlineLastSyncAt = now;
    fetch(
      "/lerobot/api/collection/stations/" + encodeURIComponent(stationId),
      { credentials: "same-origin", cache: "no-store" },
    )
      .then(function (res) {
        return res.ok ? res.json() : null;
      })
      .then(function (station) {
        collectionModeUi.online = station ? Boolean(station.online) : true;
      })
      .catch(function () {
        collectionModeUi.online = true;
      })
      .finally(function () {
        collectionOnlineSyncInFlight = false;
        applyCollectionModeChromeVisual();
        syncCollectionPreviewOverlay();
      });
  }

  function collectionPreviewSnapshotUrl(stationId, cam) {
    return (
      "/lerobot/api/collection/stations/" +
      encodeURIComponent(stationId) +
      "/preview/" +
      encodeURIComponent(cam) +
      "/jpg?t=" +
      Date.now()
    );
  }

  var PREVIEW_JPG_MAX_RETRIES = 6;
  var PREVIEW_JPG_RETRY_MS = 400;

  function bindPreviewOverlaySource(slot, stationId, cam) {
    var overlay = slot.overlay;
    if (!overlay._datalabPreviewBind) {
      overlay._datalabPreviewBind = { retries: 0, loading: false };
    }
    var state = overlay._datalabPreviewBind;
    function loadJpg() {
      if (state.loading) return;
      state.loading = true;
      overlay.src = collectionPreviewSnapshotUrl(stationId, cam);
    }
    overlay.onload = function () {
      state.loading = false;
      state.retries = 0;
    };
    overlay.onerror = function () {
      state.loading = false;
      state.retries += 1;
      if (state.retries > PREVIEW_JPG_MAX_RETRIES) {
        return;
      }
      g.setTimeout(loadJpg, PREVIEW_JPG_RETRY_MS);
    };
    loadJpg();
  }

  function suspendReplayVideosForPreview() {
    if (!isParentChromeMode()) return;
    var videos = document.querySelectorAll("video");
    for (var i = 0; i < videos.length; i++) {
      var video = videos[i];
      if (video._datalabPreviewSuspended) continue;
      video._datalabPreviewSuspended = true;
      video._datalabWasPaused = video.paused;
      try {
        video.pause();
      } catch (e1) {
        /* ignore */
      }
      video._datalabPrevSrcObject = video.srcObject;
      video._datalabPrevSrc = video.getAttribute("src") || "";
      try {
        video.removeAttribute("src");
        video.srcObject = null;
        video.load();
      } catch (e2) {
        /* ignore */
      }
      video.style.visibility = "hidden";
      video.style.pointerEvents = "none";
      video.style.position = "absolute";
      video.style.width = "0";
      video.style.height = "0";
      video.style.margin = "0";
      video.style.padding = "0";
      video.style.overflow = "hidden";
    }
  }

  function resumeReplayVideosAfterPreview() {
    var videos = document.querySelectorAll("video");
    for (var i = 0; i < videos.length; i++) {
      var video = videos[i];
      if (!video._datalabPreviewSuspended) continue;
      video._datalabPreviewSuspended = false;
      video.style.visibility = "";
      video.style.pointerEvents = "";
      video.style.position = "";
      video.style.width = "";
      video.style.height = "";
      video.style.margin = "";
      video.style.padding = "";
      video.style.overflow = "";
      if (video._datalabPrevSrc) video.setAttribute("src", video._datalabPrevSrc);
      if (video._datalabPrevSrcObject) video.srcObject = video._datalabPrevSrcObject;
      if (!video._datalabWasPaused) {
        try {
          video.play();
        } catch (e3) {
          /* ignore */
        }
      }
      video._datalabPrevSrc = "";
      video._datalabPrevSrcObject = null;
    }
  }

  function featureMatchesText(text, featureKey) {
    if (!text) return false;
    if (text.indexOf(featureKey) >= 0) return true;
    var short = featureKey.split(".").pop();
    if (!short) return false;
    if (text.indexOf(short) >= 0) return true;
    return false;
  }

  function findFeatureVideoSlot(featureKey, skipVideos) {
    var videos = document.querySelectorAll("video");
    for (var i = 0; i < videos.length; i++) {
      var video = videos[i];
      if (skipVideos && skipVideos.has(video)) continue;
      var node = video.parentElement;
      for (var depth = 0; depth < 18 && node; depth += 1) {
        var blob = (node.getAttribute("title") || "") + (node.textContent || "");
        if (featureMatchesText(blob, featureKey)) {
          return { video: video, wrap: video.parentElement };
        }
        node = node.parentElement;
      }
    }
    return null;
  }

  function findFeatureVideoSlotByIndex(index, skipVideos) {
    var videos = document.querySelectorAll("video");
    var n = 0;
    for (var i = 0; i < videos.length; i++) {
      var video = videos[i];
      if (skipVideos && skipVideos.has(video)) continue;
      if (n === index) {
        return { video: video, wrap: video.parentElement };
      }
      n += 1;
    }
    return null;
  }

  /** Dockview panel body (.dv-content-container), not the tight <video> wrapper. */
  function findPreviewPanelWrap(video) {
    var node = video.parentElement;
    while (node && node !== document.body) {
      if (node.classList && node.classList.contains("dv-content-container")) {
        return node;
      }
      node = node.parentElement;
    }
    return video.parentElement;
  }

  function ensurePreviewSlot(featureKey, cam, slotIndex) {
    var hit = collectionPreviewSlots.get(featureKey);
    if (hit && hit.overlay && hit.overlay.isConnected) return hit;
    var skipVideos = new Set();
    collectionPreviewSlots.forEach(function (s) {
      if (s && s.video) skipVideos.add(s.video);
    });
    var found =
      typeof slotIndex === "number"
        ? findFeatureVideoSlotByIndex(slotIndex, skipVideos) || findFeatureVideoSlot(featureKey, skipVideos)
        : findFeatureVideoSlot(featureKey, skipVideos);
    if (!found || !found.wrap) return null;
    var wrap = findPreviewPanelWrap(found.video) || found.wrap;
    wrap.setAttribute("data-datalab-preview-panel", "1");
    var style = g.getComputedStyle(wrap);
    if (style.position === "static") wrap.style.position = "relative";
    var overlay = wrap.querySelector('[data-datalab-preview-overlay="' + cam + '"]');
    if (!overlay) {
      overlay = document.createElement("img");
      overlay.setAttribute("data-datalab-preview-overlay", cam);
      overlay.className = "datalab-collection-preview-overlay";
      overlay.alt = cam;
      wrap.appendChild(overlay);
    } else if (overlay.parentElement !== wrap) {
      wrap.appendChild(overlay);
    }
    hit = { video: found.video, wrap: wrap, overlay: overlay };
    collectionPreviewSlots.set(featureKey, hit);
    return hit;
  }

  function refreshCollectionPreviewSnapshots() {
    var stationId = collectionModeUi.stationId || stationIdFromContext();
    if (!stationId) return;
    var usedVideos = new Set();
    COLLECTION_PREVIEW_FEATURES.forEach(function (entry, index) {
      var slot = ensurePreviewSlot(entry.feature, entry.cam, index);
      if (!slot) return;
      if (usedVideos.has(slot.video)) return;
      usedVideos.add(slot.video);
      bindPreviewOverlaySource(slot, stationId, entry.cam);
    });
  }

  function connectCollectionPreviewStreams() {
    suspendReplayVideosForPreview();
    refreshCollectionPreviewSnapshots();
    if (previewSnapshotTimer) return;
    previewSnapshotTimer = g.setInterval(refreshCollectionPreviewSnapshots, PREVIEW_SNAPSHOT_MS);
  }

  function disconnectCollectionPreviewStreams() {
    if (previewSnapshotTimer) {
      g.clearInterval(previewSnapshotTimer);
      previewSnapshotTimer = null;
    }
    collectionPreviewSlots.forEach(function (slot) {
      if (slot.overlay) slot.overlay.removeAttribute("src");
    });
    collectionPreviewSlots.clear();
    resumeReplayVideosAfterPreview();
  }

  function syncCollectionPreviewOverlay() {
    document.documentElement.setAttribute(
      "data-datalab-collection-preview",
      collectionModeUi.mode === "preview" ? "1" : "0",
    );
    // Keep live preview while capture runs even if ingest heartbeat is stale (Scheme A).
    if (collectionModeUi.mode === "preview") {
      connectCollectionPreviewStreams();
    } else {
      disconnectCollectionPreviewStreams();
    }
  }

  function collectionModeLabels() {
    var zh = pageLang().indexOf("zh") === 0;
    return {
      preview: zh ? "实时" : "Live",
      dataset: zh ? "回放" : "Replay",
      offline: zh ? "采集离线" : "Offline",
    };
  }

  function notifyParentCollectionMode(mode) {
    if (g.parent === g) return;
    try {
      g.parent.postMessage(
        {
          type: "datalab-collection-mode",
          mode: mode,
          source: "iframe-ui",
          online: collectionModeUi.online,
        },
        g.location.origin,
      );
    } catch (e) {
      /* ignore */
    }
  }

  function applyCollectionModeChromeVisual() {
    var labels = collectionModeLabels();
    var segPreview = collectionModeUi.segPreview;
    var segDataset = collectionModeUi.segDataset;
    var statusEl = collectionModeUi.statusEl;
    if (!segPreview || !segDataset) return;
    var isPreview = collectionModeUi.mode === "preview";
    segPreview.classList.toggle("is-active", isPreview);
    segDataset.classList.toggle("is-active", !isPreview);
    segPreview.setAttribute("aria-selected", isPreview ? "true" : "false");
    segDataset.setAttribute("aria-selected", !isPreview ? "true" : "false");
  // Scheme A: preview uses 214 MJPEG proxy; do not block Live tab on ingest heartbeat alone.
    segPreview.disabled = false;
    segPreview.title = collectionModeUi.online ? "" : labels.offline;
    segDataset.disabled = false;
    segDataset.title = "";
    if (statusEl) {
      if (collectionModeUi.online) {
        statusEl.hidden = true;
        statusEl.classList.remove("is-offline");
      } else {
        statusEl.hidden = false;
        statusEl.textContent = labels.offline;
        statusEl.classList.add("is-offline");
      }
    }
  }

  function selectCollectionMode(mode, options) {
    options = options || {};
    if (mode === "preview" && !collectionModeUi.online && options.forceOffline) mode = "dataset";
    collectionModeUi.mode = mode;
    applyCollectionModeChromeVisual();
    syncCollectionPreviewOverlay();
    if (!options.fromParent) notifyParentCollectionMode(mode);
  }

  function applyParentCollectionChrome() {
    document.documentElement.setAttribute("data-datalab-parent-chrome", "1");
  }

  function handleParentCollectionModeMessage(event) {
    if (event.source !== g.parent) return;
    if (event.origin !== g.location.origin) return;
    var data = event.data;
    if (!data || typeof data !== "object" || data.type !== "datalab-collection-mode") return;
    if (data.parentChrome) applyParentCollectionChrome();
    if (typeof data.online === "boolean") {
      collectionModeUi.online = data.online;
    }
    if (data.mode === "preview" || data.mode === "dataset") {
      selectCollectionMode(data.mode, { fromParent: true });
      if (typeof g.__datalabBootstrapStreamOpen === "function") {
        g.__datalabBootstrapStreamOpen();
      }
    } else {
      applyCollectionModeChromeVisual();
    }
  }

  if (!g.__DATALAB_COLLECTION_PARENT_MSG_BOUND__) {
    g.__DATALAB_COLLECTION_PARENT_MSG_BOUND__ = true;
    g.addEventListener("message", handleParentCollectionModeMessage);
  }

  function findThemeStyleControl() {
    var buttons = document.querySelectorAll("button");
    for (var i = 0; i < buttons.length; i++) {
      var btn = buttons[i];
      if (btn.closest("[data-datalab-collection-mode-root]")) continue;
      var text = (btn.textContent || "").replace(/\s+/g, " ").trim();
      if (/^(自动|跟随系统|浅色|深色|Auto|System|Light|Dark)$/i.test(text)) return btn;
    }
    return null;
  }

  function findCollectionToolbarButton(labelRe) {
    var buttons = document.querySelectorAll("button");
    for (var i = 0; i < buttons.length; i++) {
      var btn = buttons[i];
      if (btn.closest("[data-datalab-collection-mode-root]")) continue;
      var text = (btn.textContent || "").replace(/\s+/g, " ").trim();
      if (labelRe.test(text)) return btn;
    }
    return null;
  }

  function findCollectionToolbarRow() {
    var browse = findCollectionToolbarButton(/^(浏览|Browse)$/i);
    if (browse && browse.parentNode) return browse.parentNode;
    var inspect = findCollectionToolbarButton(/^(检查|Inspect)$/i);
    if (inspect && inspect.parentNode) return inspect.parentNode;
    var exportBtn = findCollectionToolbarButton(/^(导出|Export)$/i);
    if (exportBtn && exportBtn.parentNode) return exportBtn.parentNode;
    return null;
  }

  /** Insert mode chrome after Export, else after Inspect, else row tail. */
  function insertCollectionModeChrome(toolbarRow, root) {
    var exportBtn = findCollectionToolbarButton(/^(导出|Export)$/i);
    if (exportBtn && exportBtn.parentNode === toolbarRow) {
      if (exportBtn.nextSibling) {
        toolbarRow.insertBefore(root, exportBtn.nextSibling);
      } else {
        toolbarRow.appendChild(root);
      }
      return;
    }
    var inspectBtn = findCollectionToolbarButton(/^(检查|Inspect)$/i);
    if (inspectBtn && inspectBtn.parentNode === toolbarRow) {
      if (inspectBtn.nextSibling) {
        toolbarRow.insertBefore(root, inspectBtn.nextSibling);
      } else {
        toolbarRow.appendChild(root);
      }
      return;
    }
    toolbarRow.appendChild(root);
  }

  function isChromeAfterExportAnchor(toolbarRow, root) {
    var exportBtn = findCollectionToolbarButton(/^(导出|Export)$/i);
    if (exportBtn && exportBtn.parentNode === toolbarRow) {
      return root.previousSibling === exportBtn;
    }
    var inspectBtn = findCollectionToolbarButton(/^(检查|Inspect)$/i);
    if (inspectBtn && inspectBtn.parentNode === toolbarRow) {
      return root.previousSibling === inspectBtn;
    }
    return toolbarRow.lastElementChild === root;
  }

  function relocateCollectionModeChromeIfNeeded() {
    var root = collectionModeUi.chromeRoot;
    if (!root || !root.isConnected) return;
    var toolbarRow = findCollectionToolbarRow();
    if (!toolbarRow) return;
    if (root.parentNode === toolbarRow && isChromeAfterExportAnchor(toolbarRow, root)) return;
    if (root.parentNode) root.remove();
    insertCollectionModeChrome(toolbarRow, root);
  }

  function removeLegacyCollectionModeButtons() {
    document.querySelectorAll("[data-datalab-collection-mode]").forEach(function (node) {
      if (!node.closest("[data-datalab-collection-mode-root]")) node.remove();
    });
  }

  function installCollectionModePill() {
    if (!isCollectionStationEmbed()) return;
    removeLegacyCollectionModeButtons();
    if (collectionModeUi.chromeRoot && collectionModeUi.chromeRoot.isConnected) {
      relocateCollectionModeChromeIfNeeded();
      applyCollectionModeChromeVisual();
      return;
    }
    var toolbarRow = findCollectionToolbarRow();
    if (!toolbarRow) return;
    var labels = collectionModeLabels();
    var root = document.createElement("div");
    root.setAttribute("data-datalab-collection-mode-root", "1");
    root.className = "datalab-collection-mode-chrome";
    var pill = document.createElement("div");
    pill.className = "datalab-collection-mode-pill";
    pill.setAttribute("role", "tablist");
    pill.setAttribute("aria-label", labels.preview + " / " + labels.dataset);
    var segPreview = document.createElement("button");
    segPreview.type = "button";
    segPreview.className = "datalab-collection-mode-seg";
    segPreview.setAttribute("role", "tab");
    segPreview.setAttribute("data-datalab-collection-mode-seg", "preview");
    segPreview.textContent = labels.preview;
    var segDataset = document.createElement("button");
    segDataset.type = "button";
    segDataset.className = "datalab-collection-mode-seg";
    segDataset.setAttribute("role", "tab");
    segDataset.setAttribute("data-datalab-collection-mode-seg", "dataset");
    segDataset.textContent = labels.dataset;
    segPreview.addEventListener("click", function (event) {
      event.stopPropagation();
      selectCollectionMode("preview");
    });
    segDataset.addEventListener("click", function (event) {
      event.stopPropagation();
      selectCollectionMode("dataset");
    });
    pill.appendChild(segPreview);
    pill.appendChild(segDataset);
    var statusEl = document.createElement("span");
    statusEl.className = "datalab-collection-mode-status";
    statusEl.setAttribute("aria-live", "polite");
    root.appendChild(pill);
    root.appendChild(statusEl);
    insertCollectionModeChrome(toolbarRow, root);
    collectionModeUi.chromeRoot = root;
    collectionModeUi.pillRoot = pill;
    collectionModeUi.statusEl = statusEl;
    collectionModeUi.segPreview = segPreview;
    collectionModeUi.segDataset = segDataset;
    pillInstalled = true;
    applyCollectionModeChromeVisual();
  }

  function installCollectionModeSwitcher() {
    installCollectionModePill();
  }

  function tick() {
    if (!collectionModeUi.stationId) {
      collectionModeUi.stationId = stationIdFromContext();
    }
    if (!pillInstalled) {
      installCollectionModeSwitcher();
    }
    if (collectionModeUi.mode === "preview") {
      connectCollectionPreviewStreams();
    }
  }

  function scheduleTick() {
    if (tickScheduled) return;
    tickScheduled = true;
    if (tickDebounceTimer) clearTimeout(tickDebounceTimer);
    tickDebounceTimer = setTimeout(function () {
      tickScheduled = false;
      tickDebounceTimer = null;
      tick();
    }, TICK_DEBOUNCE_MS);
  }

  function schedule() {
    syncCollectionStationOnline(true);
    tick();
    setTimeout(tick, 500);
    setTimeout(tick, 1500);
    setTimeout(tick, 3000);
    if (!g.__DATALAB_COLLECTION_MODE_ONLINE_TIMER__) {
      g.__DATALAB_COLLECTION_MODE_ONLINE_TIMER__ = setInterval(function () {
        syncCollectionStationOnline(true);
      }, ONLINE_SYNC_MIN_MS);
    }
    if (!g.__DATALAB_COLLECTION_MODE_OBSERVER__ && document.body) {
      g.__DATALAB_COLLECTION_MODE_OBSERVER__ = new MutationObserver(function () {
        if (!pillInstalled) scheduleTick();
        else if (collectionModeUi.mode === "preview") scheduleTick();
      });
      g.__DATALAB_COLLECTION_MODE_OBSERVER__.observe(document.body, {
        childList: true,
        subtree: true,
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", schedule);
  } else {
    schedule();
  }
})();
