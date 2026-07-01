/**
 * Draw HaMeR 2D hand keypoints on one or more inference camera panels (v8 QA color fix + compact joints).
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

  var LEGACY_TO_CANONICAL_VIDEO_KEY = {
    "observation.images.camera_head_left": "observation.images.camera_front_left",
    "observation.images.camera_head_right": "observation.images.camera_front_right",
    "observation.images.camera_depth_head": "observation.images.camera_rear_left",
    "observation.images.camera_02": "observation.images.camera_rear_right",
  };

  // RGB converted from hand_viz_style.HAND_COLORS_BGR (OpenCV BGR → canvas RGB).
  // left: 豆沙骨线 + 浅桃关节; right: 绿骨线 + 浅蓝关节
  var RENDER = {
    LINE_WIDTH: 2,
    WRIST_RADIUS: 3,
    JOINT_RADIUS: 1.5,
    ALPHA_HIGH: 0.95,
    ALPHA_LOW: 0.45,
    ALPHA_REPROJ_CAP: 0.55,
    REPROJ_THRESHOLD: 8.0,
    QUALITY_LOW: 2,
  };

  var HAND_COLORS_RGB = {
    left: { edge: [246, 130, 59], joint: [253, 197, 147] },
    right: { edge: [94, 197, 34], joint: [21, 204, 250] },
    unknown: { edge: [247, 85, 168], joint: [254, 180, 216] },
  };

  var state = {
    enabled: true,
    datasetId: null,
    payload: null,
    episodeIndex: "0",
    /** @type {Map<string, {lookup: Map<number, number[]>, payload: object}>} */
    cameraStates: new Map(),
    /** @type {Map<string, {video: HTMLVideoElement, wrap: HTMLElement, canvas: HTMLCanvasElement}>} */
    slots: new Map(),
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

  function rgb(c) {
    return "rgb(" + c[0] + "," + c[1] + "," + c[2] + ")";
  }

  function cameraPayloadFromSub(sub, rootPayload) {
    return {
      fps: sub.fps || rootPayload.fps || 20,
      width: sub.width,
      height: sub.height,
      frame_index: sub.frame_index || [],
      kp2d: sub.kp2d || [],
      hand_side: sub.hand_side || [],
      frame_quality: sub.frame_quality || [],
      reprojection_error: sub.reprojection_error || [],
      hands_mode: sub.hands_mode || rootPayload.hands_mode || "single",
      hands: sub.hands || null,
      sync_mode: sub.sync_mode || rootPayload.sync_mode || "frame_index",
    };
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

  function episodeKeyCandidates() {
    var ep = detectActiveEpisodeIndex();
    var n = parseInt(ep, 10);
    if (!Number.isFinite(n)) n = 0;
    var keys = [String(n), String(ep), String(n).padStart(6, "0")];
    var seen = {};
    var out = [];
    for (var i = 0; i < keys.length; i += 1) {
      if (!seen[keys[i]]) {
        seen[keys[i]] = true;
        out.push(keys[i]);
      }
    }
    return out;
  }

  function resolveEpisodeMap(rootPayload) {
    if (!rootPayload || !rootPayload.episodes) return { key: null, map: null };
    var eps = rootPayload.episodes;
    var candidates = episodeKeyCandidates();
    for (var i = 0; i < candidates.length; i += 1) {
      if (eps[candidates[i]]) {
        return { key: candidates[i], map: eps[candidates[i]] };
      }
    }
    var keys = Object.keys(eps);
    if (!keys.length) return { key: null, map: null };
    return { key: keys[0], map: eps[keys[0]] };
  }

  function resolveEpisodeCameraPayloads(rootPayload) {
    if (!rootPayload) return [];
    var resolved = resolveEpisodeMap(rootPayload);
    state.episodeIndex = resolved.key || detectActiveEpisodeIndex();

    if (rootPayload.version === 4 && resolved.map) {
      var epMap = resolved.map;
      var list4 = [];
      var vkeys4 = rootPayload.video_keys || Object.keys(epMap);
      for (var i4 = 0; i4 < vkeys4.length; i4 += 1) {
        var vk4 = canonicalVideoKey(vkeys4[i4]);
        var sub4 = epMap[vkeys4[i4]] || epMap[vk4];
        if (sub4) {
          list4.push({
            video_key: vk4,
            payload: cameraPayloadFromSub(sub4, rootPayload),
          });
        }
      }
      return list4;
    }

    if (rootPayload.version === 3 && resolved.map) {
      var sub3 = resolved.map;
      if (!sub3) return [];
      return [
        {
          video_key: canonicalVideoKey(sub3.video_key || rootPayload.video_key),
          payload: cameraPayloadFromSub(sub3, rootPayload),
        },
      ];
    }

    return [
      {
        video_key: canonicalVideoKey(rootPayload.video_key || "observation.images.camera_front_left"),
        payload: rootPayload,
      },
    ];
  }

  function refreshCameraStates() {
    state.cameraStates = new Map();
    if (!state.payload) return;
    var cams = resolveEpisodeCameraPayloads(state.payload);
    for (var i = 0; i < cams.length; i += 1) {
      var entry = cams[i];
      state.cameraStates.set(entry.video_key, {
        lookup: buildLookup(entry.payload),
        payload: entry.payload,
      });
    }
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
        refreshCameraStates();
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

  function frameIndexForVideo(video, payload) {
    var fps = payload.fps || 20;
    if (!video || !Number.isFinite(fps) || fps <= 0) return 0;
    var idx = Math.floor((video.currentTime || 0) * fps + 0.5);
    var frames = payload.frame_index || [];
    var maxIdx = frames.length ? Number(frames[frames.length - 1]) : idx;
    return Math.max(0, Math.min(idx, maxIdx));
  }

  function frameRowIndex(payload, frameIdx) {
    var frames = payload.frame_index || [];
    for (var i = 0; i < frames.length; i += 1) {
      if (Number(frames[i]) === frameIdx) return i;
    }
    return -1;
  }

  function isValidWrist(flat) {
    return flat && flat.length >= 2 && isValidJoint(flat[0], flat[1]);
  }

  function blendAlphaForRow(frameQuality, reprojErr) {
    var alpha = RENDER.ALPHA_HIGH;
    if (frameQuality !== undefined && Number(frameQuality) >= RENDER.QUALITY_LOW) {
      alpha = RENDER.ALPHA_LOW;
    }
    if (reprojErr !== undefined && Number(reprojErr) > RENDER.REPROJ_THRESHOLD) {
      alpha = Math.min(alpha, RENDER.ALPHA_REPROJ_CAP);
    }
    return alpha;
  }

  function blendAlpha(payload, frameIdx) {
    var row = frameRowIndex(payload, frameIdx);
    if (row < 0) return RENDER.ALPHA_HIGH;
    var fq = payload.frame_quality;
    var re = payload.reprojection_error;
    return blendAlphaForRow(fq ? fq[row] : undefined, re ? re[row] : undefined);
  }

  function blendAlphaForHand(handPayload, row) {
    var fq = handPayload.frame_quality;
    var re = handPayload.reprojection_error;
    return blendAlphaForRow(fq ? fq[row] : undefined, re ? re[row] : undefined);
  }

  function ensureOffscreen(canvas) {
    if (!canvas._datalabOffscreen) {
      canvas._datalabOffscreen = document.createElement("canvas");
    }
    if (canvas._datalabOffscreen.width !== canvas.width || canvas._datalabOffscreen.height !== canvas.height) {
      canvas._datalabOffscreen.width = canvas.width;
      canvas._datalabOffscreen.height = canvas.height;
    }
    return canvas._datalabOffscreen;
  }

  function scalePoints(flat, scaleX, scaleY) {
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
    return pts;
  }

  function drawHandLayer(bufCtx, pts, side, scale) {
    var colors = HAND_COLORS_RGB[side] || HAND_COLORS_RGB.unknown;
    if (!pts[0] || !pts[0].ok) return;
    var lw = Math.max(1, RENDER.LINE_WIDTH * scale);
    var wristR = Math.max(1.2, RENDER.WRIST_RADIUS * scale);
    var jointR = Math.max(0.8, RENDER.JOINT_RADIUS * scale);
    bufCtx.lineWidth = lw;
    bufCtx.lineCap = "round";
    bufCtx.lineJoin = "round";
    bufCtx.strokeStyle = rgb(colors.edge);
    bufCtx.fillStyle = rgb(colors.joint);
    for (var e = 0; e < HAND_EDGES.length; e += 1) {
      var a = pts[HAND_EDGES[e][0]];
      var b = pts[HAND_EDGES[e][1]];
      if (!a.ok || !b.ok) continue;
      bufCtx.beginPath();
      bufCtx.moveTo(a.x, a.y);
      bufCtx.lineTo(b.x, b.y);
      bufCtx.stroke();
    }
    for (var p = 0; p < pts.length; p += 1) {
      if (!pts[p].ok) continue;
      bufCtx.beginPath();
      bufCtx.arc(pts[p].x, pts[p].y, p === 0 ? wristR : jointR, 0, Math.PI * 2);
      bufCtx.fill();
    }
  }

  function compositeLayer(mainCtx, buffer, alpha) {
    mainCtx.save();
    mainCtx.globalAlpha = alpha;
    mainCtx.drawImage(buffer, 0, 0);
    mainCtx.restore();
  }

  function handsToDraw(payload, frameIdx, lookup) {
    var row = frameRowIndex(payload, frameIdx);
    var out = [];
    if (payload.hands_mode === "both" && payload.hands) {
      ["left", "right"].forEach(function (side) {
        var h = payload.hands[side];
        if (!h || !h.kp2d || row < 0) return;
        var flat = h.kp2d[row];
        if (!isValidWrist(flat)) return;
        out.push({ side: side, flat: flat, alpha: blendAlphaForHand(h, row) });
      });
      if (out.length) return out;
    }
    var flat = lookup.get(frameIdx);
    if (!isValidWrist(flat)) return [];
    out.push({
      side: handSideForFrame(payload, frameIdx),
      flat: flat,
      alpha: blendAlpha(payload, frameIdx),
    });
    return out;
  }

  function handSideForFrame(payload, frameIdx) {
    var row = frameRowIndex(payload, frameIdx);
    if (row < 0) return "unknown";
    var hs = payload.hand_side;
    if (hs && hs[row]) {
      var s = String(hs[row]).toLowerCase();
      if (s.indexOf("left") >= 0) return "left";
      if (s.indexOf("right") >= 0) return "right";
    }
    return "unknown";
  }

  function panelRenderScale(canvas) {
    var s = Math.min(canvas.width || 1, canvas.height || 1) / 320;
    return Math.max(0.55, Math.min(1.0, s));
  }

  function drawSkeleton(canvas, video, wrap, payload, lookup) {
    if (!canvas || !video || !wrap || !payload || !lookup) return;
    var display = syncCanvasToVideo(canvas, video, wrap);
    if (!display) return;

    var ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!state.enabled) return;

    var frameIdx = frameIndexForVideo(video, payload);
    var hands = handsToDraw(payload, frameIdx, lookup);
    if (!hands.length) return;

    var srcW = payload.width || video.videoWidth || 0;
    var srcH = payload.height || video.videoHeight || 0;
    var scaleX = srcW > 0 ? display.width / srcW : display.scale || 1;
    var scaleY = srcH > 0 ? display.height / srcH : display.scale || 1;

    var offscreen = ensureOffscreen(canvas);
    var bufCtx = offscreen.getContext("2d");
    if (!bufCtx) return;
    var scale = panelRenderScale(canvas);

    for (var hi = 0; hi < hands.length; hi += 1) {
      var hand = hands[hi];
      bufCtx.clearRect(0, 0, offscreen.width, offscreen.height);
      var pts = scalePoints(hand.flat, scaleX, scaleY);
      drawHandLayer(bufCtx, pts, hand.side, scale);
      compositeLayer(ctx, offscreen, hand.alpha);
    }
  }

  function ensureAllSlots() {
    state.slots = new Map();
    state.cameraStates.forEach(function (_camState, videoKey) {
      var slot = ensureCanvasSlot(videoKey);
      if (slot) state.slots.set(videoKey, slot);
    });
  }

  function paintLoop() {
    state.rafId = 0;
    if (!state.payload || state.cameraStates.size === 0) return;

    var needRefresh = false;
    state.cameraStates.forEach(function (camState, videoKey) {
      var slot = state.slots.get(videoKey);
      if (!slotIsLive(slot)) {
        needRefresh = true;
      }
    });
    if (needRefresh || state.slots.size !== state.cameraStates.size) {
      ensureAllSlots();
    }

    state.cameraStates.forEach(function (camState, videoKey) {
      var slot = state.slots.get(videoKey);
      if (!slot) return;
      drawSkeleton(slot.canvas, slot.video, slot.wrap, camState.payload, camState.lookup);
    });

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
      } else {
        state.slots.forEach(function (slot) {
          if (slot && slot.canvas) {
            var ctx = slot.canvas.getContext("2d");
            if (ctx) ctx.clearRect(0, 0, slot.canvas.width, slot.canvas.height);
          }
        });
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
    var dual = state.cameraStates.size > 1;
    label.textContent = isZh()
      ? on
        ? dual
          ? "双手 2D（相机视角）"
          : "手部 2D 标注"
        : dual
          ? "双手 2D（关）"
          : "手部 2D 标注（关）"
      : on
        ? dual
          ? "Hand 2D (cam L/R)"
          : "Hand 2D overlay"
        : dual
          ? "Hand 2D off"
          : "Hand 2D overlay (off)";
  }

  function deactivate() {
    stopPaintLoop();
    document.documentElement.removeAttribute("data-datalab-hand-kp2d");
    if (state.pill) {
      state.pill.remove();
      state.pill = null;
    }
    state.slots = new Map();
    state.cameraStates = new Map();
    state.datasetId = null;
    state.payload = null;
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
      refreshCameraStates();
      ensureAllSlots();
      updatePillLabel();
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
      state.cameraStates = new Map();
      state.slots = new Map();
    }
    activateForDataset(datasetId);
    if (state.payload) {
      var prevEp = state.episodeIndex;
      refreshCameraStates();
      if (prevEp !== state.episodeIndex) {
        ensureAllSlots();
      }
      updatePillLabel();
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
