(() => {
  const { port, protocol, hostname, pathname, search, hash } = window.location;
  const publicPort = { "8085": "8080", "8086": "8081" }[port];
  if (publicPort) {
    window.location.replace(`${protocol}//${hostname}:${publicPort}${pathname}${search}${hash}`);
  }
})();

const API_PREFIX = (() => {
  const path = window.location.pathname || "";
  for (const marker of ["/qc", "/lerobot-qc"]) {
    const idx = path.indexOf(marker);
    if (idx >= 0) return path.slice(0, idx + marker.length);
  }
  return "";
})();

const t = (key, vars) => window.LA_I18N?.t(key, vars) ?? key;

async function api(path, options = {}) {
  let url = path.startsWith("/") ? path : `/${path}`;
  if (API_PREFIX && url.startsWith(`${API_PREFIX}/`)) {
    url = url.slice(API_PREFIX.length);
  }
  const response = await fetch(`${API_PREFIX}${url}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return response.json();
}

const els = {
  status: document.getElementById("status"),
  connectPanel: document.getElementById("connectPanel"),
  connectToggle: document.getElementById("connectToggle"),
  connectForm: document.getElementById("connectForm"),
  collectionSelect: document.getElementById("collectionSelect"),
  packageSelect: document.getElementById("packageSelect"),
  datasetPathInput: document.getElementById("datasetPathInput"),
  operatorInput: document.getElementById("operatorInput"),
  screeningFileInput: document.getElementById("screeningFileInput"),
  connectHelper: document.getElementById("connectHelper"),
  workspace: document.getElementById("workspace"),
  datasetMeta: document.getElementById("datasetMeta"),
  reviewProgress: document.getElementById("reviewProgress"),
  episodeList: document.getElementById("episodeList"),
  episodeSearch: document.getElementById("episodeSearch"),
  episodeStatusFilter: document.getElementById("episodeStatusFilter"),
  episodeTitle: document.getElementById("episodeTitle"),
  episodeMeta: document.getElementById("episodeMeta"),
  removedBanner: document.getElementById("removedBanner"),
  metricsPanel: document.getElementById("metricsPanel"),
  instructionInput: document.getElementById("instructionInput"),
  saveInstructionBtn: document.getElementById("saveInstructionBtn"),
  approveBtn: document.getElementById("approveBtn"),
  rejectBtn: document.getElementById("rejectBtn"),
  suspiciousBtn: document.getElementById("suspiciousBtn"),
  restoreEpisodeBtn: document.getElementById("restoreEpisodeBtn"),
  videoBlock: document.getElementById("videoBlock"),
  videoGrid: document.getElementById("videoGrid"),
  playPauseBtn: document.getElementById("playPauseBtn"),
  stepBackBtn: document.getElementById("stepBackBtn"),
  stepFwdBtn: document.getElementById("stepFwdBtn"),
  speedSelect: document.getElementById("speedSelect"),
  frameReadout: document.getElementById("frameReadout"),
  actionChart: document.getElementById("actionChart"),
  stateChart: document.getElementById("stateChart"),
  rebuildBtn: document.getElementById("rebuildBtn"),
  rebuildStatusInline: document.getElementById("rebuildStatusInline"),
  rebuildInlineProgress: document.getElementById("rebuildInlineProgress"),
  rebuildInlineProgressBar: document.getElementById("rebuildInlineProgressBar"),
  rebuildInlineProgressFill: document.getElementById("rebuildInlineProgressFill"),
  rebuildInlineProgressLabel: document.getElementById("rebuildInlineProgressLabel"),
  rebuildOutputHint: document.getElementById("rebuildOutputHint"),
  rebuildModal: document.getElementById("rebuildModal"),
  rebuildModalBody: document.getElementById("rebuildModalBody"),
  rebuildModalTitle: document.getElementById("rebuildModalTitle"),
  rebuildStatusText: document.getElementById("rebuildStatusText"),
  closeRebuildModal: document.getElementById("closeRebuildModal"),
};

const state = {
  dataset: null,
  episodes: [],
  qcState: {},
  collections: [],
  queryEpisode: null,
  currentEpisode: null,
  currentDetail: null,
  trajectory: null,
  frameIndex: 0,
  playing: false,
  videoPanes: [],
  drag: null,
  episodeStatusFilter: "all",
};

let videoLoadToken = 0;
let rebuildPollTimer = null;
let rebuildActive = false;
let qcActionInFlight = false;

function setStatus(text, ok = false) {
  els.status.textContent = text;
  els.status.classList.toggle("ok", ok);
}

function formatDuration(sec) {
  if (!Number.isFinite(sec)) return "—";
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function reviewStatusLabel(status) {
  const key = `qc_status_${status || "pending"}`;
  return t(key);
}

function reviewStatusClass(status) {
  if (status === "approved") return "complete";
  if (status === "suspicious") return "partial";
  if (status === "rejected") return "rejected";
  return "none";
}

function collapseConnectPanel() {
  if (els.connectToggle) {
    els.connectToggle.setAttribute("aria-expanded", "false");
  }
  if (els.connectForm) {
    els.connectForm.classList.add("collapsed");
  }
}

function showWorkspace() {
  els.workspace.hidden = false;
  els.workspace.style.display = "grid";
}

function readQueryParams() {
  const params = new URLSearchParams(window.location.search);
  const datasetPath = params.get("datasetPath") || params.get("local_path");
  if (datasetPath) els.datasetPathInput.value = datasetPath;
  state.queryCollection = params.get("collection") || "";
  state.queryPackage = params.get("package") || "";
  const episodeParam = params.get("episode");
  state.queryEpisode = episodeParam != null && episodeParam !== "" ? Number(episodeParam) : null;
}

function syncUrlQuery() {
  const params = new URLSearchParams();
  const collection = els.collectionSelect?.value || "";
  const packageId = els.packageSelect?.value || "";
  const localPath = els.datasetPathInput?.value?.trim() || "";
  if (collection) params.set("collection", collection);
  if (packageId) params.set("package", packageId);
  if (localPath) params.set("local_path", localPath);
  if (state.dataset && state.currentEpisode != null && Number.isFinite(state.currentEpisode)) {
    params.set("episode", String(state.currentEpisode));
  }
  const base = window.location.pathname || "/qc/";
  const next = params.toString();
  const nextUrl = next ? `${base}?${next}` : base;
  const currentUrl = `${window.location.pathname}${window.location.search}`;
  if (currentUrl !== nextUrl) {
    window.history.replaceState(null, "", nextUrl);
  }
}

function isCurrentEpisodeRemoved() {
  if (state.currentDetail?.is_removed) return true;
  if (state.currentEpisode == null) return false;
  const review = state.qcState[String(state.currentEpisode)]?.review?.status;
  return review === "rejected";
}

function updateRemovedBanner(detail) {
  if (!els.removedBanner) return;
  const removed = detail?.is_removed || isCurrentEpisodeRemoved();
  if (!removed) {
    els.removedBanner.hidden = true;
    els.removedBanner.textContent = "";
    return;
  }
  const reason =
    detail?.removal_reason ||
    state.qcState[String(state.currentEpisode)]?.review?.reason ||
    "";
  els.removedBanner.hidden = false;
  els.removedBanner.textContent = reason.trim()
    ? t("qc_removed_banner_with_reason", { reason: reason.trim() })
    : t("qc_removed_banner");
}

function updateQcActionButtons() {
  const hasEpisode = Boolean(state.dataset && state.currentEpisode != null && !qcActionInFlight);
  const removed = isCurrentEpisodeRemoved();
  els.saveInstructionBtn.disabled = !hasEpisode || removed;
  els.instructionInput.disabled = !hasEpisode || removed;
  els.approveBtn.disabled = !hasEpisode || removed;
  els.rejectBtn.disabled = !hasEpisode || removed;
  els.suspiciousBtn.disabled = !hasEpisode || removed;
  if (els.restoreEpisodeBtn) {
    els.restoreEpisodeBtn.hidden = !hasEpisode || !removed;
    els.restoreEpisodeBtn.disabled = !hasEpisode || !removed;
  }
}

function setQcActionsBusy(busy) {
  qcActionInFlight = busy;
  updateQcActionButtons();
}

function parseApiError(error) {
  const raw = String(error?.message || error || "");
  try {
    const payload = JSON.parse(raw);
    if (payload.detail) {
      return typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
    }
  } catch {
    /* not JSON */
  }
  return raw || "Unknown error";
}

function renderReviewProgress(summary) {
  if (!summary) {
    els.reviewProgress.textContent = "";
    return;
  }
  els.reviewProgress.textContent = t("qc_review_progress", summary);
}

function renderEpisodeList() {
  const filter = els.episodeSearch.value.trim();
  const statusFilter = els.episodeStatusFilter?.value || state.episodeStatusFilter || "all";
  els.episodeList.innerHTML = "";
  state.episodes
    .filter((ep) => {
      if (!String(ep.episode_index).includes(filter)) return false;
      if (statusFilter === "all") return true;
      const review = state.qcState[String(ep.episode_index)]?.review?.status || "pending";
      return review === statusFilter;
    })
    .forEach((ep) => {
      const review = state.qcState[String(ep.episode_index)]?.review?.status || "pending";
      const li = document.createElement("li");
      li.dataset.status = reviewStatusClass(review);

      const title = document.createElement("span");
      title.className = "ls-episode-title";
      title.textContent = t("episode_title", { idx: ep.episode_index });

      const meta = document.createElement("span");
      meta.className = "ls-episode-meta";

      const badge = document.createElement("span");
      badge.className = `ls-episode-status ls-episode-status-${reviewStatusClass(review)}`;
      badge.textContent = reviewStatusLabel(review);

      const dur = document.createElement("span");
      dur.className = "ls-episode-duration";
      dur.textContent = formatDuration(ep.duration);

      meta.appendChild(badge);
      meta.appendChild(dur);
      li.appendChild(title);
      li.appendChild(meta);
      if (state.currentEpisode === ep.episode_index) li.classList.add("active");
      li.addEventListener("click", () => selectEpisode(ep.episode_index));
      els.episodeList.appendChild(li);
    });
}

function renderMetrics(metrics) {
  els.metricsPanel.hidden = false;
  const items = [
    ["Duration", `${metrics.duration_sec.toFixed(2)}s`],
    ["dt_max", `${metrics.dt_max_ms} ms`],
    ["Time gap", metrics.has_time_gap ? "yes" : "no"],
    ["Frame rate", metrics.frame_rate_stable ? "stable" : "unstable"],
  ];
  els.metricsPanel.innerHTML = items
    .map(([label, value]) => {
      const anomaly =
        (label === "Duration" && metrics.duration_sec < 1) ||
        (label === "dt_max" && metrics.dt_max_ms > (metrics.dt_max_threshold_ms ?? 20)) ||
        (label === "Time gap" && metrics.has_time_gap);
      return `<div class="ls-qc-metric ${anomaly ? "anomaly" : ""}"><strong>${label}:</strong> ${value}</div>`;
    })
    .join("");
}

function getFps() {
  return state.dataset?.fps || state.currentDetail?.fps || 30;
}

function getTotalFrames() {
  if (state.trajectory?.frame_count) return state.trajectory.frame_count;
  if (state.currentDetail?.length) return state.currentDetail.length;
  const master = getMasterVideo();
  if (master?.duration) return Math.max(1, Math.round(master.duration * getFps()));
  return 0;
}

function getEpisodeDuration() {
  if (state.currentDetail?.duration) return state.currentDetail.duration;
  const master = getMasterVideo();
  return master?.duration || 0;
}

function clampFrameIndex(frame) {
  const total = getTotalFrames();
  if (!total) return 0;
  return Math.max(0, Math.min(frame, total - 1));
}

function setPlayingUi(playing) {
  state.playing = playing;
  els.playPauseBtn.textContent = playing ? t("qc_btn_pause") : t("qc_btn_play");
}

function getPaneVideos() {
  return state.videoPanes.map((p) => p.video).filter(Boolean);
}

function getMasterVideo() {
  return state.videoPanes[0]?.video || null;
}

function formatVideoKeyLabel(key) {
  return (key || "").split(".").pop() || key;
}

function isDepthVideoKey(key) {
  return /depth/i.test(key || "");
}

/** RGB / H.264 streams only — depth MKV is not playable in HTML5 video. */
function getPlaybackVideoKeys(keys) {
  const playable = (keys || []).filter((k) => !isDepthVideoKey(k));
  return playable.slice(0, 2);
}

function buildVideoUrl(episodeIndex, videoKey) {
  return `${API_PREFIX}/api/video/${episodeIndex}?video_key=${encodeURIComponent(videoKey)}`;
}

function setVideoBlockLayout(paneCount) {
  if (!els.videoBlock) return;
  els.videoBlock.classList.remove("ls-video-block--1", "ls-video-block--2");
  if (paneCount === 1 || paneCount === 2) {
    els.videoBlock.classList.add(`ls-video-block--${paneCount}`);
  }
}

function syncAllVideosToTime(time, { pause = false } = {}) {
  getPaneVideos().forEach((video) => {
    try {
      if (Number.isFinite(time) && Math.abs(video.currentTime - time) > 0.0005) {
        video.currentTime = time;
      }
      if (pause) video.pause();
    } catch (_) {
      /* ignore */
    }
  });
}

function syncOtherVideosTo(source) {
  if (!source) return;
  const time = source.currentTime;
  getPaneVideos().forEach((video) => {
    if (video === source) return;
    try {
      if (Math.abs(video.currentTime - time) > 0.05) video.currentTime = time;
    } catch (_) {
      /* ignore */
    }
  });
}

function bindVideoSyncEvents(video) {
  if (!video || video.dataset.lsBound === "1") return;
  video.dataset.lsBound = "1";
  video.addEventListener("timeupdate", () => {
    const master = getMasterVideo();
    if (video === master && Number.isFinite(video.currentTime)) {
      state.frameIndex = clampFrameIndex(secondsToStartFrame(video.currentTime));
    }
    syncOtherVideosTo(video);
    updatePlayhead();
    drawCharts();
  });
  video.addEventListener("seeked", () => {
    const master = getMasterVideo();
    if (video === master && Number.isFinite(video.currentTime)) {
      state.frameIndex = clampFrameIndex(secondsToStartFrame(video.currentTime));
    }
    syncOtherVideosTo(video);
    updatePlayhead();
    drawCharts();
  });
  video.addEventListener("loadedmetadata", () => {
    if (els.videoBlock?.classList.contains("ls-video-switching")) return;
    renderRuler();
    updatePlayhead();
  });
  video.addEventListener("play", () => {
    const time = video.currentTime;
    getPaneVideos().forEach((el) => {
      if (el === video) return;
      try {
        if (Math.abs(el.currentTime - time) > 0.05) el.currentTime = time;
        el.play().catch(() => {});
      } catch (_) {
        /* ignore */
      }
    });
    if (video === getMasterVideo()) setPlayingUi(true);
  });
  video.addEventListener("pause", () => {
    if (video !== getMasterVideo()) return;
    getPaneVideos().forEach((el) => {
      if (el !== video && !el.paused) el.pause();
    });
    if (getPaneVideos().every((el) => el.paused || el.ended)) {
      setPlayingUi(false);
    }
  });
  video.addEventListener("ended", () => {
    if (video !== getMasterVideo()) return;
    const total = getTotalFrames();
    if (total) {
      state.frameIndex = total - 1;
      syncAllVideosToTime(startFrameToSeconds(state.frameIndex), { pause: true });
    } else {
      pauseAllVideos();
    }
    updatePlayhead();
    drawCharts();
    setPlayingUi(false);
  });
}

function renderVideoGrid(videoKeys) {
  const keys = videoKeys || [];
  els.videoGrid.innerHTML = "";
  state.videoPanes = [];
  if (!keys.length) {
    els.videoGrid.className = "ls-video-grid ls-video-grid--1";
    setVideoBlockLayout(0);
    return;
  }
  const layoutCount = Math.min(keys.length, 2);
  els.videoGrid.className = `ls-video-grid ls-video-grid--${layoutCount}`;
  setVideoBlockLayout(layoutCount);
  keys.forEach((key, index) => {
    const pane = document.createElement("div");
    pane.className = "ls-video-pane";
    pane.dataset.videoKey = key;
    const label = document.createElement("div");
    label.className = "ls-video-pane-label";
    label.textContent = formatVideoKeyLabel(key);
    label.title = key;
    const video = document.createElement("video");
    video.className = index === 0 ? "ls-video-master" : "ls-video-slave";
    video.preload = "auto";
    video.playsInline = true;
    video.muted = true;
    video.controls = true;
    video.addEventListener("error", () => {
      pane.classList.add("ls-video-pane--error");
      label.title = `${key}: failed to load video`;
    });
    pane.appendChild(label);
    pane.appendChild(video);
    els.videoGrid.appendChild(pane);
    state.videoPanes.push({ key, video, pane });
    bindVideoSyncEvents(video);
  });
}

function loadEpisodeVideoSources(episodeIndex, videoKeys, { time = 0, pause = true } = {}) {
  const keys = videoKeys || [];
  if (!keys.length || episodeIndex == null) return;
  const token = ++videoLoadToken;
  if (els.videoBlock) els.videoBlock.classList.add("ls-video-switching");
  let pending = keys.length;
  const finishLoad = () => {
    if (token !== videoLoadToken) return;
    pending -= 1;
    if (pending > 0) return;
    syncAllVideosToTime(time, { pause });
    if (els.videoBlock) els.videoBlock.classList.remove("ls-video-switching");
    renderRuler();
    updatePlayhead();
    drawCharts();
  };
  keys.forEach((key) => {
    const pane = state.videoPanes.find((p) => p.key === key);
    if (!pane) {
      finishLoad();
      return;
    }
    const { video } = pane;
    const onReady = () => {
      video.removeEventListener("loadedmetadata", onReady);
      finishLoad();
    };
    video.addEventListener("loadedmetadata", onReady);
    video.src = buildVideoUrl(episodeIndex, key);
  });
}

function secondsToStartFrame(sec) {
  return Math.max(0, Math.round(sec * getFps()));
}

function startFrameToSeconds(frame) {
  return frame / getFps();
}

function currentFrame() {
  const master = getMasterVideo();
  if (master && Number.isFinite(master.duration) && master.duration > 0 && !master.paused) {
    state.frameIndex = secondsToStartFrame(master.currentTime);
  }
  return state.frameIndex;
}

function frameToPercent(frame) {
  const total = getTotalFrames();
  if (!total) return 0;
  return (clampFrameIndex(frame) / total) * 100;
}

function percentToFrame(pct) {
  const total = getTotalFrames();
  return Math.round(Math.max(0, Math.min(1, pct)) * Math.max(0, total - 1));
}

function trackXToPercent(clientX) {
  return 0;
}

function seekToClientX(clientX) {
  return;
}

function renderRuler() {
  return;
}

function updatePlayhead() {
  const total = getTotalFrames();
  if (!total) {
    if (els.frameReadout) els.frameReadout.textContent = "frame —";
    return;
  }
  const frame = clampFrameIndex(state.frameIndex);
  state.frameIndex = frame;
  if (els.frameReadout) {
    els.frameReadout.textContent = t("frame_readout", { cur: frame, max: total - 1 });
  }
}

function beginSeekDrag(e) {
  return false;
}

function pauseAllVideos() {
  getPaneVideos().forEach((video) => video.pause());
  setPlayingUi(false);
}

function togglePlayback() {
  if (!getTotalFrames()) return;
  const videos = getPaneVideos();
  const anyPlaying = videos.some((video) => !video.paused && !video.ended);
  if (anyPlaying) {
    pauseAllVideos();
    return;
  }
  syncAllVideosToTime(startFrameToSeconds(state.frameIndex));
  videos.forEach((video) => video.play().catch(() => {}));
  setPlayingUi(true);
}

function setFrame(frameIndex) {
  const maxFrame = Math.max(0, getTotalFrames() - 1);
  const frame = Math.max(0, Math.min(frameIndex, maxFrame));
  state.frameIndex = frame;
  syncAllVideosToTime(startFrameToSeconds(frame), { pause: true });
  updatePlayhead();
  drawCharts();
}

const CHART_MARGIN = { top: 14, right: 14, bottom: 32, left: 54 };
const CHART_SERIES_PALETTE = [
  "#576cc9", "#52a675", "#d98b3a", "#c45c7a", "#6b8f71",
  "#8e6cc9", "#3aa0a8", "#b86b3a", "#5c7ac4", "#a85c9a",
];

function formatChartValue(v) {
  if (!Number.isFinite(v)) return "";
  const abs = Math.abs(v);
  if (abs >= 100) return v.toFixed(0);
  if (abs >= 10) return v.toFixed(1);
  if (abs >= 1) return v.toFixed(2);
  return v.toFixed(3);
}

function pickAxisTicks(min, max, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0];
  if (min === max) return [min];
  const span = max - min;
  const raw = span / Math.max(1, count - 1);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = Math.ceil(raw / mag) * mag;
  const start = Math.floor(min / step) * step;
  const ticks = [];
  for (let v = start; v <= max + step * 0.001; v += step) {
    if (v >= min - step * 0.001 && v <= max + step * 0.001) ticks.push(v);
  }
  return ticks.length ? ticks : [min, max];
}

function drawSeriesChart(canvas, seriesMap, defaultColor) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const width = canvas.clientWidth || 800;
  const height = canvas.height;
  canvas.width = width;
  ctx.clearRect(0, 0, width, height);

  const entries = Object.entries(seriesMap || {}).filter(([, values]) => Array.isArray(values) && values.length);
  if (!entries.length) {
    ctx.fillStyle = "rgba(0,0,0,0.45)";
    ctx.font = "12px sans-serif";
    ctx.fillText("No data", CHART_MARGIN.left, 24);
    return;
  }

  const frameCount = entries[0][1].length;
  const timestamps = state.trajectory?.timestamps || [];
  const fps = getFps();
  const xAtFrame = (i) => (timestamps[i] != null && Number.isFinite(timestamps[i]) ? timestamps[i] : i / fps);
  const xMin = xAtFrame(0);
  const xMax = xAtFrame(Math.max(0, frameCount - 1));

  let yMin = Infinity;
  let yMax = -Infinity;
  entries.forEach(([, arr]) => {
    arr.forEach((v) => {
      if (!Number.isFinite(v)) return;
      yMin = Math.min(yMin, v);
      yMax = Math.max(yMax, v);
    });
  });
  if (!Number.isFinite(yMin) || !Number.isFinite(yMax)) {
    yMin = 0;
    yMax = 1;
  }
  if (yMin === yMax) {
    yMin -= 1;
    yMax += 1;
  }
  const yPad = (yMax - yMin) * 0.08 || 0.5;
  yMin -= yPad;
  yMax += yPad;

  const plotLeft = CHART_MARGIN.left;
  const plotTop = CHART_MARGIN.top;
  const plotRight = width - CHART_MARGIN.right;
  const plotBottom = height - CHART_MARGIN.bottom;
  const plotW = plotRight - plotLeft;
  const plotH = plotBottom - plotTop;

  const xAt = (i) => plotLeft + ((xAtFrame(i) - xMin) / Math.max(1e-9, xMax - xMin)) * plotW;
  const yAt = (v) => plotBottom - ((v - yMin) / Math.max(1e-9, yMax - yMin)) * plotH;

  const yTicks = pickAxisTicks(yMin, yMax, 5);
  const xTickCount = Math.min(6, Math.max(3, Math.floor(plotW / 90)));
  const xTicks = Array.from({ length: xTickCount }, (_, i) => xMin + ((xMax - xMin) * i) / Math.max(1, xTickCount - 1));

  ctx.strokeStyle = "rgba(0,0,0,0.08)";
  ctx.lineWidth = 1;
  yTicks.forEach((v) => {
    const y = yAt(v);
    ctx.beginPath();
    ctx.moveTo(plotLeft, y);
    ctx.lineTo(plotRight, y);
    ctx.stroke();
  });
  xTicks.forEach((t) => {
    const x = plotLeft + ((t - xMin) / Math.max(1e-9, xMax - xMin)) * plotW;
    ctx.beginPath();
    ctx.moveTo(x, plotTop);
    ctx.lineTo(x, plotBottom);
    ctx.stroke();
  });

  ctx.strokeStyle = "rgba(0,0,0,0.35)";
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.moveTo(plotLeft, plotTop);
  ctx.lineTo(plotLeft, plotBottom);
  ctx.lineTo(plotRight, plotBottom);
  ctx.stroke();

  ctx.fillStyle = "rgba(0,0,0,0.55)";
  ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  yTicks.forEach((v) => {
    ctx.fillText(formatChartValue(v), plotLeft - 6, yAt(v));
  });

  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  xTicks.forEach((t) => {
    const x = plotLeft + ((t - xMin) / Math.max(1e-9, xMax - xMin)) * plotW;
    ctx.fillText(`${t.toFixed(1)}s`, x, plotBottom + 6);
  });

  entries.forEach(([, arr], idx) => {
    const stroke = CHART_SERIES_PALETTE[idx % CHART_SERIES_PALETTE.length] || defaultColor;
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    arr.forEach((v, i) => {
      if (!Number.isFinite(v)) return;
      const x = xAt(i);
      const y = yAt(v);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });

  const frame = clampFrameIndex(state.frameIndex);
  const markerX = xAt(Math.min(frame, frameCount - 1));
  ctx.strokeStyle = "#ff4d4f";
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.moveTo(markerX, plotTop);
  ctx.lineTo(markerX, plotBottom);
  ctx.stroke();

  ctx.fillStyle = "rgba(0,0,0,0.45)";
  ctx.font = "10px sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText(`${entries.length} dim`, plotRight - 36, plotTop + 2);
}

function drawCharts() {
  if (!state.trajectory) return;
  drawSeriesChart(els.actionChart, state.trajectory.action, "#576cc9");
  drawSeriesChart(els.stateChart, state.trajectory.observation_state, "#52a675");
}

async function refreshDatasetInfo() {
  const info = await api("/api/dataset/info");
  state.qcState = info.qc_state || {};
  state.episodes = info.episodes || [];
  renderReviewProgress(info.review_summary);
  renderEpisodeList();
  return info;
}

async function selectEpisode(episodeIndex) {
  pauseAllVideos();
  state.currentEpisode = episodeIndex;
  renderEpisodeList();
  const [detail, trajectory] = await Promise.all([
    api(`/api/episodes/${episodeIndex}`),
    api(`/api/episodes/${episodeIndex}/trajectory`),
  ]);
  state.currentDetail = detail;
  state.trajectory = trajectory;
  els.episodeTitle.textContent = t("episode_title", { idx: episodeIndex });
  const review = detail.review?.status || "pending";
  els.episodeMeta.textContent = t("episode_meta", {
    frames: detail.length,
    fps: getFps(),
    duration: formatDuration(detail.duration),
  }) + ` · ${reviewStatusLabel(review)}`;
  renderMetrics(detail.metrics);
  els.instructionInput.value = detail.language_instruction || "";
  updateRemovedBanner(detail);
  updateQcActionButtons();
  const videoKeys = getPlaybackVideoKeys(detail.video_keys || state.dataset?.video_keys || []);
  renderVideoGrid(videoKeys);
  loadEpisodeVideoSources(episodeIndex, videoKeys, { time: 0, pause: true });
  state.frameIndex = 0;
  els.playPauseBtn.disabled = false;
  els.stepBackBtn.disabled = false;
  els.stepFwdBtn.disabled = false;
  els.speedSelect.disabled = false;
  renderRuler();
  updatePlayhead();
  drawCharts();
  syncUrlQuery();
}

async function loadDataset(event) {
  if (event) event.preventDefault();
  const local_path = els.datasetPathInput.value.trim();
  const operator_id = els.operatorInput.value.trim() || undefined;
  setStatus(t("connect_loading"));
  try {
    const summary = await api("/api/dataset/load", {
      method: "POST",
      body: JSON.stringify({ local_path, operator_id }),
    });
    state.dataset = summary;
    state.episodes = summary.episodes || [];
    state.qcState = summary.qc_state || {};
    els.workspace.hidden = false;
    showWorkspace();
    collapseConnectPanel();
    els.datasetMeta.textContent = `${summary.total_episodes} episodes · ${summary.fps} fps · ${summary.robot_type || "robot"}`;
    els.connectHelper.textContent = `Sidecar: ${summary.sidecar_root}`;
    els.rebuildBtn.disabled = false;
    els.screeningFileInput.disabled = false;
    renderReviewProgress(summary.review_summary);
    renderEpisodeList();
    if (summary.active_rebuild_job) {
      await resumeActiveRebuild(summary.active_rebuild_job);
    }
    const initialEpisode =
      state.queryEpisode != null &&
      state.episodes.some((ep) => ep.episode_index === state.queryEpisode)
        ? state.queryEpisode
        : state.episodes[0]?.episode_index;
    state.queryEpisode = null;
    if (initialEpisode != null) await selectEpisode(initialEpisode);
    syncUrlQuery();
    setStatus(t("status_loaded", { name: summary.root?.split("/").pop() || "dataset" }), true);
  } catch (error) {
    setStatus(t("status_disconnected"));
    els.connectHelper.textContent = String(error.message || error);
  }
}

async function postReview(status) {
  if (state.currentEpisode == null || qcActionInFlight) return;
  let reason = "";
  if (status === "rejected") {
    reason =
      window.prompt(t("qc_reject_reason_prompt"), t("qc_reject_reason_default")) || "";
    if (!reason.trim()) return;
  }
  setQcActionsBusy(true);
  try {
    await api("/api/qc/review", {
      method: "POST",
      body: JSON.stringify({
        episode_index: state.currentEpisode,
        status,
        reason: reason.trim() || undefined,
      }),
    });
    const info = await refreshDatasetInfo();
    if (status === "rejected") {
      await selectEpisode(state.currentEpisode);
      setStatus(t("status_saved"), true);
      return;
    }
    await selectEpisode(state.currentEpisode);
    renderReviewProgress(info.review_summary);
    setStatus(t("status_saved"), true);
  } catch (error) {
    const message = parseApiError(error);
    setStatus(t("qc_action_failed", { error: message }));
    els.connectHelper.textContent = t("qc_action_failed", { error: message });
  } finally {
    setQcActionsBusy(false);
  }
}

async function saveInstruction() {
  if (state.currentEpisode == null || qcActionInFlight) return;
  const instruction = els.instructionInput.value.trim();
  if (!instruction) {
    setStatus(t("qc_instruction_empty"));
    els.connectHelper.textContent = t("qc_instruction_empty");
    return;
  }
  setQcActionsBusy(true);
  try {
    const result = await api("/api/qc/instruction", {
      method: "POST",
      body: JSON.stringify({
        episode_index: state.currentEpisode,
        instruction,
        original_instruction: state.currentDetail?.original_language_instruction,
      }),
    });
    await refreshDatasetInfo();
    await selectEpisode(state.currentEpisode);
    if (result.review?.status) {
      state.currentDetail.review = result.review;
    }
    const savedText = t("qc_instruction_saved", { idx: state.currentEpisode });
    setStatus(savedText, true);
    els.connectHelper.textContent = savedText;
  } catch (error) {
    const message = parseApiError(error);
    setStatus(t("qc_action_failed", { error: message }));
    els.connectHelper.textContent = t("qc_action_failed", { error: message });
  } finally {
    setQcActionsBusy(false);
  }
}

async function importScreeningFile(file) {
  const text = await file.text();
  const payload = JSON.parse(text);
  const result = await api("/api/qc/screening/import", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  await refreshDatasetInfo();
  els.connectHelper.textContent = `Screening imported: suspicious=${result.suspicious?.length || 0}, rejected=${result.rejected?.length || 0}, skipped=${result.skipped?.length || 0}`;
  if (state.episodes.length) await selectEpisode(state.episodes[0].episode_index);
}

function hideRebuildModal() {
  if (els.rebuildModal) els.rebuildModal.hidden = true;
}

function showRebuildResultModal(message, inlineTone = null) {
  hideRebuildProgress();
  if (els.rebuildModal) els.rebuildModal.hidden = false;
  setRebuildModalTitle(t("qc_delivery_title"));
  els.rebuildStatusText.textContent = message;
  els.rebuildStatusText.className = "ls-muted";
  if (els.rebuildModalBody) {
    els.rebuildModalBody.classList.remove(
      "ls-qc-modal-body--running",
      "ls-qc-modal-body--success",
      "ls-qc-modal-body--failed",
    );
    els.rebuildModalBody.classList.add("ls-qc-modal-body--running");
  }
  if (els.rebuildStatusInline) {
    els.rebuildStatusInline.textContent = inlineTone ? message : "";
    els.rebuildStatusInline.classList.remove("ok", "error");
    if (inlineTone === "success") els.rebuildStatusInline.classList.add("ok");
    if (inlineTone === "error") els.rebuildStatusInline.classList.add("error");
  }
}

function setRebuildModalTitle(text) {
  if (els.rebuildModalTitle) els.rebuildModalTitle.textContent = text;
}

function hideRebuildProgress() {
  if (els.rebuildInlineProgress) els.rebuildInlineProgress.hidden = true;
  if (els.rebuildOutputHint) els.rebuildOutputHint.textContent = "";
}

function localizeRebuildProgress(progress) {
  if (!progress) return "";
  const text = String(progress);
  if (text === "writing parquet files") return t("qc_rebuild_writing_parquet");
  return text;
}

function applyRebuildProgressBar(fillEl, barEl, labelEl, pct, label) {
  if (!fillEl) return;
  const clamped = Math.max(0, Math.min(100, pct));
  fillEl.style.width = `${clamped}%`;
  if (barEl) barEl.setAttribute("aria-valuenow", String(clamped));
  if (labelEl) labelEl.textContent = label;
}

function renderRebuildProgress(status) {
  const isActive = status && ["queued", "running"].includes(status.status);
  if (els.rebuildInlineProgress) els.rebuildInlineProgress.hidden = !isActive;
  if (!isActive) return;

  let pct = 0;
  let label = "";
  const progressText = localizeRebuildProgress(status.progress);
  const isEpisodeProgress = /^\d+\/\d+ episodes$/.test(String(status.progress || ""));

  if (status.progress_current != null && status.progress_total) {
    pct = Math.round((status.progress_current / status.progress_total) * 100);
    if (isEpisodeProgress || !progressText) {
      label = t("qc_rebuild_episode_progress", {
        current: status.progress_current,
        total: status.progress_total,
        pct,
      });
    } else {
      label = progressText;
    }
  } else if (progressText) {
    label = progressText;
    if (status.status === "queued") pct = 0;
  }

  applyRebuildProgressBar(
    els.rebuildInlineProgressFill,
    els.rebuildInlineProgressBar,
    els.rebuildInlineProgressLabel,
    pct,
    label,
  );

  if (els.rebuildOutputHint) {
    els.rebuildOutputHint.textContent = status.output_root
      ? t("qc_rebuild_output_hint", { path: status.output_root })
      : "";
  }
}

function formatRebuildStatus(status) {
  let text = status.status || "";
  if (status.progress_current && status.progress_total) {
    const pct = Math.round((status.progress_current / status.progress_total) * 100);
    text += ` · ${status.progress_current}/${status.progress_total} (${pct}%)`;
  } else if (status.progress) {
    text += ` · ${status.progress}`;
  }
  if (status.output_root) {
    text += ` → ${status.output_root}`;
  }
  if (status.error) text += ` · ${status.error}`;
  return text;
}

function clearRebuildPollTimer() {
  if (rebuildPollTimer != null) {
    clearTimeout(rebuildPollTimer);
    rebuildPollTimer = null;
  }
}

function setRebuildUiActive(active) {
  rebuildActive = active;
  if (els.rebuildBtn) els.rebuildBtn.disabled = active || !state.dataset;
}

async function pollRebuildJob(jobId) {
  clearRebuildPollTimer();
  const poll = async () => {
    try {
      const status = await api(`/api/qc/rebuild/${jobId}`);
      hideRebuildModal();
      renderRebuildProgress(status);
      if (status.status === "queued" || status.status === "running") {
        rebuildPollTimer = setTimeout(poll, 1500);
        return;
      }
      clearRebuildPollTimer();
      setRebuildUiActive(false);
      const text = formatRebuildStatus(status);
      if (status.status === "completed") {
        showRebuildResultModal(formatRebuildStatus(status), "success");
      } else if (status.status === "failed") {
        const err = status.error || text;
        const message = String(err).includes("Service restarted")
          ? t("qc_rebuild_stale_hint")
          : formatRebuildStatus(status);
        showRebuildResultModal(message, "error");
      }
    } catch (error) {
      clearRebuildPollTimer();
      setRebuildUiActive(false);
      showRebuildResultModal(t("qc_rebuild_poll_error", { error: parseApiError(error) }), "error");
    }
  };
  await poll();
}

async function resumeActiveRebuild(activeJob) {
  if (!activeJob?.job_id) return;
  if (!["queued", "running"].includes(activeJob.status)) {
    if (activeJob.status === "failed") {
      showRebuildResultModal(t("qc_rebuild_stale_hint"), "error");
    }
    return;
  }
  setRebuildUiActive(true);
  hideRebuildModal();
  renderRebuildProgress(activeJob);
  await pollRebuildJob(activeJob.job_id);
}

async function startRebuild() {
  if (rebuildActive) return;
  clearRebuildPollTimer();
  setRebuildUiActive(true);
  hideRebuildModal();
  hideRebuildProgress();
  if (els.rebuildStatusInline) {
    els.rebuildStatusInline.textContent = "";
    els.rebuildStatusInline.classList.remove("ok", "error");
  }
  try {
    const job = await api("/api/qc/rebuild", { method: "POST", body: JSON.stringify({}) });
    await pollRebuildJob(job.job_id);
  } catch (error) {
    setRebuildUiActive(false);
    const message = String(error.message || error);
    if (message.includes("already in progress") || message.includes("409")) {
      try {
        const info = await api("/api/dataset/info");
        if (info.active_rebuild_job) await resumeActiveRebuild(info.active_rebuild_job);
      } catch {
        /* ignore secondary failure */
      }
      return;
    }
    showRebuildResultModal(t("qc_rebuild_failed", { error: message }), "error");
  }
}

async function loadCollections() {
  try {
    const data = await api("/api/datasets/collections");
    state.collections = data.collections || [];
    els.collectionSelect.innerHTML = "";
    state.collections.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = `${c.title || c.id} (${c.package_count || 0})`;
      els.collectionSelect.appendChild(opt);
    });
    if (state.queryCollection) {
      els.collectionSelect.value = state.queryCollection;
    } else if (state.collections.length) {
      els.collectionSelect.value = state.collections[0].id;
    }
    if (els.collectionSelect.value) {
      await onCollectionChange();
      if (state.queryPackage) els.packageSelect.value = state.queryPackage;
    }
  } catch (error) {
    els.connectHelper.textContent = `Catalog unavailable: ${error.message || error}`;
  }
}

async function onCollectionChange() {
  const collectionId = els.collectionSelect.value;
  els.packageSelect.innerHTML = `<option value="">—</option>`;
  syncUrlQuery();
  if (!collectionId) return;
  const collection = await api(`/api/datasets/collections/${encodeURIComponent(collectionId)}`);
  (collection.packages || []).forEach((pkg) => {
    const opt = document.createElement("option");
    opt.value = pkg.id;
    const v3 = pkg.codebase_version === "v3.0" ? "" : ` [${pkg.codebase_version || "?"}]`;
    opt.textContent = `${pkg.display_name || pkg.id}${v3}`;
    opt.disabled = !pkg.loadable;
    els.packageSelect.appendChild(opt);
  });
}

async function onPackageChange() {
  const collectionId = els.collectionSelect.value;
  const packageId = els.packageSelect.value;
  if (!collectionId || !packageId) {
    syncUrlQuery();
    return;
  }
  const resolved = await api(
    `/api/datasets/resolve?collection=${encodeURIComponent(collectionId)}&package=${encodeURIComponent(packageId)}`,
  );
  els.datasetPathInput.value = resolved.local_path;
  syncUrlQuery();
}

function onKeyboard(event) {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.target.matches("input, textarea, select")) return;
  if (state.currentEpisode == null) return;
  if (event.key === "a" || event.key === "A") postReview("approved");
  if (event.key === "r" || event.key === "R") postReview("rejected");
  if (event.key === "s" || event.key === "S") postReview("suspicious");
  if (event.key === " ") {
    event.preventDefault();
    togglePlayback();
  }
  if (event.key === "ArrowLeft") setFrame(state.frameIndex - 1);
  if (event.key === "ArrowRight") setFrame(state.frameIndex + 1);
}

function onPlayheadMouseDown(e) {
  return false;
}

function onRulerMouseDown(e) {
  return false;
}

function onTimelineMouseMove(e) {
  return;
}

function onTimelineMouseUp(e) {
  return;
}

function bindTimelineEvents() {
  return;
}

els.connectForm.addEventListener("submit", loadDataset);
els.collectionSelect.addEventListener("change", onCollectionChange);
els.packageSelect.addEventListener("change", onPackageChange);
els.episodeSearch.addEventListener("input", renderEpisodeList);
els.episodeStatusFilter?.addEventListener("change", renderEpisodeList);
els.playPauseBtn.addEventListener("click", togglePlayback);
els.stepBackBtn.addEventListener("click", () => setFrame(currentFrame() - 1));
els.stepFwdBtn.addEventListener("click", () => setFrame(currentFrame() + 1));
els.approveBtn.addEventListener("click", () => postReview("approved"));
els.rejectBtn.addEventListener("click", () => postReview("rejected"));
els.suspiciousBtn.addEventListener("click", () => postReview("suspicious"));
els.restoreEpisodeBtn?.addEventListener("click", () => postReview("pending"));
els.saveInstructionBtn.addEventListener("click", saveInstruction);
els.rebuildBtn.addEventListener("click", startRebuild);
els.closeRebuildModal.addEventListener("click", hideRebuildModal);
els.screeningFileInput.addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file || !state.dataset) return;
  try {
    await importScreeningFile(file);
  } catch (error) {
    els.connectHelper.textContent = `Screening import failed: ${error.message || error}`;
  }
  event.target.value = "";
});
if (els.connectToggle) {
  els.connectToggle.addEventListener("click", () => {
    const expanded = els.connectToggle.getAttribute("aria-expanded") === "true";
    els.connectToggle.setAttribute("aria-expanded", expanded ? "false" : "true");
    if (els.connectForm) {
      els.connectForm.classList.toggle("collapsed", expanded);
    }
  });
}
window.addEventListener("resize", drawCharts);
window.addEventListener("keydown", onKeyboard);
window.__laOnLocaleChange = () => {
  window.LA_I18N.applyStaticUI();
  renderEpisodeList();
  updatePlayhead();
  if (!state.playing) els.playPauseBtn.textContent = t("qc_btn_play");
};

bindTimelineEvents();
window.LA_I18N?.init();
readQueryParams();
loadCollections().then(() => {
  if (els.datasetPathInput.value.trim()) loadDataset();
});
