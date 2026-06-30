/**
 * Draw HaMeR 2D hand keypoints (OpenPose 21-joint skeleton) on the inference camera panel.
 */
(function () {
  "use strict";

  var g = typeof globalThis !== "undefined" ? globalThis : window;
  if (g.__DATALAB_HAND_KP2D__) return;
  g.__DATALAB_HAND_KP2D__ = true;

  var HAND_EDGES = [
    [0, 1], [1, 2], [2, 3], [3, 4],
    [0, 5], [5, 6], [6, 7], [7, 8],
    [0, 9], [9, 10], [10, 11], [11, 12],
    [0, 13], [13, 14], [14, 15], [15, 16],
    [0, 17], [17, 18], [18, 19], [19, 20],
  ];

  // Legacy HaMeR / stream ingest names -> LeRobot v3 panel feature keys.
  var LEGACY_TO_CANONICAL_VIDEO_KEY = {
    "observation.images.camera_head_left": "observation.images.camera_front_left",
    "observation.images.camera_head_right": "observation.images.camera_front_right",
    "observation.images.camera_depth_head": "observation.images.camera_rear_left",
    "observation.images.camera_02": "observation.images.camera_rear_right",
  };

  var state = {
    enabled: true,
    datasetId: null,
    payload: null,
    lookup: null,
    activeVideoKey: null,
    activeEpisode: "0",
    slot: null,
    rafId: 0,
    observer: null,
    pill: null,
    loading: false,
    loadError: null,
    fetchPromise: null,
  };

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

  function isZh() {
    return pageLang().indexOf("zh") === 0;
  }

  function parseSampleDatasetId() {
    try {
      var raw = new URLSearchParams(g.location.search).get("url") || "";
      if (raw.indexOf("sample://") === 0) {
        return raw.replace(/^sample:\/\//, "").split("?")[0] || null;
      }
    } catch (e) {
      /* ignore */
    }
    return null;
  }

  function featureMatchesText(text, featureKey) {
    if (!text) return false;
    if (text.indexOf(featureKey) >= 0) return true;
    var short = featureKey.split(".").pop();
    return short ? text.indexOf(short) >= 0 : false;
  }

  function resolveVideoKeyCandidates(featureKey) {
    var out = [];
    var seen = {};
    function push(key) {
      if (!key || seen[key]) return;
      seen[key] = true;
      out.push(key);
    }
    push(featureKey);
    push(LEGACY_TO_CANONICAL_VIDEO_KEY[featureKey]);
    for (var legacy in LEGACY_TO_CANONICAL_VIDEO_KEY) {
      if (LEGACY_TO_CANONICAL_VIDEO_KEY[legacy] === featureKey) {
        push(legacy);
      }
    }
    return out;
  }

  function canonicalVideoKey(featureKey) {
    return LEGACY_TO_CANONICAL_VIDEO_KEY[featureKey] || featureKey;
  }

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

  function findFeatureVideoSlot(featureKey) {
    var videos = document.querySelectorAll("video");
    var best = null;
    for (var i = 0; i < videos.length; i++) {
      var video = videos[i];
      var node = video.parentElement;
      for (var depth = 0; depth < 18 && node; depth += 1) {
        var blob = (node.getAttribute("title") || "") + (node.textContent || "");
        if (featureMatchesText(blob, featureKey)) {
          if (!best || depth < best.depth) {
            best = {
              video: video,
              depth: depth,
              wrap: findPreviewPanelWrap(video) || video.parentElement,
            };
          }
          break;
        }
        node = node.parentElement;
      }
    }
    return best ? { video: best.video, wrap: best.wrap } : null;
  }

  function slotIsLive(slot) {
    return !!(slot && slot.canvas && slot.canvas.isConnected && slot.video && slot.video.isConnected);
  }

  function buildLookup(payload) {
    var lookup = new Map();
    var frames = payload.frame_index || [];
    var kp2d = payload.kp2d || [];
    for (var i = 0; i < frames.length; i += 1) {
      lookup.set(Number(frames[i]), kp2d[i] || null);
    }
    return lookup;
  }

  function detectActiveEpisodeIndex() {
    try {
      var q = new URLSearchParams(g.location.search).get("episode");
      if (q !== null && q !== "") return String(parseInt(q, 10) || 0);
    } catch (e) {
      /* ignore */
    }
    var selected = document.querySelector(
      '[aria-selected="true"], .selected, [data-selected="true"]',
    );
    if (selected) {
      var text = (selected.textContent || "").trim();
      var m = text.match(/#\s*(\d+)/) || text.match(/^(\d+)\b/);
      if (m) return String(parseInt(m[1], 10));
    }
    return "0";
  }

  function resolveEpisodePayload(rootPayload) {
    if (!rootPayload) return null;
    if (rootPayload.version === 3 && rootPayload.episodes) {
      var ep = detectActiveEpisodeIndex();
      var sub = rootPayload.episodes[ep] || rootPayload.episodes["0"];
      if (!sub) {
        var keys = Object.keys(rootPayload.episodes);
        sub = keys.length ? rootPayload.episodes[keys[0]] : null;
      }
      if (!sub) return null;
      return {
        fps: sub.fps || rootPayload.fps || 20,
        video_key: sub.video_key || rootPayload.video_key,
        frame_index: sub.frame_index || [],
        kp2d: sub.kp2d || [],
      };
    }
    return rootPayload;
  }

  function refreshActiveEpisodeLookup() {
    if (!state.payload) return;
    var epPayload = resolveEpisodePayload(state.payload);
    if (!epPayload) return;
    state.lookup = buildLookup(epPayload);
    state.activeVideoKey = canonicalVideoKey(
      epPayload.video_key || "observation.images.camera_front_left",
    );
    state.activeEpisode = detectActiveEpisodeIndex();
  }

  function fetchHandKp2d(datasetId) {
    if (state.payload && state.datasetId === datasetId) {
      return Promise.resolve(state.payload);
    }
    if (state.fetchPromise) {
      return state.fetchPromise;
    }
    state.loading = true;
    state.loadError = null;
    var url = "/lerobot/api/sample/" + encodeURIComponent(datasetId) + "/hand-kp2d.json";
    state.fetchPromise = fetch(url, { credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) throw new Error("hand_kp2d_http_" + res.status);
        return res.json();
      })
      .then(function (payload) {
        state.datasetId = datasetId;
        state.payload = payload;
        refreshActiveEpisodeLookup();
        state.loading = false;
        state.loadError = null;
        return payload;
      })
      .catch(function (err) {
        state.loading = false;
        state.loadError = err;
        return null;
      })
      .finally(function () {
        state.fetchPromise = null;
      });
    return state.fetchPromise;
  }

  function ensureCanvasSlot(featureKey) {
    var candidates = resolveVideoKeyCandidates(featureKey);
    var found = null;
    for (var ci = 0; ci < candidates.length; ci += 1) {
      found = findFeatureVideoSlot(candidates[ci]);
      if (found && found.wrap && found.video) break;
      found = null;
    }
    if (!found || !found.wrap || !found.video) return null;

    var wrap = found.wrap;
    wrap.setAttribute("data-datalab-hand-kp2d-panel", "1");
    if (g.getComputedStyle(wrap).position === "static") {
      wrap.style.position = "relative";
    }

    var canvas = wrap.querySelector("canvas.datalab-hand-kp2d-overlay");
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.className = "datalab-hand-kp2d-overlay";
      canvas.setAttribute("aria-hidden", "true");
      wrap.appendChild(canvas);
    } else if (canvas.parentElement !== wrap) {
      wrap.appendChild(canvas);
    }

    if (!canvas._datalabResizeObs) {
      var ro = new ResizeObserver(function () {
        syncCanvasToVideo(canvas, found.video, wrap);
      });
      ro.observe(wrap);
      ro.observe(found.video);
      canvas._datalabResizeObs = ro;
    }

    return { video: found.video, wrap: wrap, canvas: canvas };
  }

  function containedVideoRect(video) {
    var cw = video.clientWidth || 0;
    var ch = video.clientHeight || 0;
    var vw = video.videoWidth || 0;
    var vh = video.videoHeight || 0;
    if (cw <= 0 || ch <= 0) return null;
    if (vw <= 0 || vh <= 0) {
      return { offsetX: 0, offsetY: 0, width: cw, height: ch, scale: 1 };
    }
    var scale = Math.min(cw / vw, ch / vh);
    var width = vw * scale;
    var height = vh * scale;
    return {
      offsetX: (cw - width) / 2,
      offsetY: (ch - height) / 2,
      width: width,
      height: height,
      scale: scale,
    };
  }

  function syncCanvasToVideo(canvas, video, wrap) {
    var rect = containedVideoRect(video);
    if (!rect) return null;
    var vr = video.getBoundingClientRect();
    var wr = wrap.getBoundingClientRect();
    var left = vr.left - wr.left + rect.offsetX;
    var top = vr.top - wr.top + rect.offsetY;
    var w = Math.max(1, Math.round(rect.width));
    var h = Math.max(1, Math.round(rect.height));
    canvas.style.left = left + "px";
    canvas.style.top = top + "px";
    canvas.style.width = w + "px";
    canvas.style.height = h + "px";
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    return rect;
  }

  function isValidJoint(u, v) {
    return Number.isFinite(u) && Number.isFinite(v) && (u > 1 || v > 1);
  }

  function frameIndexForVideo(video, fps) {
    if (!video || !Number.isFinite(fps) || fps <= 0) return 0;
    return Math.max(0, Math.round((video.currentTime || 0) * fps));
  }

  function drawSkeleton(canvas, video, wrap, payload, lookup) {
    if (!canvas || !video || !wrap || !payload || !lookup) return;
    var display = syncCanvasToVideo(canvas, video, wrap);
    if (!display) return;

    var ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!state.enabled) return;

    var frameIdx = frameIndexForVideo(video, payload.fps || 15);
    var flat = lookup.get(frameIdx);
    if (!flat || flat.length < 42) return;

    var scale = display.scale || 1;
    var srcW = payload.width || video.videoWidth || 0;
    var srcH = payload.height || video.videoHeight || 0;
    var scaleX = srcW > 0 ? display.width / srcW : scale;
    var scaleY = srcH > 0 ? display.height / srcH : scale;
    var pts = [];
    for (var j = 0; j < 21; j += 1) {
      var u = flat[j * 2];
      var v = flat[j * 2 + 1];
      pts.push({
        x: u * scaleX,
        y: v * scaleY,
        ok: isValidJoint(u, v),
      });
    }
    if (!pts[0].ok) return;

    ctx.lineWidth = 2;
    ctx.strokeStyle = "rgba(34, 197, 94, 0.95)";
    ctx.fillStyle = "rgba(250, 204, 21, 0.95)";

    for (var e = 0; e < HAND_EDGES.length; e += 1) {
      var a = pts[HAND_EDGES[e][0]];
      var b = pts[HAND_EDGES[e][1]];
      if (!a.ok || !b.ok) continue;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }

    for (var p = 0; p < pts.length; p += 1) {
      if (!pts[p].ok) continue;
      ctx.beginPath();
      ctx.arc(pts[p].x, pts[p].y, p === 0 ? 4.5 : 3.2, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function paintLoop() {
    state.rafId = 0;
    if (!state.payload) return;
    if (!slotIsLive(state.slot)) {
      var epPayload = resolveEpisodePayload(state.payload) || state.payload;
      var vk =
        state.activeVideoKey ||
        canonicalVideoKey(epPayload.video_key || "observation.images.camera_front_left");
      state.slot = ensureCanvasSlot(vk);
    }
    if (state.slot) {
      var epPayload2 = resolveEpisodePayload(state.payload) || state.payload;
      drawSkeleton(state.slot.canvas, state.slot.video, state.slot.wrap, epPayload2, state.lookup);
    }
    if (state.enabled) {
      state.rafId = g.requestAnimationFrame(paintLoop);
    }
  }

  function startPaintLoop() {
    if (state.rafId) return;
    state.rafId = g.requestAnimationFrame(paintLoop);
  }

  function stopPaintLoop() {
    if (!state.rafId) return;
    g.cancelAnimationFrame(state.rafId);
    state.rafId = 0;
  }

  function ensurePill() {
    if (state.pill && state.pill.isConnected) return state.pill;
    var pill = document.createElement("button");
    pill.type = "button";
    pill.className = "datalab-hand-kp2d-pill";
    pill.setAttribute("aria-pressed", state.enabled ? "true" : "false");
    pill.innerHTML =
      '<span class="datalab-hand-kp2d-pill-dot"></span><span class="datalab-hand-kp2d-pill-label"></span>';
    pill.addEventListener("click", function () {
      state.enabled = !state.enabled;
      pill.setAttribute("aria-pressed", state.enabled ? "true" : "false");
      updatePillLabel();
      if (state.enabled) {
        startPaintLoop();
      } else if (state.slot && state.slot.canvas) {
        var ctx = state.slot.canvas.getContext("2d");
        if (ctx) ctx.clearRect(0, 0, state.slot.canvas.width, state.slot.canvas.height);
        stopPaintLoop();
      }
    });
    document.body.appendChild(pill);
    state.pill = pill;
    updatePillLabel();
    return pill;
  }

  function updatePillLabel() {
    if (!state.pill) return;
    var label = state.pill.querySelector(".datalab-hand-kp2d-pill-label");
    if (!label) return;
    var on = state.enabled;
    label.textContent = isZh()
      ? on
        ? "手部 2D 标注"
        : "手部 2D 标注（关）"
      : on
        ? "Hand 2D overlay"
        : "Hand 2D overlay (off)";
  }

  function deactivate() {
    stopPaintLoop();
    document.documentElement.removeAttribute("data-datalab-hand-kp2d");
    if (state.pill) {
      state.pill.remove();
      state.pill = null;
    }
    state.slot = null;
    state.datasetId = null;
    state.payload = null;
    state.lookup = null;
    state.fetchPromise = null;
  }

  function activateForDataset(datasetId) {
    fetchHandKp2d(datasetId).then(function (payload) {
      if (!payload) {
        deactivate();
        return;
      }
      document.documentElement.setAttribute("data-datalab-hand-kp2d", "1");
      ensurePill();
      refreshActiveEpisodeLookup();
      var vk =
        state.activeVideoKey ||
        canonicalVideoKey(payload.video_key || "observation.images.camera_front_left");
      state.slot = ensureCanvasSlot(vk);
      startPaintLoop();
    });
  }

  function refresh() {
    var datasetId = parseSampleDatasetId();
    if (!datasetId) {
      deactivate();
      return;
    }
    if (state.datasetId !== datasetId) {
      state.payload = null;
      state.lookup = null;
      state.slot = null;
    }
    activateForDataset(datasetId);
    if (state.payload) {
      var prevEp = state.activeEpisode;
      refreshActiveEpisodeLookup();
      var epChanged = prevEp !== state.activeEpisode;
      var vk =
        state.activeVideoKey ||
        canonicalVideoKey(
          (resolveEpisodePayload(state.payload) || {}).video_key ||
            "observation.images.camera_front_left",
        );
      if (epChanged || !slotIsLive(state.slot)) {
        state.slot = ensureCanvasSlot(vk);
      }
      if (state.enabled) startPaintLoop();
    }
  }

  function scheduleRefresh() {
    g.clearTimeout(scheduleRefresh._t);
    scheduleRefresh._t = g.setTimeout(refresh, 120);
  }

  function install() {
    refresh();
    if (!state.observer) {
      state.observer = new MutationObserver(scheduleRefresh);
      state.observer.observe(document.documentElement, { childList: true, subtree: true });
    }
    g.addEventListener("popstate", scheduleRefresh);
    g.addEventListener("hashchange", scheduleRefresh);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install);
  } else {
    install();
  }
})();
