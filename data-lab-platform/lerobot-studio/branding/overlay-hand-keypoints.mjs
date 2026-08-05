/**
 * Hand 2D overlay for LeRobot Viewer (ES module entry).
 */
import {
  OVERLAY_VERSION,
  OVERLAY_RENDER_STYLE,
  HAND_COLORS_RGB,
  RENDER,
  buildLookup,
  buildEpisodeFingerprintIndex,
  cameraShortName,
  canonicalVideoKey,
  featureMatchesText,
  handsToDraw,
  isValidJoint,
  normalizeEpisodeNumber,
  parseEpisodeFromLabel,
  resolveActiveEpisodeIndex,
  resolveEpisodeCameraPayloads,
  resolveEpisodeMap,
  resolveFrameIndexForVideo,
  resolveRenderStyle,
} from "./overlay-hand-keypoints-lib.mjs";
import {
  buildRichHandDrawPlan,
  drawRichHandPlan,
  scalePointsToCanvas,
} from "./overlay-hand-render-rich.mjs";
import { bindVideoFrameUpdates } from "./overlay-video-sync.mjs";

const HAND_EDGES = [
  [0, 1], [1, 2], [2, 3], [3, 4],
  [0, 5], [5, 6], [6, 7], [7, 8],
  [0, 9], [9, 10], [10, 11], [11, 12],
  [0, 13], [13, 14], [14, 15], [15, 16],
  [0, 17], [17, 18], [18, 19], [19, 20],
];

const LEGACY_TO_CANONICAL_VIDEO_KEY = {
  "observation.images.camera_head_left": "observation.images.camera_front_left",
  "observation.images.camera_head_right": "observation.images.camera_front_right",
  "observation.images.camera_depth_head": "observation.images.camera_rear_left",
  "observation.images.camera_02": "observation.images.camera_rear_right",
};

function resolveVideoKeyCandidatesLocal(featureKey) {
  const out = [];
  const seen = new Set();
  function push(key) {
    if (!key || seen.has(key)) return;
    seen.add(key);
    out.push(key);
  }
  push(featureKey);
  push(LEGACY_TO_CANONICAL_VIDEO_KEY[featureKey]);
  for (const legacy in LEGACY_TO_CANONICAL_VIDEO_KEY) {
    if (LEGACY_TO_CANONICAL_VIDEO_KEY[legacy] === featureKey) push(legacy);
  }
  return out;
}

const g = globalThis;

