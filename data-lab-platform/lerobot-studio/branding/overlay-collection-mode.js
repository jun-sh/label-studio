/**
 * Collection station embed: replay/live mode dropdown + MJPEG preview (v0.0.1 overlay unchanged).
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
    dropdownRoot: null,
    trigger: null,
    menu: null,
    menuOpen: false,
  };

  var collectionOnlineSyncInFlight = false;
  var collectionOnlineLastSyncAt = 0;
  var ONLINE_SYNC_MIN_MS = 15_000;
  var tickScheduled = false;
  var tickDebounceTimer = null;
  var TICK_DEBOUNCE_MS = 250;
  var dropdownInstalled = false;

  var COLLECTION_PREVIEW_FEATURES = [
    { feature: "observation.images.camera_head_left", cam: "head_left" },
    { feature: "observation.images.camera_head_right", cam: "head_right" },
    { feature: "observation.images.camera_depth_head", cam: "depth" },
    { feature: "observation.images.camera_02", cam: "cam_02" },
  ];

  var collectionPreviewSlots = new Map();
  var previewSnapshotTimer = null;
  var PREVIEW_SNAPSHOT_MS = 125;

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
      applyCollectionModeDropdownVisual();
      return;
    }
    var stationId = collectionModeUi.stationId || stationIdFromContext();
    if (!stationId) {
      collectionModeUi.online = true;
      applyCollectionModeDropdownVisual();
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
        applyCollectionModeDropdownVisual();
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
    var retries = 0;
    function loadJpg() {
      overlay.src = collectionPreviewSnapshotUrl(stationId, cam);
    }
    overlay.onerror = function () {
      retries += 1;
      if (retries > PREVIEW_JPG_MAX_RETRIES) {
        overlay.onerror = null;
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
    var wrap = found.wrap;
    var style = g.getComputedStyle(wrap);
    if (style.position === "static") wrap.style.position = "relative";
    var overlay = wrap.querySelector('[data-datalab-preview-overlay="' + cam + '"]');
    if (!overlay) {
      overlay = document.createElement("img");
      overlay.setAttribute("data-datalab-preview-overlay", cam);
      overlay.className = "datalab-collection-preview-overlay";
      overlay.alt = cam;
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
    if (collectionModeUi.mode === "preview" && collectionModeUi.online) {
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
      offline: zh ? "采集离线" : "Station offline",
    };
  }

  function applyCollectionModeDropdownVisual() {
    var labels = collectionModeLabels();
    var trigger = collectionModeUi.trigger;
    var menu = collectionModeUi.menu;
    if (!trigger || !menu) return;
    trigger.textContent = collectionModeUi.mode === "preview" ? labels.preview : labels.dataset;
    trigger.disabled = false;
    var options = menu.querySelectorAll("[data-datalab-collection-mode-option]");
    options.forEach(function (opt) {
      var mode = opt.getAttribute("data-datalab-collection-mode-option");
      var selected = mode === collectionModeUi.mode;
      opt.setAttribute("aria-selected", selected ? "true" : "false");
      opt.classList.toggle("is-selected", selected);
      if (mode === "preview") {
        opt.disabled = !collectionModeUi.online;
        opt.title = collectionModeUi.online ? "" : labels.offline;
      } else {
        opt.disabled = false;
        opt.title = "";
      }
    });
  }

  function selectCollectionMode(mode) {
    if (mode === "preview" && !collectionModeUi.online) mode = "dataset";
    collectionModeUi.mode = mode;
    applyCollectionModeDropdownVisual();
    syncCollectionPreviewOverlay();
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
      selectCollectionMode(data.mode);
      if (typeof g.__datalabBootstrapStreamOpen === "function") {
        g.__datalabBootstrapStreamOpen();
      }
    }
  }

  if (!g.__DATALAB_COLLECTION_PARENT_MSG_BOUND__) {
    g.__DATALAB_COLLECTION_PARENT_MSG_BOUND__ = true;
    g.addEventListener("message", handleParentCollectionModeMessage);
  }

  function closeCollectionModeMenu() {
    collectionModeUi.menuOpen = false;
    if (collectionModeUi.menu) collectionModeUi.menu.hidden = true;
    if (collectionModeUi.trigger) collectionModeUi.trigger.setAttribute("aria-expanded", "false");
  }

  function openCollectionModeMenu() {
    if (!collectionModeUi.menu || !collectionModeUi.trigger) return;
    collectionModeUi.menuOpen = true;
    collectionModeUi.menu.hidden = false;
    collectionModeUi.trigger.setAttribute("aria-expanded", "true");
  }

  function toggleCollectionModeMenu() {
    if (collectionModeUi.menuOpen) closeCollectionModeMenu();
    else openCollectionModeMenu();
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

  function findMenuItemTemplate() {
    return (
      document.querySelector('[role="menuitem"]') ||
      document.querySelector('[role="menuitemradio"]')
    );
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

  function bindCollectionModeMenuDismiss() {
    if (g.__DATALAB_COLLECTION_MODE_MENU_BOUND__) return;
    g.__DATALAB_COLLECTION_MODE_MENU_BOUND__ = true;
    document.addEventListener(
      "click",
      function (event) {
        if (!collectionModeUi.menuOpen || !collectionModeUi.dropdownRoot) return;
        if (collectionModeUi.dropdownRoot.contains(event.target)) return;
        closeCollectionModeMenu();
      },
      true,
    );
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeCollectionModeMenu();
    });
  }

  function removeLegacyCollectionModeButtons() {
    document.querySelectorAll("[data-datalab-collection-mode]").forEach(function (node) {
      if (!node.closest("[data-datalab-collection-mode-root]")) node.remove();
    });
  }

  function installCollectionModeDropdown() {
    if (!isCollectionStationEmbed()) return;
    removeLegacyCollectionModeButtons();
    if (collectionModeUi.dropdownRoot && collectionModeUi.dropdownRoot.isConnected) {
      applyCollectionModeDropdownVisual();
      return;
    }
    var inspectBtn = findCollectionToolbarButton(/^(检查|Inspect)$/i);
    if (!inspectBtn || !inspectBtn.parentNode) return;
    var menuItemTemplate = findMenuItemTemplate();
    var labels = collectionModeLabels();
    var root = document.createElement("div");
    root.setAttribute("data-datalab-collection-mode-root", "1");
    root.className = "datalab-collection-mode-dropdown";
    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "datalab-collection-mode-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    var menu = document.createElement("div");
    menu.className = "datalab-collection-mode-menu";
    menu.setAttribute("role", "listbox");
    menu.hidden = true;
    ["dataset", "preview"].forEach(function (mode) {
      var opt = document.createElement("button");
      opt.type = "button";
      opt.className = "datalab-collection-mode-option";
      opt.setAttribute("role", "option");
      opt.setAttribute("data-datalab-collection-mode-option", mode);
      opt.textContent = mode === "preview" ? labels.preview : labels.dataset;
      if (menuItemTemplate) {
        opt.className = menuItemTemplate.className + " datalab-collection-mode-option";
      }
      opt.addEventListener("click", function () {
        selectCollectionMode(mode);
        closeCollectionModeMenu();
      });
      menu.appendChild(opt);
    });
    trigger.addEventListener("click", function (event) {
      event.stopPropagation();
      toggleCollectionModeMenu();
    });
    root.appendChild(trigger);
    root.appendChild(menu);
    if (inspectBtn.nextSibling) {
      inspectBtn.parentNode.insertBefore(root, inspectBtn.nextSibling);
    } else {
      inspectBtn.parentNode.appendChild(root);
    }
    collectionModeUi.dropdownRoot = root;
    collectionModeUi.trigger = trigger;
    collectionModeUi.menu = menu;
    dropdownInstalled = true;
    bindCollectionModeMenuDismiss();
    applyCollectionModeDropdownVisual();
  }

  function installCollectionModeSwitcher() {
    installCollectionModeDropdown();
  }

  function tick() {
    if (!collectionModeUi.stationId) {
      collectionModeUi.stationId = stationIdFromContext();
    }
    if (!dropdownInstalled) {
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
        if (!dropdownInstalled) scheduleTick();
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
