/**
 * Depth preview overlay logic (front_left depth shown in front_right slot).
 */
export const DEPTH_OVERLAY_VERSION = 3;

export const SOURCE_VIDEO_KEY = "observation.images.camera_front_left";
export const DISPLAY_VIDEO_KEY = "observation.images.camera_front_right";
export const DISPLAY_PANEL_LABEL = "depth_front_left";

export const LEGACY_TO_CANONICAL_VIDEO_KEY = {
  "observation.images.camera_head_left": "observation.images.camera_front_left",
  "observation.images.camera_head_right": "observation.images.camera_front_right",
  "observation.images.camera_depth_head": "observation.images.camera_rear_left",
  "observation.images.camera_depth_left": "observation.images.camera_rear_left",
  "observation.images.camera_02": "observation.images.camera_rear_right",
};

export function canonicalVideoKey(featureKey) {
  return LEGACY_TO_CANONICAL_VIDEO_KEY[featureKey] || featureKey;
}

export function cameraShortName(featureKey) {
  return (featureKey || "").split(".").pop() || "";
}

export function displayPanelLabel(payload) {
  return payload?.display_panel_label || DISPLAY_PANEL_LABEL;
}

export function featureMatchesText(text, featureKey) {
  if (!text) return false;
  if (
    featureKey === DISPLAY_VIDEO_KEY
    || canonicalVideoKey(featureKey) === DISPLAY_VIDEO_KEY
  ) {
    const panelLabel = DISPLAY_PANEL_LABEL;
    if (text.indexOf(panelLabel) >= 0) return true;
  }
  if (text.indexOf(featureKey) >= 0) return true;
  const short = cameraShortName(featureKey);
  if (!short || text.indexOf(short) < 0) return false;
  const shorts = [
    "camera_front_left",
    "camera_front_right",
    "camera_rear_left",
    "camera_rear_right",
  ];
  for (const other of shorts) {
    if (other === short) continue;
    if (text.indexOf(other) >= 0) return false;
  }
  return true;
}

export function normalizeEpisodeNumber(ep) {
  const n = parseInt(String(ep), 10);
  return Number.isFinite(n) ? n : 0;
}

export function episodeKeyCandidates(activeEpisodeIndex) {
  const n = normalizeEpisodeNumber(activeEpisodeIndex);
  const keys = [String(n), String(activeEpisodeIndex), String(n).padStart(6, "0")];
  const seen = new Set();
  const out = [];
  for (const key of keys) {
    if (!seen.has(key)) {
      seen.add(key);
      out.push(key);
    }
  }
  return out;
}

export function resolveEpisodeEntry(payload, activeEpisodeIndex) {
  const episodes = payload?.episodes || {};
  for (const key of episodeKeyCandidates(activeEpisodeIndex)) {
    if (episodes[key]) return { episodeKey: key, entry: episodes[key] };
  }
  const keys = Object.keys(episodes).sort();
  if (keys.length === 1) return { episodeKey: keys[0], entry: episodes[keys[0]] };
  return { episodeKey: null, entry: null };
}

export function frameIndexForVideo(video, payload, fpsFallback = 30) {
  const fps = Number(payload?.fps) || fpsFallback;
  const t = Number(video?.currentTime) || 0;
  return Math.max(0, Math.min(Math.round(t * fps), 1_000_000));
}

export function frameUrl(payload, episodeKey, frameIndex) {
  const template = payload?.frame_url_template || "";
  if (!template) return "";
  const ep = episodeKey;
  const frame = String(frameIndex).padStart(6, "0");
  return template.replace("{episode}", ep).replace("{frame:06d}", frame).replace("{frame}", frame);
}

export function displayFeatureKey(payload) {
  return canonicalVideoKey(payload?.display_video_key || DISPLAY_VIDEO_KEY);
}

export function resolveVideoKeyCandidates(featureKey) {
  const out = [];
  const seen = new Set();
  function push(key) {
    if (!key || seen.has(key)) return;
    seen.add(key);
    out.push(key);
  }
  push(featureKey);
  push(canonicalVideoKey(featureKey));
  push(LEGACY_TO_CANONICAL_VIDEO_KEY[featureKey]);
  for (const legacy in LEGACY_TO_CANONICAL_VIDEO_KEY) {
    if (LEGACY_TO_CANONICAL_VIDEO_KEY[legacy] === featureKey) push(legacy);
    if (LEGACY_TO_CANONICAL_VIDEO_KEY[legacy] === canonicalVideoKey(featureKey)) push(legacy);
  }
  return out;
}

export function displayVideoKeyCandidates(payload) {
  return resolveVideoKeyCandidates(displayFeatureKey(payload));
}

export function containedVideoLayout(videoWidth, videoHeight, clientWidth, clientHeight) {
  if (clientWidth <= 0 || clientHeight <= 0) return null;
  if (videoWidth <= 0 || videoHeight <= 0) {
    return { offsetX: 0, offsetY: 0, width: clientWidth, height: clientHeight, scale: 1 };
  }
  const scale = Math.min(clientWidth / videoWidth, clientHeight / videoHeight);
  const width = videoWidth * scale;
  const height = videoHeight * scale;
  return {
    offsetX: (clientWidth - width) / 2,
    offsetY: (clientHeight - height) / 2,
    width,
    height,
    scale,
  };
}
