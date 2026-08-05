/**
 * Depth preview overlay for LeRobot Viewer (scheme A).
 * front_right slot shows front_left depth; panel label is depth_front_left.
 */
import {
  DEPTH_OVERLAY_VERSION,
  DISPLAY_PANEL_LABEL,
  cameraShortName,
  containedVideoLayout,
  displayPanelLabel,
  displayVideoKeyCandidates,
  episodeKeyCandidates,
  featureMatchesText,
  frameUrl,
  normalizeEpisodeNumber,
  resolveEpisodeEntry,
} from "./overlay-depth-preview-lib.mjs";
import { bindVideoFrameUpdates, frameIndexFromVideoTime } from "./overlay-video-sync.mjs";

const g = globalThis;
if (!g.__DATALAB_DEPTH_PREVIEW__) {
  g.__DATALAB_DEPTH_PREVIEW__ = true;

  const state = {
    datasetId: null,
    payload: null,
    episodeKey: "000000",
    slot: null,
    pill: null,
    enabled: true,
    videoCleanup: null,
    preloadCache: new Map(),
    restartTimer: 0,
  };

  function depthPreviewHasFrames(payload) {
    const episodes = payload?.episodes || {};
    return Object.values(episodes).some((entry) => Number(entry?.frames || 0) > 0);
  }

  function applyDepthEnabledDefault(payload) {
    state.enabled = depthPreviewHasFrames(payload);
  }

  function parseDatasetId() {
    try {
      const params = new URLSearchParams(g.location.search);
      const raw = params.get("url") || "";
      const sampleMatch = raw.match(/sample:\/\/([^/?#]+)/i);
      if (sampleMatch) return decodeURIComponent(sampleMatch[1]);
      const httpMatch = raw.match(/\/api\/sample\/([^/?#]+)\/dataset\/?/i);
      if (httpMatch) return decodeURIComponent(httpMatch[1]);
      return null;
    } catch {
      return null;
    }
  }

  function parseEpisodeFromUrl() {
    try {
      const params = new URLSearchParams(g.location.search);
      const ep = params.get("episode");
      if (ep == null || ep === "") return null;
      return String(normalizeEpisodeNumber(ep)).padStart(6, "0");
    } catch {
      return null;
    }
  }

  function detectEpisodeKey() {
    const fromUrl = parseEpisodeFromUrl();
    if (fromUrl && state.payload?.episodes?.[fromUrl]) return fromUrl;
    const resolved = resolveEpisodeEntry(state.payload, "0");
    return resolved.episodeKey || "000000";
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
        const blob = `${node.getAttribute("title") || ""}${node.textContent || ""}`;
        if (featureMatchesText(blob, featureKey)) {
          if (!best || depth < best.depth) {
            best = {
              video,
              depth,
              wrap: findPreviewPanelWrap(video) || video.parentElement,
              featureKey,
            };
          }
          break;
        }
        node = node.parentElement;
      }
    }
    return best ? { video: best.video, wrap: best.wrap, featureKey: best.featureKey } : null;
  }

  function findDisplayVideoSlot() {
    for (const featureKey of displayVideoKeyCandidates(state.payload)) {
      const found = findFeatureVideoSlot(featureKey);
      if (found?.wrap && found?.video) return found;
    }
    return null;
  }

  function releaseSlot(slot) {
    if (!slot) return;
    slot.video?.classList?.remove("datalab-depth-preview-target-video");
    slot.wrap?.classList?.remove("datalab-depth-preview-panel");
    slot.wrap?.removeAttribute("data-datalab-depth-preview");
    slot.wrap?.removeAttribute("data-datalab-depth-preview-camera");
    slot.wrap?.removeAttribute("data-datalab-depth-version");
  }

  function containedVideoRect(video) {
    const cw = video.clientWidth || 0;
    const ch = video.clientHeight || 0;
    return containedVideoLayout(video.videoWidth || 0, video.videoHeight || 0, cw, ch);
  }

  function syncOverlayToVideo(img, video, wrap) {
    const rect = containedVideoRect(video);
    if (!rect || !img || !wrap) return null;
    const vr = video.getBoundingClientRect();
    const wr = wrap.getBoundingClientRect();
    const left = vr.left - wr.left + rect.offsetX;
    const top = vr.top - wr.top + rect.offsetY;
    const w = Math.max(1, Math.round(rect.width));
    const h = Math.max(1, Math.round(rect.height));
    img.style.left = `${left}px`;
    img.style.top = `${top}px`;
    img.style.width = `${w}px`;
    img.style.height = `${h}px`;
    img.style.right = "auto";
    img.style.bottom = "auto";
    return rect;
  }

  function applyDisplayPanelLabel(wrap) {
    const label = displayPanelLabel(state.payload);
    const replacements = [
      ["observation.images.camera_front_right", label],
      ["camera_front_right", label],
    ];
    let node = wrap;
    for (let depth = 0; depth < 14 && node; depth += 1) {
      const stack = [node];
      while (stack.length) {
        const el = stack.pop();
        if (!el) continue;
        if (el.nodeType === Node.TEXT_NODE) {
          let text = el.textContent || "";
          let changed = false;
          for (const [from, to] of replacements) {
            if (text.indexOf(from) >= 0) {
              text = text.split(from).join(to);
              changed = true;
            }
          }
          if (changed) el.textContent = text;
          continue;
        }
        if (el.nodeType !== Node.ELEMENT_NODE) continue;
        for (const attr of ["title", "aria-label"]) {
          const value = el.getAttribute(attr);
          if (!value) continue;
          let next = value;
          for (const [from, to] of replacements) {
            next = next.split(from).join(to);
          }
          if (next !== value) el.setAttribute(attr, next);
        }
        for (const child of el.childNodes || []) stack.push(child);
      }
      node = node.parentElement;
    }
  }

  function ensureSlot() {
    const found = findDisplayVideoSlot();
    if (!found?.wrap || !found?.video) return null;

    const { wrap, video, featureKey } = found;
    if (state.slot?.video && state.slot.video !== video) {
      releaseSlot(state.slot);
    }

    wrap.classList.add("datalab-depth-preview-panel");
    wrap.setAttribute("data-datalab-depth-preview", "1");
    wrap.setAttribute("data-datalab-depth-preview-camera", displayPanelLabel(state.payload));
    wrap.setAttribute("data-datalab-depth-version", String(DEPTH_OVERLAY_VERSION));
    if (!wrap.dataset.datalabDepthLabelApplied) {
      applyDisplayPanelLabel(wrap);
      wrap.dataset.datalabDepthLabelApplied = "1";
    }
    if (g.getComputedStyle(wrap).position === "static") wrap.style.position = "relative";

    video.classList.add("datalab-depth-preview-target-video");

    let img = wrap.querySelector("img.datalab-depth-preview-overlay");
    if (!img) {
      img = document.createElement("img");
      img.className = "datalab-depth-preview-overlay";
      img.alt = "";
      img.setAttribute("aria-hidden", "true");
      wrap.appendChild(img);
    } else if (img.parentElement !== wrap) {
      wrap.appendChild(img);
    }

    if (!img._datalabResizeObs) {
      const ro = new ResizeObserver(() => syncOverlayToVideo(img, video, wrap));
      ro.observe(wrap);
      ro.observe(video);
      img._datalabResizeObs = ro;
    }

    syncOverlayToVideo(img, video, wrap);
    return { wrap, video, img, featureKey };
  }

  function ensurePill() {
    if (state.pill?.isConnected) return state.pill;
    const pill = document.createElement("button");
    pill.type = "button";
    pill.className = "datalab-depth-preview-pill";
    pill.setAttribute("aria-pressed", state.enabled ? "true" : "false");
    pill.textContent = "depth preview";
    pill.addEventListener("click", () => {
      state.enabled = !state.enabled;
      pill.setAttribute("aria-pressed", state.enabled ? "true" : "false");
      updatePill();
      if (state.slot?.img) {
        state.slot.img.style.display = state.enabled ? "block" : "none";
      }
      if (state.enabled) {
        paintFrame();
      }
    });
    document.body.appendChild(pill);
    state.pill = pill;
    return pill;
  }

  function updatePill() {
    const pill = ensurePill();
    const ep = state.episodeKey || "?";
    const backend = state.payload?.episodes?.[ep]?.backend || state.payload?.depth_kind || "relative";
    const label = displayPanelLabel(state.payload);
    const onOff = state.enabled ? (g.location.search.indexOf("lang=zh") >= 0 ? "开" : "on") : (g.location.search.indexOf("lang=zh") >= 0 ? "关" : "off");
    pill.textContent = `${label} · ep ${parseInt(ep, 10)} · ${backend} · ${onOff}`;
  }

  function preloadAround(episodeKey, frameIdx, entry) {
    const maxFrame = Math.max(0, Number(entry?.frames || 1) - 1);
    for (let offset = 1; offset <= 5; offset += 1) {
      const next = frameIdx + offset;
      if (next <= maxFrame) loaderForUrl(frameUrl(state.payload, episodeKey, next));
    }
  }

  function loaderForUrl(url) {
    let loader = state.preloadCache.get(url);
    if (!loader) {
      loader = new Image();
      loader.decoding = "async";
      loader.src = url;
      state.preloadCache.set(url, loader);
      if (state.preloadCache.size > 128) {
        const first = state.preloadCache.keys().next().value;
        state.preloadCache.delete(first);
      }
    }
    return loader;
  }

  function setFrameImage(img, url) {
    if (!url || !img) return;
    if (img.dataset.src === url) return;

    const loader = loaderForUrl(url);
    const apply = () => {
      if (img.dataset.src === url) return;
      const pending = img.dataset.pendingSrc;
      if (pending && pending !== url) return;
      img.dataset.src = url;
      delete img.dataset.pendingSrc;
      img.src = url;
    };

    if (loader.complete && loader.naturalWidth > 0) {
      apply();
      return;
    }

    img.dataset.pendingSrc = url;
    loader.addEventListener(
      "load",
      () => {
        apply();
      },
      { once: true },
    );
  }

  function fetchDepthPreview(datasetId) {
    const url = `/lerobot/api/sample/${encodeURIComponent(datasetId)}/depth-preview.json`;
    return fetch(url, { credentials: "same-origin" })
      .then((res) => {
        if (!res.ok) throw new Error(`depth_preview_http_${res.status}`);
        return res.json();
      })
      .then((payload) => {
        state.datasetId = datasetId;
        state.payload = payload;
        state.episodeKey = detectEpisodeKey();
        applyDepthEnabledDefault(payload);
        updatePill();
        return payload;
      });
  }

  function paintFrame() {
    if (!state.enabled || !state.payload || !state.slot) return;
    const { video, img, wrap } = state.slot;
    const entry = state.payload.episodes?.[state.episodeKey];
    if (!entry) return;
    syncOverlayToVideo(img, video, wrap);
    const fps = Number(state.payload?.fps) || 30;
    const frameIdx = frameIndexFromVideoTime(video, fps);
    const maxFrame = Math.max(0, Number(entry.frames || 1) - 1);
    const clamped = Math.min(frameIdx, maxFrame);
    const url = frameUrl(state.payload, state.episodeKey, clamped);
    setFrameImage(img, url);
    preloadAround(state.episodeKey, clamped, entry);
  }

  function detachVideoSync() {
    if (state.videoCleanup) {
      state.videoCleanup();
      state.videoCleanup = null;
    }
  }

  function attachVideoSync() {
    detachVideoSync();
    const video = state.slot?.video;
    if (!video) return;
    const fps = Number(state.payload?.fps) || 30;
    state.videoCleanup = bindVideoFrameUpdates(
      video,
      () => {
        const nextEp = detectEpisodeKey();
        if (nextEp !== state.episodeKey) {
          state.episodeKey = nextEp;
          updatePill();
        }
        paintFrame();
      },
      { fps },
    );
  }

  function start() {
    if (!state.slot?.video?.isConnected) {
      state.slot = ensureSlot();
    }
    if (state.slot?.img) {
      state.slot.img.style.display = state.enabled ? "block" : "none";
    }
    attachVideoSync();
    paintFrame();
  }

  function warmPreload(payload) {
    const episodes = payload?.episodes || {};
    for (const [episodeKey, entry] of Object.entries(episodes)) {
      const frames = Math.min(12, Number(entry?.frames || 0));
      for (let i = 0; i < frames; i += 1) {
        loaderForUrl(frameUrl(payload, episodeKey, i));
      }
    }
  }

  function boot() {
    const datasetId = parseDatasetId();
    if (!datasetId) return;
    fetchDepthPreview(datasetId)
      .then((payload) => {
        warmPreload(payload);
        state.slot = ensureSlot();
        start();
      })
      .catch(() => {
        /* optional overlay */
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }

  const observer = new MutationObserver(() => {
    if (!state.payload) return;
    if (state.slot?.video?.isConnected) return;
    if (state.restartTimer) return;
    state.restartTimer = g.setTimeout(() => {
      state.restartTimer = 0;
      if (state.slot?.video?.isConnected) return;
      state.slot = ensureSlot();
      if (state.slot) start();
    }, 250);
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
}
