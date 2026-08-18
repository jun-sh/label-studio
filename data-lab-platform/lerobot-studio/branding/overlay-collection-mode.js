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
    captureState: "unknown",
    remotePreviewAllowed: false,
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
  var previewStaggerTimer = null;
  var previewPollActive = false;
  var replayTransportSuspended = false;
  var collectionUiConfig = g.__DATALAB_COLLECTION_UI__ || {};
  var remotePreviewEnabled = Boolean(collectionUiConfig.remotePreview);
  var PREVIEW_SNAPSHOT_MS = Number(collectionUiConfig.previewSnapshotMs) || 500;
  var PREVIEW_STAGGER_MS = Number(collectionUiConfig.previewStaggerMs) || 100;

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
        collectionModeUi.captureState = station ? String(station.captureState || "unknown") : "unknown";
        collectionModeUi.remotePreviewAllowed = station
          ? Boolean(station.remotePreviewAllowed)
          : false;
      })
      .catch(function () {
        collectionModeUi.online = true;
        collectionModeUi.captureState = "unknown";
        collectionModeUi.remotePreviewAllowed = false;
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

  function revokePreviewObjectUrl(overlay) {
    var state = overlay && overlay._datalabPreviewBind;
    if (!state || !state.objectUrl) return;
    try {
      URL.revokeObjectURL(state.objectUrl);
    } catch (e0) {
      /* ignore */
    }
    state.objectUrl = null;
  }

  function loadPreviewJpg(slot, stationId, cam) {
    var overlay = slot.overlay;
    if (!overlay) return;
    if (!overlay._datalabPreviewBind) {
      overlay._datalabPreviewBind = { retries: 0, loading: false, objectUrl: null };
    }
    var state = overlay._datalabPreviewBind;
    if (state.loading) return;
    state.loading = true;
    fetch(collectionPreviewSnapshotUrl(stationId, cam), {
      credentials: "same-origin",
      cache: "no-store",
    })
      .then(function (res) {
        if (!res.ok) throw new Error("preview_http_" + res.status);
        return res.blob();
      })
      .then(function (blob) {
        if (!blob || !blob.size) throw new Error("preview_empty");
        var nextUrl = URL.createObjectURL(blob);
        overlay.src = nextUrl;
        if (state.objectUrl) {
          try {
            URL.revokeObjectURL(state.objectUrl);
          } catch (e1) {
            /* ignore */
          }
        }
        state.objectUrl = nextUrl;
        state.retries = 0;
        syncPreviewOverlayToVideo(slot);
      })
      .catch(function () {
        state.retries += 1;
        /* Keep last good frame on src — do not clear overlay.src on error. */
      })
      .finally(function () {
        state.loading = false;
        if (state.retries > 0 && state.retries <= PREVIEW_JPG_MAX_RETRIES) {
          g.setTimeout(function () {
            if (collectionModeUi.mode === "preview" && previewModeAllowed()) {
              loadPreviewJpg(slot, stationId, cam);
            }
          }, PREVIEW_JPG_RETRY_MS);
        }
      });
  }

  function ensureAllPreviewSlots() {
    var usedVideos = new Set();
    COLLECTION_PREVIEW_FEATURES.forEach(function (entry, index) {
      var slot = ensurePreviewSlot(entry.feature, entry.cam, index);
      if (!slot || usedVideos.has(slot.video)) return;
      usedVideos.add(slot.video);
    });
    markNullReplayPanelsForPreview();
  }

  function isReplayChromeGroupview(groupview) {
    if (!groupview) return false;
    if (groupview.querySelector("[data-datalab-preview-panel]")) return false;
    if (groupview.querySelector("video")) return false;
    return true;
  }

  function markNullReplayPanelsForPreview() {
    clearReplayChromeMarks();
    var groupviews = document.querySelectorAll(".dv-groupview");
    for (var gi = 0; gi < groupviews.length; gi++) {
      var gv = groupviews[gi];
      if (!isReplayChromeGroupview(gv)) continue;
      gv.setAttribute("data-datalab-replay-chrome", "1");
    }
  }

  function clearReplayChromeMarks() {
    document.querySelectorAll("[data-datalab-replay-chrome]").forEach(function (node) {
      node.removeAttribute("data-datalab-replay-chrome");
    });
  }

  function runPreviewSnapshotCycle() {
    if (collectionModeUi.mode !== "preview" || !previewModeAllowed()) return;
    var stationId = collectionModeUi.stationId || stationIdFromContext();
    if (!stationId) return;
    suspendReplayTransportForPreview();
    dismissScalarSplitView();
    ensureAllPreviewSlots();
    collectionPreviewSlots.forEach(function (slot) {
      syncPreviewOverlayToVideo(slot);
    });
    var tasks = [];
    var usedVideos = new Set();
    COLLECTION_PREVIEW_FEATURES.forEach(function (entry, index) {
      var slot = ensurePreviewSlot(entry.feature, entry.cam, index);
      if (!slot || usedVideos.has(slot.video)) return;
      usedVideos.add(slot.video);
      tasks.push({ slot: slot, cam: entry.cam });
    });
    var ti = 0;
    function nextStagger() {
      if (collectionModeUi.mode !== "preview" || !previewModeAllowed()) return;
      if (ti >= tasks.length) return;
      var task = tasks[ti];
      ti += 1;
      loadPreviewJpg(task.slot, stationId, task.cam);
      previewStaggerTimer = g.setTimeout(nextStagger, PREVIEW_STAGGER_MS);
    }
    if (previewStaggerTimer) {
      g.clearTimeout(previewStaggerTimer);
      previewStaggerTimer = null;
    }
    nextStagger();
  }

  function startPreviewPollLoop() {
    if (previewPollActive) return;
    previewPollActive = true;
    runPreviewSnapshotCycle();
    previewSnapshotTimer = g.setInterval(runPreviewSnapshotCycle, PREVIEW_SNAPSHOT_MS);
  }

  function stopPreviewPollLoop() {
    previewPollActive = false;
    if (previewSnapshotTimer) {
      g.clearInterval(previewSnapshotTimer);
      previewSnapshotTimer = null;
    }
    if (previewStaggerTimer) {
      g.clearTimeout(previewStaggerTimer);
      previewStaggerTimer = null;
    }
  }

  function isTransportPlayPauseButton(btn) {
    if (!btn || btn.closest("[data-datalab-collection-mode-root]")) return false;
    var aria = (btn.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim();
    var title = (btn.getAttribute("title") || "").replace(/\s+/g, " ").trim();
    var text = (btn.textContent || "").replace(/\s+/g, " ").trim();
    var label = aria || title || text;
    if (!label) return false;
    var lower = label.toLowerCase();
    if (/拆分|侧栏|joint|自动刷新|auto.?refresh|split/i.test(label)) return false;
    if (/^(play\/pause|播放\/暂停)$/i.test(label)) return true;
    if (/^(play|pause|播放|暂停)$/i.test(aria)) return true;
    if (/^(play|pause|播放|暂停)$/i.test(title)) return true;
    if (/^(play|pause)$/i.test(lower)) return true;
    return false;
  }

  function findTransportPlayPauseButton() {
    var buttons = document.querySelectorAll("button");
    for (var bi = 0; bi < buttons.length; bi++) {
      if (isTransportPlayPauseButton(buttons[bi])) return buttons[bi];
    }
    return null;
  }

  function transportButtonShowsPauseIcon(btn) {
    var svg = btn && btn.querySelector("svg");
    var cls = (svg && svg.getAttribute("class")) || "";
    return cls.indexOf("lucide-pause") >= 0;
  }

  function clickTransportPlayPause() {
    var btn = findTransportPlayPauseButton();
    if (!btn) return;
    try {
      btn.click();
    } catch (e1) {
      /* ignore */
    }
  }

  function isScalarSplitSidebar(node) {
    if (!node) return false;
    var blob = (node.textContent || "").replace(/\s+/g, " ").trim();
    return /拆分|split/i.test(blob);
  }

  function dismissScalarSplitView() {
    document.querySelectorAll("div.fixed.z-50").forEach(function (node) {
      if (!isScalarSplitSidebar(node)) return;
      var closeBtn = node.querySelector("button");
      if (closeBtn) {
        try {
          closeBtn.click();
        } catch (e0) {
          /* ignore */
        }
      }
    });
  }

  function closeOpenRadixMenus() {
    document.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true, cancelable: true }),
    );
  }

  function findTransportBar() {
    return document.querySelector(".h-16.border-t.bg-background");
  }

  function findTransportSlider(bar) {
    if (!bar) bar = findTransportBar();
    if (!bar) return null;
    return bar.querySelector('[role="slider"][aria-valuemin="0"]');
  }

  function dismissAutoplayDialog() {
    var buttons = document.querySelectorAll("button");
    for (var bi = 0; bi < buttons.length; bi++) {
      var text = (buttons[bi].textContent || "").replace(/\s+/g, " ").trim();
      if (text === "知道了" || text === "Got it") {
        try {
          buttons[bi].click();
        } catch (e0) {
          /* ignore */
        }
      }
    }
  }

  function isReplayTransportPlaying() {
    return transportButtonShowsPauseIcon(findTransportPlayPauseButton());
  }

  function sendTransportPauseKeys() {
    var target = findTransportBar() || document.body;
    if (!target) return;
    ["keydown", "keyup"].forEach(function (type) {
      target.dispatchEvent(
        new KeyboardEvent(type, { key: " ", code: "Space", bubbles: true, cancelable: true }),
      );
      target.dispatchEvent(
        new KeyboardEvent(type, { key: "k", code: "KeyK", bubbles: true, cancelable: true }),
      );
    });
  }

  function ensureTransportPaused() {
    var videos = document.querySelectorAll("video");
    for (var vi = 0; vi < videos.length; vi++) {
      try {
        videos[vi].pause();
      } catch (e0) {
        /* ignore */
      }
    }
    var btn = findTransportPlayPauseButton();
    if (btn && btn.disabled && btn.getAttribute("data-datalab-live-play-blocked") === "1") {
      btn.disabled = false;
      btn.removeAttribute("aria-disabled");
    }
    var attempts = 0;
    while (isReplayTransportPlaying() && attempts < 4) {
      clickTransportPlayPause();
      sendTransportPauseKeys();
      attempts += 1;
    }
  }

  var liveTransportObserver = null;
  var patchTransportScheduled = false;
  var liveSeekBurstTimer = null;
  var liveRawSnapshotObserver = null;
  var applyingLiveFeaturesPlaceholder = false;
  var livePauseGuardTimer = null;

  function clickAllFeaturesTab() {
    var buttons = document.querySelectorAll("button");
    for (var bi = 0; bi < buttons.length; bi++) {
      var label = (buttons[bi].textContent || "").replace(/\s+/g, " ").trim();
      if (!/^全部\s*Features$/i.test(label)) continue;
      try {
        buttons[bi].click();
      } catch (e0) {
        /* ignore */
      }
      return true;
    }
    return false;
  }

  function rawViewportHasFeaturesLayout(viewport) {
    if (!viewport) return false;
    return viewport.querySelector(".flex") !== null;
  }

  function ensureLiveRawSnapshotObserver() {
    if (liveRawSnapshotObserver) return;
    var viewport = findRawMessageViewport();
    if (!viewport) return;
    liveRawSnapshotObserver = new MutationObserver(function () {
      if (applyingLiveFeaturesPlaceholder) return;
      if (collectionModeUi.mode !== "preview" || !previewModeAllowed()) return;
      applyLiveFeaturesPlaceholder();
    });
    liveRawSnapshotObserver.observe(viewport, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  }

  function stopLiveRawSnapshotObserver() {
    if (!liveRawSnapshotObserver) return;
    liveRawSnapshotObserver.disconnect();
    liveRawSnapshotObserver = null;
  }

  function schedulePatchLiveTransportBar() {
    if (patchTransportScheduled) return;
    patchTransportScheduled = true;
    g.requestAnimationFrame(function () {
      patchTransportScheduled = false;
      if (collectionModeUi.mode !== "preview" || !previewModeAllowed()) return;
      patchLiveTransportBar();
    });
  }

  function ensureLiveTransportObserver() {
    if (liveTransportObserver) return;
    var bar = findTransportBar();
    if (!bar) return;
    liveTransportObserver = new MutationObserver(function () {
      schedulePatchLiveTransportBar();
    });
    liveTransportObserver.observe(bar, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  }

  function stopLiveTransportObserver() {
    if (!liveTransportObserver) return;
    liveTransportObserver.disconnect();
    liveTransportObserver = null;
  }

  function findRawMessageViewport() {
    var groupviews = document.querySelectorAll(".dv-groupview");
    for (var gi = 0; gi < groupviews.length; gi++) {
      var gv = groupviews[gi];
      if ((gv.textContent || "").indexOf("原始消息") < 0) continue;
      return gv.querySelector("[data-radix-scroll-area-viewport]");
    }
    return null;
  }

  function findJointsChartGroupview() {
    var groupviews = document.querySelectorAll(".dv-groupview");
    for (var gi = 0; gi < groupviews.length; gi++) {
      var gv = groupviews[gi];
      if ((gv.textContent || "").indexOf("折线图") >= 0) return gv;
    }
    return null;
  }

  function isJointsPickerSummaryText(text) {
    if (!text) return false;
    return (
      /^全部\s*joints$/i.test(text) ||
      /^all\s*joints$/i.test(text) ||
      /^未选择$/i.test(text) ||
      /^none\s*selected$/i.test(text) ||
      /^已选\s*\d+$/i.test(text) ||
      /^\d+\s*selected$/i.test(text)
    );
  }

  function findJointsPickerTriggerButton(gv) {
    if (!gv) gv = findJointsChartGroupview();
    if (!gv) return null;
    var buttons = gv.querySelectorAll("button");
    for (var bi = 0; bi < buttons.length; bi++) {
      var btn = buttons[bi];
      if (btn.closest("[data-datalab-collection-mode-root]")) continue;
      var labelSpan = btn.querySelector("span.truncate");
      if (labelSpan && isJointsPickerSummaryText((labelSpan.textContent || "").trim())) {
        return btn;
      }
      var spans = btn.querySelectorAll("span");
      for (var si = 0; si < spans.length; si++) {
        if (isJointsPickerSummaryText((spans[si].textContent || "").replace(/\s+/g, " ").trim())) {
          return btn;
        }
      }
    }
    return null;
  }

  function jointsPickerSummaryText(gv) {
    if (!gv) gv = findJointsChartGroupview();
    if (!gv) return "";
    var trigger = findJointsPickerTriggerButton(gv);
    if (!trigger) return "";
    var labelSpan = trigger.querySelector("span.truncate");
    if (labelSpan) {
      return (labelSpan.textContent || "").replace(/\s+/g, " ").trim();
    }
    var spans = trigger.querySelectorAll("span");
    for (var si = 0; si < spans.length; si++) {
      var spanText = (spans[si].textContent || "").replace(/\s+/g, " ").trim();
      if (isJointsPickerSummaryText(spanText)) return spanText;
    }
    return "";
  }

  function jointsPickerIsNone(gv) {
    var label = jointsPickerSummaryText(gv);
    return /^未选择$/i.test(label) || /^none\s*selected$/i.test(label);
  }

  function jointsPickerIsAll(gv) {
    var label = jointsPickerSummaryText(gv);
    return /^全部\s*joints$/i.test(label) || /^all\s*joints$/i.test(label);
  }

  function jointsPickerIsPartial(gv) {
    var label = jointsPickerSummaryText(gv);
    return /^已选\s*\d+$/i.test(label) || /^\d+\s*selected$/i.test(label);
  }

  function finishLiveJointsBootstrap(gv) {
    jointsBootstrapInFlight = false;
    document.documentElement.removeAttribute("data-datalab-live-joints-bootstrapping");
    closeJointsPickerMenu();
    if (gv) markJointsChartLiveCleared(gv);
  }

  function abortLiveJointsBootstrap() {
    jointsBootstrapInFlight = false;
    document.documentElement.removeAttribute("data-datalab-live-joints-bootstrapping");
    closeJointsPickerMenu();
  }

  function findJointsPickerMenuRoot() {
    var inputs = document.querySelectorAll("input[placeholder]");
    for (var ii = 0; ii < inputs.length; ii++) {
      var placeholder = inputs[ii].getAttribute("placeholder") || "";
      if (!/joint/i.test(placeholder)) continue;
      var node = inputs[ii].parentElement;
      for (var depth = 0; depth < 20 && node; depth += 1) {
        if (node.querySelector('button[data-filter-row="true"]')) return node;
        node = node.parentElement;
      }
    }
    return null;
  }

  function findJointsMenuSelectAllToggle(menu) {
    if (!menu) return null;
    return menu.querySelector('button[data-filter-row="true"]');
  }

  function closeJointsPickerMenu() {
    closeOpenRadixMenus();
  }

  function markJointsChartLiveCleared(gv) {
    if (!gv) gv = findJointsChartGroupview();
    if (gv) gv.setAttribute("data-datalab-live-joints-cleared", "1");
    g.__DATALAB_LIVE_JOINTS_CLEARED__ = true;
  }

  function clearJointsChartLiveClearedMark() {
    document.querySelectorAll("[data-datalab-live-joints-cleared]").forEach(function (node) {
      node.removeAttribute("data-datalab-live-joints-cleared");
    });
    document.documentElement.removeAttribute("data-datalab-live-joints-bootstrapping");
    delete g.__DATALAB_LIVE_JOINTS_CLEARED__;
    delete g.__DATALAB_LIVE_JOINTS_USER_OVERRIDE__;
  }

  var jointsBootstrapInFlight = false;

  function bootstrapJointsSelectionForLive(attempt, clearPass) {
    if (collectionModeUi.mode !== "preview" || !previewModeAllowed()) return;
    if (g.__DATALAB_LIVE_JOINTS_USER_OVERRIDE__ || g.__DATALAB_LIVE_JOINTS_CLEARED__) return;
    if (typeof attempt !== "number") attempt = 0;
    if (typeof clearPass !== "number") clearPass = 0;
    if (attempt === 0 && jointsBootstrapInFlight) return;
    if (attempt === 0) {
      jointsBootstrapInFlight = true;
      document.documentElement.setAttribute("data-datalab-live-joints-bootstrapping", "1");
    }
    if (attempt >= 16) {
      abortLiveJointsBootstrap();
      return;
    }
    dismissScalarSplitView();
    ensureTransportPaused();
    var gv = findJointsChartGroupview();
    if (!gv) {
      g.setTimeout(function () {
        bootstrapJointsSelectionForLive(attempt + 1, clearPass);
      }, 150);
      return;
    }
    if (jointsPickerIsNone(gv)) {
      finishLiveJointsBootstrap(gv);
      return;
    }
    var menu = findJointsPickerMenuRoot();
    if (!menu) {
      var trigger = findJointsPickerTriggerButton(gv);
      if (trigger) {
        try {
          trigger.click();
        } catch (e0) {
          /* ignore */
        }
      }
      g.setTimeout(function () {
        bootstrapJointsSelectionForLive(attempt + 1, clearPass);
      }, 180);
      return;
    }
    var toggle = findJointsMenuSelectAllToggle(menu);
    if (!toggle) {
      g.setTimeout(function () {
        bootstrapJointsSelectionForLive(attempt + 1, clearPass);
      }, 120);
      return;
    }
    var nextPass = clearPass;
    if (!nextPass) {
      nextPass = jointsPickerIsPartial(gv) ? 1 : 2;
    }
    try {
      toggle.click();
    } catch (e1) {
      /* ignore */
    }
    g.setTimeout(function () {
      var latest = findJointsChartGroupview();
      if (jointsPickerIsNone(latest)) {
        finishLiveJointsBootstrap(latest || gv);
        return;
      }
      if (nextPass === 1) {
        bootstrapJointsSelectionForLive(attempt, 2);
        return;
      }
      bootstrapJointsSelectionForLive(attempt + 1, nextPass);
    }, 220);
  }

  var jointsSelectionBootstrapToken = 0;

  function scheduleJointsSelectionBootstrap() {
    if (g.__DATALAB_LIVE_JOINTS_CLEARED__ || g.__DATALAB_LIVE_JOINTS_USER_OVERRIDE__) return;
    jointsSelectionBootstrapToken += 1;
    delete g.__DATALAB_LIVE_JOINTS_CLEARED__;
    delete g.__DATALAB_LIVE_JOINTS_USER_OVERRIDE__;
    clearJointsChartLiveClearedMark();
    bootstrapJointsSelectionForLive(0, 0);
  }

  function noteLiveJointsUserOverride(target) {
    if (collectionModeUi.mode !== "preview" || !previewModeAllowed()) return;
    var gv = findJointsChartGroupview();
    if (!gv) return;
    var trigger = findJointsPickerTriggerButton(gv);
    if (!trigger) return;
    if (target !== trigger && !trigger.contains(target) && !findJointsPickerMenuRoot()) return;
    g.__DATALAB_LIVE_JOINTS_USER_OVERRIDE__ = true;
    jointsSelectionBootstrapToken += 1;
    jointsBootstrapInFlight = false;
    document.documentElement.removeAttribute("data-datalab-live-joints-bootstrapping");
    gv.removeAttribute("data-datalab-live-joints-cleared");
    delete g.__DATALAB_LIVE_JOINTS_CLEARED__;
  }

  function installLiveJointsUserOverrideListener() {
    if (g.__DATALAB_LIVE_JOINTS_USER_LISTENER__) return;
    g.__DATALAB_LIVE_JOINTS_USER_LISTENER__ = true;
    document.addEventListener(
      "click",
      function (event) {
        noteLiveJointsUserOverride(event.target);
      },
      true,
    );
  }

  function runRestoreJointsSelectionForReplay(attempt) {
    if (collectionModeUi.mode === "preview" && previewModeAllowed()) return;
    if (typeof attempt !== "number") attempt = 0;
    if (attempt > 12) {
      closeJointsPickerMenu();
      return;
    }
    var gv = findJointsChartGroupview();
    if (!gv) {
      g.setTimeout(function () {
        runRestoreJointsSelectionForReplay(attempt + 1);
      }, 150);
      return;
    }
    clearJointsChartLiveClearedMark();
    if (jointsPickerIsAll(gv)) {
      closeJointsPickerMenu();
      return;
    }
    var menu = findJointsPickerMenuRoot();
    if (!menu) {
      var trigger = findJointsPickerTriggerButton(gv);
      if (trigger) {
        try {
          trigger.click();
        } catch (e2) {
          /* ignore */
        }
      }
      g.setTimeout(function () {
        runRestoreJointsSelectionForReplay(attempt + 1);
      }, 180);
      return;
    }
    var toggle = findJointsMenuSelectAllToggle(menu);
    if (!toggle) {
      g.setTimeout(function () {
        runRestoreJointsSelectionForReplay(attempt + 1);
      }, 120);
      return;
    }
    if (jointsPickerIsNone(gv) || !jointsPickerIsAll(gv)) {
      try {
        toggle.click();
      } catch (e3) {
        /* ignore */
      }
    }
    g.setTimeout(function () {
      runRestoreJointsSelectionForReplay(attempt + 1);
    }, 200);
  }

  function restoreJointsSelectionForReplay() {
    runRestoreJointsSelectionForReplay(0);
  }

  function startLiveJointsSelectionGuard() {
    installLiveJointsUserOverrideListener();
    scheduleJointsSelectionBootstrap();
  }

  function stopLiveJointsSelectionGuard() {
    jointsBootstrapInFlight = false;
    jointsSelectionBootstrapToken += 1;
    closeJointsPickerMenu();
    restoreJointsSelectionForReplay();
    clearJointsChartLiveClearedMark();
  }

  function buildZeroedFeaturesHtml(html) {
    var tmp = document.createElement("div");
    tmp.innerHTML = String(html || "");
    tmp.querySelectorAll("*").forEach(function (el) {
      if (el.children.length > 0) return;
      var t = (el.textContent || "").trim();
      if (/^-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(t)) {
        el.textContent = "0";
      }
    });
    return String(tmp.innerHTML)
      .replace(/"frame_index"\s*:\s*\d+/g, '"frame_index": 0')
      .replace(/"timestamp"\s*:\s*[\d.eE+-]+/g, '"timestamp": 0');
  }

  function liveFeaturesPlaceholderHtml() {
    return g.__DATALAB_LIVE_FEATURES_PLACEHOLDER_HTML__ || g.__DATALAB_LIVE_FRAME_ZERO_HTML__ || "";
  }

  function captureLiveFeaturesPlaceholder() {
    if (liveFeaturesPlaceholderHtml()) return true;
    var viewport = findRawMessageViewport();
    if (!viewport || !rawViewportHasFeaturesLayout(viewport)) return false;
    var html = buildZeroedFeaturesHtml(viewport.innerHTML);
    g.__DATALAB_LIVE_FEATURES_PLACEHOLDER_HTML__ = html;
    return true;
  }

  function applyLiveFeaturesPlaceholder() {
    var html = liveFeaturesPlaceholderHtml();
    if (!html) return false;
    var viewport = findRawMessageViewport();
    if (!viewport) return false;
    if (viewport.innerHTML === html) return true;
    applyingLiveFeaturesPlaceholder = true;
    viewport.innerHTML = html;
    applyingLiveFeaturesPlaceholder = false;
    return true;
  }

  function bootstrapLiveFeaturesPlaceholder(attempt) {
    if (typeof attempt !== "number") attempt = 0;
    if (liveFeaturesPlaceholderHtml()) {
      applyLiveFeaturesPlaceholder();
      return;
    }
    dismissAutoplayDialog();
    ensureTransportPaused();
    patchLiveTransportBar();
    clickAllFeaturesTab();
    captureLiveFeaturesPlaceholder();
    applyLiveFeaturesPlaceholder();
    patchLiveTransportBar();
    ensureTransportPaused();
    if (liveFeaturesPlaceholderHtml()) return;
    if (attempt >= 24) return;
    g.setTimeout(function () {
      bootstrapLiveFeaturesPlaceholder(attempt + 1);
    }, 100);
  }

  function pauseReplayBeforeLiveSwitch() {
    dismissAutoplayDialog();
    ensureTransportPaused();
    var videos = document.querySelectorAll("video");
    for (var vi = 0; vi < videos.length; vi++) {
      try {
        videos[vi].pause();
      } catch (e0) {
        /* ignore */
      }
    }
  }

  function pointerSeekSlider(slider, clientX, clientY) {
    var track = slider && slider.parentElement;
    var target = track || slider;
    ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach(function (type) {
      var Cls = type.indexOf("pointer") === 0 ? PointerEvent : MouseEvent;
      target.dispatchEvent(
        new Cls(type, {
          bubbles: true,
          cancelable: true,
          composed: true,
          clientX: clientX,
          clientY: clientY,
          view: g,
          pointerId: 1,
          pointerType: "mouse",
          buttons: 1,
        }),
      );
    });
  }

  function seekReplayTransportToStart() {
    if (
      collectionModeUi.mode === "preview" &&
      previewModeAllowed() &&
      liveFeaturesPlaceholderHtml()
    ) {
      applyLiveFeaturesPlaceholder();
      return;
    }
    var slider = findTransportSlider();
    if (!slider) return;
    ensureTransportPaused();
    var frameNow = Number(slider.getAttribute("aria-valuenow") || 0);
    if (frameNow > 0) {
      nudgeSliderToStart(slider);
      ensureTransportPaused();
    }
    applyLiveFeaturesPlaceholder();
  }

  function stashTransportLabel(el, nextText) {
    if (!el.dataset.datalabTransportOrig) {
      el.dataset.datalabTransportOrig = el.textContent;
    }
    if (el.textContent !== nextText) {
      el.textContent = nextText;
    }
  }

  function patchLiveTransportBar() {
    var bar = findTransportBar();
    if (!bar) return;
    bar.setAttribute("data-datalab-live-transport", "1");
    var groups = bar.querySelectorAll(".flex.items-baseline");
    for (var gi = 0; gi < groups.length; gi++) {
      var group = groups[gi];
      var blob = (group.textContent || "").replace(/\s+/g, " ");
      var isTimeGroup = /\d+:\d+\.\d+/.test(blob);
      var isFrameGroup = /\d+\s*\/\s*\d+/.test(blob) && !/\d+:\d+/.test(blob);
      var leaves = group.querySelectorAll("*");
      for (var lj = 0; lj < leaves.length; lj++) {
        var node = leaves[lj];
        if (node.children.length > 0) continue;
        var txt = (node.textContent || "").trim();
        if (isTimeGroup) {
          if (/^\d+:\d+\.\d+$/.test(txt)) stashTransportLabel(node, "0:00.00");
          if (/^\/\s*\d+:\d+\.\d+$/.test(txt)) stashTransportLabel(node, "/ 0:00.00");
        }
        if (isFrameGroup) {
          if (/^\d+$/.test(txt)) stashTransportLabel(node, "0");
          if (/^\/\s*\d+$/.test(txt)) stashTransportLabel(node, "/ 0");
        }
      }
    }
    var slider = findTransportSlider(bar);
    if (slider) {
      slider.setAttribute("aria-disabled", "true");
      slider.setAttribute("aria-valuenow", "0");
      slider.setAttribute("aria-valuetext", "0:00.00 / 0:00.00, 帧 0/0");
      slider.style.pointerEvents = "none";
    }
    var playBtn = findTransportPlayPauseButton();
    if (playBtn) {
      var playSvg = playBtn.querySelector("svg");
      if (playSvg) {
        playSvg.setAttribute("class", "lucide lucide-play h-4 w-4");
      }
    }
    bar.querySelectorAll("*").forEach(function (node) {
      if (node.children.length > 0) return;
      var flat = (node.textContent || "").trim();
      if (/^\d+:\d+\.\d+$/.test(flat)) stashTransportLabel(node, "0:00.00");
      if (/^\/\s*\d+:\d+\.\d+$/.test(flat)) stashTransportLabel(node, "/ 0:00.00");
    });
  }

  function clearLiveTransportFreeze() {
    stopLiveSeekBurst();
    stopLivePauseGuard();
    stopLiveJointsSelectionGuard();
    stopLiveTransportObserver();
    stopLiveRawSnapshotObserver();
    unlockLiveTransportPlayButton();
    var bar = document.querySelector("[data-datalab-live-transport]");
    if (!bar) return;
    bar.removeAttribute("data-datalab-live-transport");
    bar.querySelectorAll("[data-datalab-transport-orig]").forEach(function (el) {
      el.textContent = el.dataset.datalabTransportOrig;
      delete el.dataset.datalabTransportOrig;
    });
    var slider = findTransportSlider(bar);
    if (slider) {
      slider.removeAttribute("aria-disabled");
      slider.style.pointerEvents = "";
    }
  }

  function nudgeSliderToStart(slider) {
    var start = Number(slider.getAttribute("aria-valuenow") || 0);
    var steps = Math.min(700, Math.max(80, start + 60));
    for (var step = 0; step < steps; step += 1) {
      var frameNow = Number(slider.getAttribute("aria-valuenow") || 0);
      if (frameNow <= 0) break;
      slider.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowLeft", code: "ArrowLeft", bubbles: true }),
      );
    }
  }

  function lockLiveTransportPlayButton() {
    var btn = findTransportPlayPauseButton();
    if (!btn) return;
    if (isReplayTransportPlaying()) return;
    btn.setAttribute("data-datalab-live-play-blocked", "1");
    btn.disabled = true;
    btn.setAttribute("aria-disabled", "true");
    btn.tabIndex = -1;
  }

  function unlockLiveTransportPlayButton() {
    document.querySelectorAll('button[data-datalab-live-play-blocked="1"]').forEach(function (btn) {
      btn.removeAttribute("data-datalab-live-play-blocked");
      btn.disabled = false;
      btn.removeAttribute("aria-disabled");
      btn.tabIndex = 0;
    });
  }

  function freezeTransportSliderPosition(slider) {
    if (!slider) return;
    if (Number(slider.getAttribute("aria-valuenow") || 0) !== 0) {
      slider.setAttribute("aria-valuenow", "0");
    }
  }

  function startLivePauseGuard() {
    if (livePauseGuardTimer) return;
    livePauseGuardTimer = g.setInterval(function () {
      if (collectionModeUi.mode !== "preview" || !previewModeAllowed()) {
        stopLivePauseGuard();
        return;
      }
      dismissAutoplayDialog();
      ensureTransportPaused();
      captureLiveFeaturesPlaceholder();
      applyLiveFeaturesPlaceholder();
      freezeTransportSliderPosition(findTransportSlider());
      patchLiveTransportBar();
      lockLiveTransportPlayButton();
    }, 150);
  }

  function stopLivePauseGuard() {
    if (!livePauseGuardTimer) return;
    g.clearInterval(livePauseGuardTimer);
    livePauseGuardTimer = null;
  }

  function startLiveSeekBurst() {
    if (liveSeekBurstTimer) return;
    var ticks = 0;
    liveSeekBurstTimer = g.setInterval(function () {
      if (collectionModeUi.mode !== "preview" || !previewModeAllowed()) {
        stopLiveSeekBurst();
        return;
      }
      dismissAutoplayDialog();
      captureLiveFeaturesPlaceholder();
      ensureTransportPaused();
      applyLiveFeaturesPlaceholder();
      patchLiveTransportBar();
      ticks += 1;
      if (ticks >= 5) {
        stopLiveSeekBurst();
        startLivePauseGuard();
      }
    }, 100);
  }

  function stopLiveSeekBurst() {
    if (!liveSeekBurstTimer) return;
    g.clearInterval(liveSeekBurstTimer);
    liveSeekBurstTimer = null;
  }

  function applyLiveReplayFreeze() {
    if (collectionModeUi.mode !== "preview" || !previewModeAllowed()) return;
    dismissAutoplayDialog();
    ensureTransportPaused();
    captureLiveFeaturesPlaceholder();
    clickAllFeaturesTab();
    bootstrapLiveFeaturesPlaceholder(0);
    applyLiveFeaturesPlaceholder();
    dismissScalarSplitView();
    startLiveJointsSelectionGuard();
    lockLiveTransportPlayButton();
    freezeTransportSliderPosition(findTransportSlider());
    patchLiveTransportBar();
    ensureLiveTransportObserver();
    ensureLiveRawSnapshotObserver();
    startLivePauseGuard();
  }

  function installLiveTransportPlayBlock() {
    if (g.__DATALAB_LIVE_TRANSPORT_PLAY_BLOCK__) return;
    g.__DATALAB_LIVE_TRANSPORT_PLAY_BLOCK__ = true;
    document.addEventListener(
      "click",
      function (event) {
        if (collectionModeUi.mode !== "preview" || !previewModeAllowed()) return;
        var target = event.target;
        var btn = target && target.closest ? target.closest("button") : null;
        if (!btn || !isTransportPlayPauseButton(btn)) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        ensureTransportPaused();
        patchLiveTransportBar();
      },
      true,
    );
  }

  function suspendReplayTransportForPreview() {
    if (!replayTransportSuspended) replayTransportSuspended = true;
    var ranges = document.querySelectorAll('input[type="range"]');
    for (var ri = 0; ri < ranges.length; ri++) {
      var inp = ranges[ri];
      if (inp._datalabPreviewDisabled) continue;
      inp._datalabPreviewDisabled = true;
      inp._datalabWasDisabled = inp.disabled;
      inp.disabled = true;
    }
    applyLiveReplayFreeze();
    dismissScalarSplitView();
  }

  function resumeReplayTransportForPreview() {
    if (!replayTransportSuspended) return;
    replayTransportSuspended = false;
    clearLiveTransportFreeze();
    var ranges = document.querySelectorAll('input[type="range"]');
    for (var ri = 0; ri < ranges.length; ri++) {
      var inp = ranges[ri];
      if (!inp._datalabPreviewDisabled) continue;
      inp.disabled = Boolean(inp._datalabWasDisabled);
      inp._datalabPreviewDisabled = false;
      inp._datalabWasDisabled = false;
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

  /** Flex wrapper that centers the replay <video> (same box live JPG should use). */
  function findPreviewOverlayWrap(video) {
    return (video && video.parentElement) || null;
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

  function syncPreviewOverlayToVideo(slot) {
    if (!slot || !slot.video || !slot.overlay) return;
    var video = slot.video;
    var overlay = slot.overlay;
    var left = video.offsetLeft;
    var top = video.offsetTop;
    var width = video.offsetWidth;
    var height = video.offsetHeight;
    if (!width || !height) return;
    overlay.style.left = left + "px";
    overlay.style.top = top + "px";
    overlay.style.width = width + "px";
    overlay.style.height = height + "px";
    overlay.style.right = "auto";
    overlay.style.bottom = "auto";
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
    var panelWrap = findPreviewPanelWrap(found.video) || found.wrap;
    var overlayWrap = findPreviewOverlayWrap(found.video) || found.wrap;
    panelWrap.setAttribute("data-datalab-preview-panel", "1");
    var overlayStyle = g.getComputedStyle(overlayWrap);
    if (overlayStyle.position === "static") overlayWrap.style.position = "relative";
    var overlay = overlayWrap.querySelector('[data-datalab-preview-overlay="' + cam + '"]');
    if (!overlay) {
      overlay = document.createElement("img");
      overlay.setAttribute("data-datalab-preview-overlay", cam);
      overlay.className = "datalab-collection-preview-overlay";
      overlay.alt = cam;
      overlayWrap.appendChild(overlay);
    } else if (overlay.parentElement !== overlayWrap) {
      overlayWrap.appendChild(overlay);
    }
    syncPreviewOverlayToVideo({ video: found.video, overlay: overlay });
    hit = { video: found.video, wrap: panelWrap, overlayWrap: overlayWrap, overlay: overlay };
    collectionPreviewSlots.set(featureKey, hit);
    return hit;
  }

  function wakeStationPreview(stationId) {
    if (!stationId) return;
    fetch(
      "/lerobot/api/collection/stations/" + encodeURIComponent(stationId) + "/preview/wake",
      { method: "GET", credentials: "same-origin", cache: "no-store" },
    ).catch(function () {
      /* best-effort warm-up */
    });
  }

  function connectCollectionPreviewStreams() {
    delete g.__DATALAB_LIVE_JOINTS_CLEARED__;
    clearJointsChartLiveClearedMark();
    suspendReplayTransportForPreview();
    dismissScalarSplitView();
    wakeStationPreview(collectionModeUi.stationId || stationIdFromContext());
    ensureAllPreviewSlots();
    dismissScalarSplitView();
    startLiveSeekBurst();
    startPreviewPollLoop();
  }

  function disconnectCollectionPreviewStreams() {
    stopLiveSeekBurst();
    stopLivePauseGuard();
    dismissScalarSplitView();
    stopPreviewPollLoop();
    collectionPreviewSlots.forEach(function (slot) {
      if (slot.overlay) {
        revokePreviewObjectUrl(slot.overlay);
        slot.overlay.onload = null;
        slot.overlay.onerror = null;
        slot.overlay.removeAttribute("src");
        if (slot.overlay.parentElement) {
          slot.overlay.parentElement.removeChild(slot.overlay);
        }
      }
      if (slot.wrap) {
        slot.wrap.removeAttribute("data-datalab-preview-panel");
      }
    });
    collectionPreviewSlots.clear();
    document.querySelectorAll("[data-datalab-preview-overlay]").forEach(function (node) {
      revokePreviewObjectUrl(node);
      node.remove();
    });
    document.querySelectorAll("[data-datalab-preview-panel]").forEach(function (node) {
      node.removeAttribute("data-datalab-preview-panel");
    });
    clearReplayChromeMarks();
    resumeReplayTransportForPreview();
  }

  function syncCollectionPreviewOverlay() {
    document.documentElement.setAttribute(
      "data-datalab-collection-preview",
      collectionModeUi.mode === "preview" ? "1" : "0",
    );
    // Poll 214 preview only when remote preview is enabled and station heartbeat is online.
    if (collectionModeUi.mode === "preview" && previewModeAllowed()) {
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
      captureActive: zh
        ? "采集中远程实时已禁用，保护数据采集质量"
        : "Remote live disabled during capture",
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

  function previewModeAllowed() {
    return remotePreviewEnabled && collectionModeUi.remotePreviewAllowed;
  }

  function previewBlockedLabel() {
    var labels = collectionModeLabels();
    if (!remotePreviewEnabled) return "";
    if (!collectionModeUi.online) return labels.offline;
    if (collectionModeUi.captureState !== "idle") return labels.captureActive;
    return "";
  }

  function applyCollectionModeChromeVisual() {
    var labels = collectionModeLabels();
    var segPreview = collectionModeUi.segPreview;
    var segDataset = collectionModeUi.segDataset;
    var statusEl = collectionModeUi.statusEl;
    if (!segPreview || !segDataset) return;
    if (isParentChromeMode() && collectionModeUi.chromeRoot) {
      collectionModeUi.chromeRoot.hidden = true;
      return;
    }
    var isPreview = collectionModeUi.mode === "preview";
    segPreview.classList.toggle("is-active", isPreview);
    segDataset.classList.toggle("is-active", !isPreview);
    segPreview.setAttribute("aria-selected", isPreview ? "true" : "false");
    segDataset.setAttribute("aria-selected", !isPreview ? "true" : "false");
    segPreview.hidden = !remotePreviewEnabled;
    segPreview.disabled = !previewModeAllowed();
    segPreview.title = previewModeAllowed() ? "" : previewBlockedLabel();
    segDataset.disabled = false;
    segDataset.title = "";
    if (statusEl) {
      var blocked = previewBlockedLabel();
      if (blocked) {
        statusEl.hidden = false;
        statusEl.textContent = blocked;
        statusEl.classList.add("is-offline");
      } else {
        statusEl.hidden = true;
        statusEl.classList.remove("is-offline");
      }
    }
  }

  function selectCollectionMode(mode, options) {
    options = options || {};
    if (mode === "preview" && !previewModeAllowed()) mode = "dataset";
    if (mode === "preview" && previewModeAllowed()) {
      pauseReplayBeforeLiveSwitch();
    }
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
    if (!data || typeof data !== "object" || data.type !== "datalab-collection-mode") {
      if (data && data.type === "datalab-collection-pause-replay") {
        pauseReplayBeforeLiveSwitch();
      }
      return;
    }
    if (data.parentChrome) applyParentCollectionChrome();
    if (typeof data.remotePreview === "boolean") {
      remotePreviewEnabled = data.remotePreview;
    }
    if (typeof data.online === "boolean") {
      collectionModeUi.online = data.online;
    }
    if (typeof data.captureState === "string") {
      collectionModeUi.captureState = data.captureState;
    }
    if (typeof data.remotePreviewAllowed === "boolean") {
      collectionModeUi.remotePreviewAllowed = data.remotePreviewAllowed;
    } else if (typeof data.online === "boolean" || typeof data.captureState === "string") {
      collectionModeUi.remotePreviewAllowed =
        remotePreviewEnabled && collectionModeUi.online && collectionModeUi.captureState === "idle";
    }
    if (data.mode === "preview" || data.mode === "dataset") {
      if (data.mode === "preview" && previewModeAllowed()) {
        pauseReplayBeforeLiveSwitch();
      }
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
    captureLiveFeaturesPlaceholder();
    if (!pillInstalled) {
      installCollectionModeSwitcher();
    }
    if (collectionModeUi.mode === "preview" && previewModeAllowed()) {
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

  function scheduleEarlyFeaturesPlaceholderCapture() {
    if (g.__DATALAB_LIVE_FEATURES_PLACEHOLDER_CAPTURE_TIMER__) return;
    var attempts = 0;
    g.__DATALAB_LIVE_FEATURES_PLACEHOLDER_CAPTURE_TIMER__ = g.setInterval(function () {
      if (attempts === 0) {
        dismissAutoplayDialog();
        ensureTransportPaused();
      }
      captureLiveFeaturesPlaceholder();
      if (!liveFeaturesPlaceholderHtml() && attempts % 8 === 4) {
        clickAllFeaturesTab();
        g.setTimeout(captureLiveFeaturesPlaceholder, 150);
      }
      attempts += 1;
      if (liveFeaturesPlaceholderHtml() || attempts >= 200) {
        g.clearInterval(g.__DATALAB_LIVE_FEATURES_PLACEHOLDER_CAPTURE_TIMER__);
        g.__DATALAB_LIVE_FEATURES_PLACEHOLDER_CAPTURE_TIMER__ = null;
      }
    }, 50);
  }

  function schedule() {
    installLiveTransportPlayBlock();
    dismissAutoplayDialog();
    scheduleEarlyFeaturesPlaceholderCapture();
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
