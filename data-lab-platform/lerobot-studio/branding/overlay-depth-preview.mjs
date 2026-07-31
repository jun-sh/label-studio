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
  frameIndexForVideo,
  frameUrl,
  normalizeEpisodeNumber,
  resolveEpisodeEntry,
} from "./overlay-depth-preview-lib.mjs";

const g = globalThis;
if (!g.__DATALAB_DEPTH_PREVIEW__) {
  g.__DATALAB_DEPTH_PREVIEW__ = true;

  const state = {
    datasetId: null,
    payload: null,
    episodeKey: "000000",
    slot: null,
    pill: null,
    rafId: 0,
    frameCache: new Map(),
  };

  function parseDatasetId() {
    try {
      const params = new URLSearchParams(g.location.search);
      const raw = params.get("url") || "";
      const m = raw.match(/sample:\/\/([^/?#]+)/i);
      return m ? decodeURIComponent(m[1]) : null;
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
    applyDisplayPanelLabel(wrap);
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
    const pill = document.createElement("div");
    pill.className = "datalab-depth-preview-pill";
    pill.textContent = "depth preview";
    document.body.appendChild(pill);
    state.pill = pill;
    return pill;
  }

  function updatePill() {
    const pill = ensurePill();
    const ep = state.episodeKey || "?";
    const backend = state.payload?.episodes?.[ep]?.backend || state.payload?.depth_kind || "relative";
    const label = displayPanelLabel(state.payload);
    pill.textContent = `${label} · ep ${parseInt(ep, 10)} · ${backend}`;
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
        updatePill();
        return payload;
      });
  }

  function setFrameImage(img, url) {
    if (!url) return;
    if (img.dataset.src === url) return;
    img.dataset.src = url;
    if (state.frameCache.has(url)) {
      img.src = state.frameCache.get(url);
      return;
    }
    img.src = url;
    state.frameCache.set(url, url);
  }

  function paintFrame() {
    if (!state.payload || !state.slot) return;
    const { video, img, wrap } = state.slot;
    const entry = state.payload.episodes?.[state.episodeKey];
    if (!entry) return;
    syncOverlayToVideo(img, video, wrap);
    const frameIdx = frameIndexForVideo(video, state.payload);
    const maxFrame = Math.max(0, Number(entry.frames || 1) - 1);
    const clamped = Math.min(frameIdx, maxFrame);
    const url = frameUrl(state.payload, state.episodeKey, clamped);
    setFrameImage(img, url);
  }

  function paintLoop() {
    state.rafId = 0;
    if (!state.payload) return;
    if (!state.slot?.video?.isConnected) {
      state.slot = ensureSlot();
    }
    if (!state.slot) {
      state.rafId = g.requestAnimationFrame(paintLoop);
      return;
    }
    const nextEp = detectEpisodeKey();
    if (nextEp !== state.episodeKey) {
      state.episodeKey = nextEp;
      updatePill();
    }
    paintFrame();
    state.rafId = g.requestAnimationFrame(paintLoop);
  }

  function start() {
    if (!state.rafId) state.rafId = g.requestAnimationFrame(paintLoop);
  }

  function boot() {
    const datasetId = parseDatasetId();
    if (!datasetId) return;
    fetchDepthPreview(datasetId)
      .then(() => {
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
    if (!state.slot?.video?.isConnected) state.slot = ensureSlot();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
}