if (!g.__DATALAB_HAND_KP2D__) {
  g.__DATALAB_HAND_KP2D__ = true;

  const STORAGE_RENDER_STYLE_KEY = "datalab-hand-render-style";

  function readStoredRenderStyle() {
    try {
      const stored = g.localStorage?.getItem(STORAGE_RENDER_STYLE_KEY);
      if (stored === OVERLAY_RENDER_STYLE.CLASSIC || stored === OVERLAY_RENDER_STYLE.RICH) {
        return stored;
      }
    } catch {
      /* ignore */
    }
    return null;
  }

  function persistRenderStyle(style) {
    try {
      g.localStorage?.setItem(STORAGE_RENDER_STYLE_KEY, style);
    } catch {
      /* ignore */
    }
  }

  const state = {
    enabled: true,
    datasetId: null,
    payload: null,
    episodeIndex: "0",
    userClickedEpisode: null,
    cameraStates: new Map(),
    slots: new Map(),
    videoSyncCleanups: new Map(),
    observer: null,
    pill: null,
    loading: false,
    loadError: null,
    fetchPromise: null,
    renderStylePreference: readStoredRenderStyle(),
  };

  function activeRenderStyle(payload) {
    return resolveRenderStyle({
      payload,
      rootPayload: state.payload,
      search: g.location.search,
      preference: state.renderStylePreference,
      storage: g.localStorage,
    });
  }

  function renderStyleLabel(style) {
    if (style === OVERLAY_RENDER_STYLE.RICH) return isZh() ? "富样式" : "Rich";
    return isZh() ? "经典" : "Classic";
  }

  function toggleRenderStyle() {
    const next =
      activeRenderStyle(state.payload) === OVERLAY_RENDER_STYLE.RICH
        ? OVERLAY_RENDER_STYLE.CLASSIC
        : OVERLAY_RENDER_STYLE.RICH;
    state.renderStylePreference = next;
    persistRenderStyle(next);
    document.documentElement.setAttribute("data-datalab-hand-render-style", next);
    updatePillLabel();
    if (state.enabled) startPaintLoop();
  }

  function pageLang() {
    try {
      const q = new URLSearchParams(g.location.search).get("lang") || "";
      const html = document.documentElement?.lang || "";
      return (q || html || "en").toLowerCase();
    } catch {
      return "en";
    }
  }

  function isZh() {
    return pageLang().indexOf("zh") === 0;
  }

  function parseSampleDatasetId() {
    try {
      const raw = new URLSearchParams(g.location.search).get("url") || "";
      if (raw.indexOf("sample://") === 0) {
        return raw.replace(/^sample:\/\//, "").split("?")[0] || null;
      }
      const httpMatch = raw.match(/\/api\/sample\/([^/?#]+)\/dataset\/?/i);
      if (httpMatch) return decodeURIComponent(httpMatch[1]);
    } catch {
      /* ignore */
    }
    return null;
  }

  function collectHeaderNodes() {
    const out = [];
    const nodes = document.querySelectorAll(
      'h1,h2,h3,h4,[role="heading"],[class*="title"],[class*="Title"],[class*="episode"],[class*="Episode"]'
    );
    for (const node of nodes) {
      const t = (node.textContent || "").trim();
      if (!t || !/#\s*\d+/.test(t)) continue;
      const ariaCurrent =
        node.getAttribute("aria-current") === "true" ||
        node.getAttribute("aria-selected") === "true" ||
        !!node.closest('[aria-current="true"],[aria-selected="true"]');
      const inEpisodeSidebar = !!node.closest(
        '[data-datalab-episodes-sidebar], [data-datalab-episodes-panel]'
      );
      const inNavigation = !!node.closest(
        'nav, aside, [role="navigation"], [class*="sidebar" i], [class*="Sidebar" i], [class*="episodes-list" i]'
      );
      const inMainContent = !!node.closest('.dv-content-container, main, [role="main"]');
      out.push({
        text: t,
        tagName: node.tagName,
        role: node.getAttribute("role") || "",
        ariaCurrent,
        inEpisodeSidebar,
        inNavigation,
        inMainContent,
      });
    }
    return out;
  }

  function collectAriaCurrentNodes() {
    const nodes = document.querySelectorAll('[aria-current="true"]');
    const out = [];
    for (const node of nodes) {
      out.push({
        ariaLabel: node.getAttribute("aria-label") || "",
        text: node.textContent || "",
      });
    }
    return out;
  }

  function collectEpisodeAriaNodes() {
    const out = [];
    const seen = new Set();
    const nodes = document.querySelectorAll(
      '[role="button"][aria-label*="Episode"], [role="button"][aria-label*="episode"], [aria-current="true"]'
    );
    for (const node of nodes) {
      const ariaLabel = node.getAttribute("aria-label") || "";
      const text = (node.textContent || "").trim();
      if (!/Episode|episode|#\s*\d/.test(`${ariaLabel} ${text}`)) continue;
      const key = `${ariaLabel}|${text.slice(0, 80)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({
        ariaLabel,
        text,
        ariaCurrent:
          node.getAttribute("aria-current") === "true" ||
          node.getAttribute("aria-selected") === "true",
      });
    }
    return out;
  }

  function syncEpisodeUrl(ep) {
    try {
      const url = new URL(g.location.href);
      const cur = url.searchParams.get("episode");
      const next = String(normalizeEpisodeNumber(ep));
      if (cur === next) return;
      url.searchParams.set("episode", next);
      g.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
    } catch {
      /* ignore */
    }
  }

  function detectActiveEpisodeIndex() {
    return resolveActiveEpisodeIndex({
      search: g.location.search,
      lastEpisodeIndex: state.episodeIndex,
      clickedEpisode: state.userClickedEpisode,
      headerNodes: collectHeaderNodes(),
      episodeAriaNodes: collectEpisodeAriaNodes(),
      ariaCurrentNodes: collectAriaCurrentNodes(),
      rootPayload: state.payload,
    });
  }

  function activeSessionFingerprint() {
    const { byFingerprint } = buildEpisodeFingerprintIndex(state.payload);
    const ep = String(state.episodeIndex);
    for (const [fp, mappedEp] of Object.entries(byFingerprint)) {
      if (mappedEp === ep) return fp;
    }
    return null;
  }

  function findPreviewPanelWrap(video) {
    let node = video.parentElement;
    while (node && node !== document.body) {
      if (node.classList?.contains("dv-content-container")) return node;
      node = node.parentElement;
    }
    return video.parentElement;
  }

  function findFeatureVideoSlot(featureKey, skipVideos) {
    const videos = document.querySelectorAll("video");
    let best = null;
    for (const video of videos) {
      if (skipVideos?.has(video)) continue;
      let node = video.parentElement;
      for (let depth = 0; depth < 18 && node; depth += 1) {
        const blob = (node.getAttribute("title") || "") + (node.textContent || "");
        if (featureMatchesText(blob, featureKey)) {
          if (!best || depth < best.depth) {
            best = {
              video,
              depth,
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

  function findFeatureVideoSlotByIndex(index, skipVideos) {
    const videos = document.querySelectorAll("video");
    let n = 0;
    for (const video of videos) {
      if (skipVideos?.has(video)) continue;
      if (n === index) {
        return { video, wrap: findPreviewPanelWrap(video) || video.parentElement };
      }
      n += 1;
    }
    return null;
  }

  function orderedCameraKeys() {
    const keys = [];
    const wanted = new Set(state.cameraStates.keys());
    const order = state.payload?.video_keys || [];
    for (const vkRaw of order) {
      const vk = canonicalVideoKey(vkRaw);
      if (wanted.has(vk) && !keys.includes(vk)) keys.push(vk);
    }
    for (const vk of state.cameraStates.keys()) {
      if (!keys.includes(vk)) keys.push(vk);
    }
    return keys;
  }

  function refreshCameraStates() {
    state.cameraStates = new Map();
    if (!state.payload) return;
    const activeEp = detectActiveEpisodeIndex();
    const resolved = resolveEpisodeMap(state.payload, activeEp);
    if (!resolved.map) {
      state.episodeIndex = activeEp;
      return;
    }
    state.episodeIndex = String(resolved.episodeNumber ?? normalizeEpisodeNumber(activeEp));
    syncEpisodeUrl(state.episodeIndex);
    const cams = resolveEpisodeCameraPayloads(state.payload, activeEp);
    for (const entry of cams) {
      state.cameraStates.set(entry.video_key, {
        lookup: buildLookup(entry.payload),
        payload: entry.payload,
      });
    }
    document.documentElement.setAttribute("data-datalab-hand-kp2d-episode", state.episodeIndex);
    const renderStyle = activeRenderStyle(state.payload);
    document.documentElement.setAttribute("data-datalab-hand-render-style", renderStyle);
    const fp = activeSessionFingerprint();
    if (fp) document.documentElement.setAttribute("data-datalab-hand-kp2d-session", fp);
    else document.documentElement.removeAttribute("data-datalab-hand-kp2d-session");
  }

  function clearAllCanvases() {
    state.slots.forEach((slot) => {
      const ctx = slot?.canvas?.getContext("2d");
      if (ctx) ctx.clearRect(0, 0, slot.canvas.width, slot.canvas.height);
    });
  }

  function fetchHandKp2d(datasetId) {
    if (state.payload && state.datasetId === datasetId) {
      return Promise.resolve(state.payload);
    }
    if (state.fetchPromise) return state.fetchPromise;
    state.loading = true;
    state.loadError = null;
    const url = `/lerobot/api/sample/${encodeURIComponent(datasetId)}/hand-kp2d.json`;
    state.fetchPromise = fetch(url, { credentials: "same-origin" })
      .then((res) => {
        if (!res.ok) throw new Error(`hand_kp2d_http_${res.status}`);
        return res.json();
      })
      .then((payload) => {
        state.datasetId = datasetId;
        state.payload = payload;
        refreshCameraStates();
        state.loading = false;
        state.loadError = null;
        return payload;
      })
      .catch((err) => {
        state.loading = false;
        state.loadError = err;
        return null;
      })
      .finally(() => {
        state.fetchPromise = null;
      });
    return state.fetchPromise;
  }

  function ensureCanvasSlot(featureKey, slotIndex, skipVideos) {
    let found = null;
    for (const candidate of resolveVideoKeyCandidatesLocal(featureKey)) {
      found = findFeatureVideoSlot(candidate, skipVideos);
      if (found?.wrap && found?.video) break;
      found = null;
    }
    if (!found && slotIndex != null) {
      found = findFeatureVideoSlotByIndex(slotIndex, skipVideos);
    }
    if (!found?.wrap || !found?.video) return null;

    const { wrap, video } = found;
    wrap.setAttribute("data-datalab-hand-kp2d-panel", "1");
    wrap.setAttribute("data-datalab-hand-kp2d-camera", cameraShortName(featureKey));
    wrap.setAttribute("data-datalab-hand-kp2d-version", String(OVERLAY_VERSION));
    if (g.getComputedStyle(wrap).position === "static") wrap.style.position = "relative";

    let canvas = wrap.querySelector("canvas.datalab-hand-kp2d-overlay");
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.className = "datalab-hand-kp2d-overlay";
      canvas.setAttribute("aria-hidden", "true");
      wrap.appendChild(canvas);
    } else if (canvas.parentElement !== wrap) {
      wrap.appendChild(canvas);
    }

    if (!canvas._datalabResizeObs) {
      const ro = new ResizeObserver(() => syncCanvasToVideo(canvas, video, wrap));
      ro.observe(wrap);
      ro.observe(video);
      canvas._datalabResizeObs = ro;
    }

    return { video, wrap, canvas };
  }

  function containedVideoRect(video) {
    const cw = video.clientWidth || 0;
    const ch = video.clientHeight || 0;
    const vw = video.videoWidth || 0;
    const vh = video.videoHeight || 0;
    if (cw <= 0 || ch <= 0) return null;
    if (vw <= 0 || vh <= 0) {
      return { offsetX: 0, offsetY: 0, width: cw, height: ch, scale: 1 };
    }
    const scale = Math.min(cw / vw, ch / vh);
    const width = vw * scale;
    const height = vh * scale;
    return {
      offsetX: (cw - width) / 2,
      offsetY: (ch - height) / 2,
      width,
      height,
      scale,
    };
  }

  function syncCanvasToVideo(canvas, video, wrap) {
    const rect = containedVideoRect(video);
    if (!rect) return null;
    const vr = video.getBoundingClientRect();
    const wr = wrap.getBoundingClientRect();
    const left = vr.left - wr.left + rect.offsetX;
    const top = vr.top - wr.top + rect.offsetY;
    const w = Math.max(1, Math.round(rect.width));
    const h = Math.max(1, Math.round(rect.height));
    canvas.style.left = `${left}px`;
    canvas.style.top = `${top}px`;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    return rect;
  }

  function frameIndexForVideo(video, payload) {
    return resolveFrameIndexForVideo(video, payload, {
      headerNodes: collectHeaderNodes(),
      doc: document,
    });
  }

  function rgb(c) {
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  }

  function ensureOffscreen(canvas) {
    if (!canvas._datalabOffscreen) canvas._datalabOffscreen = document.createElement("canvas");
    if (canvas._datalabOffscreen.width !== canvas.width || canvas._datalabOffscreen.height !== canvas.height) {
      canvas._datalabOffscreen.width = canvas.width;
      canvas._datalabOffscreen.height = canvas.height;
    }
    return canvas._datalabOffscreen;
  }

  function scalePoints(flat, scaleX, scaleY) {
    const pts = [];
    for (let j = 0; j < 21; j += 1) {
      const u = flat[j * 2];
      const v = flat[j * 2 + 1];
      pts.push({ x: u * scaleX, y: v * scaleY, ok: isValidJoint(u, v) });
    }
    return pts;
  }

  function drawHandLayerClassic(bufCtx, pts, side, scale) {
    const colors = HAND_COLORS_RGB[side] || HAND_COLORS_RGB.unknown;
    if (!pts[0]?.ok) return;
    const lw = Math.max(1, RENDER.LINE_WIDTH * scale);
    const wristR = Math.max(1.2, RENDER.WRIST_RADIUS * scale);
    const jointR = Math.max(0.8, RENDER.JOINT_RADIUS * scale);
    bufCtx.lineWidth = lw;
    bufCtx.lineCap = "round";
    bufCtx.lineJoin = "round";
    bufCtx.strokeStyle = rgb(colors.edge);
    bufCtx.fillStyle = rgb(colors.joint);
    for (const [aIdx, bIdx] of HAND_EDGES) {
      const a = pts[aIdx];
      const b = pts[bIdx];
      if (!a.ok || !b.ok) continue;
      bufCtx.beginPath();
      bufCtx.moveTo(a.x, a.y);
      bufCtx.lineTo(b.x, b.y);
      bufCtx.stroke();
    }
    for (let p = 0; p < pts.length; p += 1) {
      if (!pts[p].ok) continue;
      bufCtx.beginPath();
      bufCtx.arc(pts[p].x, pts[p].y, p === 0 ? wristR : jointR, 0, Math.PI * 2);
      bufCtx.fill();
    }
  }

  function drawHandLayerRich(bufCtx, hand, scaleX, scaleY, scale) {
    const pts = scalePointsToCanvas(hand.flat, scaleX, scaleY);
    if (!pts[0]?.ok) return;
    const plan = buildRichHandDrawPlan(pts, hand.side, scale, hand.alpha, hand.handId);
    drawRichHandPlan(bufCtx, plan);
  }

  function compositeLayer(mainCtx, buffer, alpha) {
    mainCtx.save();
    mainCtx.globalAlpha = alpha;
    mainCtx.drawImage(buffer, 0, 0);
    mainCtx.restore();
  }

  function panelRenderScale(canvas) {
    const s = Math.min(canvas.width || 1, canvas.height || 1) / 320;
    return Math.max(0.55, Math.min(1.0, s));
  }

  function drawSkeleton(canvas, video, wrap, payload, lookup) {
    if (!canvas || !video || !wrap || !payload || !lookup) return;
    const display = syncCanvasToVideo(canvas, video, wrap);
    if (!display) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!state.enabled) return;

    const frameIdx = frameIndexForVideo(video, payload);
    const hands = handsToDraw(payload, frameIdx, lookup);
    if (!hands.length) return;

    const srcW = payload.width || video.videoWidth || 0;
    const srcH = payload.height || video.videoHeight || 0;
    const scaleX = srcW > 0 ? display.width / srcW : display.scale || 1;
    const scaleY = srcH > 0 ? display.height / srcH : display.scale || 1;
    const offscreen = ensureOffscreen(canvas);
    const bufCtx = offscreen.getContext("2d");
    if (!bufCtx) return;
    const scale = panelRenderScale(canvas);
    const renderStyle = activeRenderStyle(payload);
    wrap.setAttribute("data-datalab-hand-kp2d-render-style", renderStyle);

    for (const hand of hands) {
      bufCtx.clearRect(0, 0, offscreen.width, offscreen.height);
      if (renderStyle === OVERLAY_RENDER_STYLE.RICH) {
        drawHandLayerRich(bufCtx, hand, scaleX, scaleY, scale);
        compositeLayer(ctx, offscreen, 1);
      } else {
        const pts = scalePoints(hand.flat, scaleX, scaleY);
        drawHandLayerClassic(bufCtx, pts, hand.side, scale);
        compositeLayer(ctx, offscreen, hand.alpha);
      }
    }
  }

  function ensureAllSlots() {
    state.slots = new Map();
    const skipVideos = new Set();
    const keys = orderedCameraKeys();
    for (let i = 0; i < keys.length; i += 1) {
      const videoKey = keys[i];
      const slot = ensureCanvasSlot(videoKey, i, skipVideos);
      if (slot) {
        skipVideos.add(slot.video);
        state.slots.set(videoKey, slot);
      }
    }
  }

  function slotIsLive(slot) {
    return !!(slot?.canvas?.isConnected && slot?.video?.isConnected);
  }

  function refreshEpisodeIfNeeded() {
    const wantEp = normalizeEpisodeNumber(detectActiveEpisodeIndex());
    if (wantEp !== normalizeEpisodeNumber(state.episodeIndex)) {
      refreshCameraStates();
      ensureAllSlots();
      updatePillLabel();
      return true;
    }
    return false;
  }

  function ensureSlotsLive() {
    let needRefresh = false;
    for (const [videoKey] of state.cameraStates) {
      if (!slotIsLive(state.slots.get(videoKey))) needRefresh = true;
    }
    if (needRefresh || state.slots.size !== state.cameraStates.size) {
      ensureAllSlots();
      return true;
    }
    return false;
  }

  function paintSlot(videoKey) {
    const camState = state.cameraStates.get(videoKey);
    const slot = state.slots.get(videoKey);
    if (!camState || !slot) return;
    drawSkeleton(slot.canvas, slot.video, slot.wrap, camState.payload, camState.lookup);
  }

  function paintAllSlots() {
    if (!state.payload || state.cameraStates.size === 0) return;
    if (state.slots.size < state.cameraStates.size) {
      ensureAllSlots();
      if (state.slots.size > 0) attachVideoSync();
    }
    for (const [videoKey] of state.cameraStates) {
      paintSlot(videoKey);
    }
  }

  function detachVideoSync() {
    for (const cleanup of state.videoSyncCleanups.values()) {
      cleanup();
    }
    state.videoSyncCleanups.clear();
  }

  function attachVideoSync() {
    detachVideoSync();
    if (!state.enabled || !state.payload) return;
    const fps = Number(state.payload?.fps) || 30;
    for (const [videoKey, slot] of state.slots) {
      if (!slot?.video) continue;
      const cleanup = bindVideoFrameUpdates(
        slot.video,
        () => {
          const epChanged = refreshEpisodeIfNeeded();
          const slotsChanged = ensureSlotsLive();
          if (epChanged || slotsChanged) {
            startPaintLoop();
            return;
          }
          paintSlot(videoKey);
        },
        { fps },
      );
      state.videoSyncCleanups.set(videoKey, cleanup);
    }
  }

  function startPaintLoop() {
    refreshEpisodeIfNeeded();
    ensureSlotsLive();
    attachVideoSync();
    paintAllSlots();
  }

  function stopPaintLoop() {
    detachVideoSync();
  }

  function updatePillLabel() {
    const label = state.pill?.querySelector(".datalab-hand-kp2d-pill-label");
    if (!label) return;
    const on = state.enabled;
    const dual = state.cameraStates.size > 1;
    const ep = state.episodeIndex;
    const fp = activeSessionFingerprint();
    const epTag = fp ? `ep${ep} · ${fp}` : `ep${ep}`;
    const styleTag = renderStyleLabel(activeRenderStyle(state.payload));
    label.textContent = isZh()
      ? on
        ? dual
          ? `双手 2D · ${styleTag} · ${epTag}`
          : `手部 2D · ${styleTag} · ${epTag}`
        : dual
          ? "双手 2D（关）"
          : "手部 2D 标注（关）"
      : on
        ? dual
          ? `Hand 2D · ${styleTag} · ${epTag}`
          : `Hand 2D · ${styleTag} · ${epTag}`
        : dual
          ? "Hand 2D off"
          : "Hand 2D overlay (off)";
  }

  function ensurePill() {
    if (state.pill?.isConnected) return state.pill;
    const pill = document.createElement("button");
    pill.type = "button";
    pill.className = "datalab-hand-kp2d-pill";
    pill.setAttribute("aria-pressed", state.enabled ? "true" : "false");
    pill.setAttribute(
      "title",
      isZh() ? "Shift+点击切换经典/富样式" : "Shift+click to toggle Classic/Rich style",
    );
    pill.innerHTML =
      '<span class="datalab-hand-kp2d-pill-dot"></span><span class="datalab-hand-kp2d-pill-label"></span>';
    pill.addEventListener("click", (e) => {
      if (e.shiftKey) {
        toggleRenderStyle();
        return;
      }
      state.enabled = !state.enabled;
      pill.setAttribute("aria-pressed", state.enabled ? "true" : "false");
      updatePillLabel();
      if (state.enabled) startPaintLoop();
      else {
        clearAllCanvases();
        stopPaintLoop();
      }
    });
    document.body.appendChild(pill);
    state.pill = pill;
    updatePillLabel();
    return pill;
  }

  function deactivate() {
    stopPaintLoop();
    g.clearInterval(scheduleSlotBootstrap._t);
    document.documentElement.removeAttribute("data-datalab-hand-kp2d");
    document.documentElement.removeAttribute("data-datalab-hand-kp2d-episode");
    document.documentElement.removeAttribute("data-datalab-hand-render-style");
    state.pill?.remove();
    state.pill = null;
    state.slots = new Map();
    state.cameraStates = new Map();
    state.datasetId = null;
    state.payload = null;
    state.fetchPromise = null;
    state.userClickedEpisode = null;
  }

  function scheduleSlotBootstrap() {
    g.clearInterval(scheduleSlotBootstrap._t);
    let attempts = 0;
    scheduleSlotBootstrap._t = g.setInterval(() => {
      attempts += 1;
      if (!state.payload || !state.enabled || attempts > 24) {
        g.clearInterval(scheduleSlotBootstrap._t);
        return;
      }
      if (state.slots.size >= state.cameraStates.size) {
        g.clearInterval(scheduleSlotBootstrap._t);
        return;
      }
      ensureAllSlots();
      if (state.slots.size > 0) {
        attachVideoSync();
        paintAllSlots();
      }
    }, 250);
  }

  function activateForDataset(datasetId) {
    fetchHandKp2d(datasetId).then((payload) => {
      if (!payload) {
        deactivate();
        return;
      }
      document.documentElement.setAttribute("data-datalab-hand-kp2d", "1");
      document.documentElement.setAttribute(
        "data-datalab-hand-render-style",
        activeRenderStyle(payload),
      );
      ensurePill();
      refreshCameraStates();
      ensureAllSlots();
      updatePillLabel();
      scheduleSlotBootstrap();
      startPaintLoop();
    });
  }

  function refresh() {
    const datasetId = parseSampleDatasetId();
    if (!datasetId) {
      deactivate();
      return;
    }
    if (state.datasetId !== datasetId) {
      state.payload = null;
      state.cameraStates = new Map();
      state.slots = new Map();
      state.userClickedEpisode = null;
    }
    const prevEp = normalizeEpisodeNumber(state.episodeIndex);
    if (state.payload) {
      refreshCameraStates();
      const newEp = normalizeEpisodeNumber(state.episodeIndex);
      if (prevEp !== newEp) {
        clearAllCanvases();
        ensureAllSlots();
      }
      updatePillLabel();
      if (state.enabled) startPaintLoop();
    } else {
      activateForDataset(datasetId);
    }
  }

  function scheduleRefresh() {
    g.clearTimeout(scheduleRefresh._t);
    scheduleRefresh._t = g.setTimeout(refresh, 120);
  }

  function onEpisodeClick(e) {
    let node = e.target;
    for (let depth = 0; depth < 10 && node; depth += 1) {
      const label = `${node.getAttribute?.("aria-label") || ""} ${node.textContent || ""}`;
      if (/Episode|#\s*\d+/.test(label)) {
        const ep = parseEpisodeFromLabel(label);
        if (ep !== null) {
          state.userClickedEpisode = ep;
          syncEpisodeUrl(ep);
          scheduleRefresh();
          break;
        }
      }
      node = node.parentElement;
    }
  }

  function install() {
    refresh();
    if (!state.observer) {
      state.observer = new MutationObserver(scheduleRefresh);
      state.observer.observe(document.documentElement, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["aria-current", "aria-selected", "class"],
      });
    }
    document.addEventListener("click", onEpisodeClick, true);
    g.addEventListener("popstate", scheduleRefresh);
    g.addEventListener("hashchange", scheduleRefresh);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install);
  } else {
    install();
  }

  g.__DATALAB_HAND_KP2D_API__ = {
    OVERLAY_VERSION,
    OVERLAY_RENDER_STYLE,
    state,
    refreshCameraStates,
    detectActiveEpisodeIndex,
    activeRenderStyle,
    toggleRenderStyle,
  };
}
