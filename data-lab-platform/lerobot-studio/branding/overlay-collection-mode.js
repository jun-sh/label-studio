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

  function collectionPreviewMjpegUrl(stationId, cam) {
    return (
      "/lerobot/api/collection/stations/" +
      encodeURIComponent(stationId) +
      "/preview/" +
      encodeURIComponent(cam) +
      "/mjpeg?t=" +
      Date.now()
    );
  }

  function featureMatchesText(text, featureKey) {
    if (!text) return false;
    if (text.indexOf(featureKey) >= 0) return true;
    var short = featureKey.split(".").pop();
    return short && text.indexOf(short) >= 0;
  }

  function findFeatureVideoSlot(featureKey) {
    var videos = document.querySelectorAll("video");
    for (var i = 0; i < videos.length; i++) {
      var video = videos[i];
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

  function ensurePreviewSlot(featureKey, cam) {
    var hit = collectionPreviewSlots.get(featureKey);
    if (hit && hit.overlay && hit.overlay.isConnected) return hit;
    var found = findFeatureVideoSlot(featureKey);
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

  function connectCollectionPreviewStreams() {
    var stationId = collectionModeUi.stationId || stationIdFromContext();
    if (!stationId) return;
    COLLECTION_PREVIEW_FEATURES.forEach(function (entry) {
      var slot = ensurePreviewSlot(entry.feature, entry.cam);
      if (!slot) return;
      slot.overlay.src = collectionPreviewMjpegUrl(stationId, entry.cam);
      slot.video.style.visibility = "hidden";
      slot.video.style.pointerEvents = "none";
    });
  }

  function disconnectCollectionPreviewStreams() {
    collectionPreviewSlots.forEach(function (slot) {
      if (slot.overlay) slot.overlay.removeAttribute("src");
      if (slot.video) {
        slot.video.style.visibility = "";
        slot.video.style.pointerEvents = "";
      }
    });
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
