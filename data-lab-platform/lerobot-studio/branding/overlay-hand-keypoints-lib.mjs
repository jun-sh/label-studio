/**
 * Pure hand-kp2d overlay logic (testable, no DOM).
 * Invariants: see .cursor/rules/hand-overlay.mdc
 */
export const OVERLAY_VERSION = 20;

export const RENDER = {
  LINE_WIDTH: 2,
  WRIST_RADIUS: 3,
  JOINT_RADIUS: 1.5,
  ALPHA_HIGH: 0.95,
  ALPHA_LOW: 0.45,
  ALPHA_REPROJ_CAP: 0.55,
  REPROJ_THRESHOLD: 12.0,
  REPROJ_THRESHOLD_LEFT: 96.0,
  QUALITY_LOW: 2,
};

export const HAND_COLORS_RGB = {
  left: { edge: [246, 130, 59], joint: [253, 197, 147] },
  right: { edge: [94, 197, 34], joint: [21, 204, 250] },
  unknown: { edge: [247, 85, 168], joint: [254, 180, 216] },
};

export const LEGACY_TO_CANONICAL_VIDEO_KEY = {
  "observation.images.camera_head_left": "observation.images.camera_front_left",
  "observation.images.camera_head_right": "observation.images.camera_front_right",
  "observation.images.camera_depth_head": "observation.images.camera_rear_left",
  "observation.images.camera_02": "observation.images.camera_rear_right",
};

export const FRONT_CAMERA_SHORTS = [
  "camera_front_left",
  "camera_front_right",
  "camera_rear_left",
  "camera_rear_right",
];

/** Scheme A: skeleton only on front_left; front_right reserved for depth preview. */
export const HAND_OVERLAY_EXCLUDE_VIDEO_KEYS = new Set([
  "observation.images.camera_front_right",
  "observation.images.camera_head_right",
]);

export function cameraShortName(featureKey) {
  return (featureKey || "").split(".").pop() || "";
}

export function canonicalVideoKey(featureKey) {
  return LEGACY_TO_CANONICAL_VIDEO_KEY[featureKey] || featureKey;
}

export function featureMatchesText(text, featureKey) {
  if (!text) return false;
  if (text.indexOf(featureKey) >= 0) return true;
  const short = cameraShortName(featureKey);
  if (!short || text.indexOf(short) < 0) return false;
  for (const other of FRONT_CAMERA_SHORTS) {
    if (other === short) continue;
    if (text.indexOf(other) >= 0) return false;
  }
  return true;
}

export function normalizeEpisodeNumber(ep) {
  const n = parseInt(String(ep), 10);
  return Number.isFinite(n) ? n : 0;
}

export function parseEpisodeFromLabel(label) {
  if (!label) return null;
  const mSelect =
    label.match(/(?:选择 Episode|Select Episode)\s*(\d+)/i);
  if (mSelect) return String(parseInt(mSelect[1], 10));

  // LeRobot sidebar title: "# 100:10" => episode 1 + 00:10 (not episode 100).
  const mLerobotTs = label.match(/#\s*(\d)\d{2}[:：]\d{2}/);
  if (mLerobotTs) return String(parseInt(mLerobotTs[1], 10));

  const mHash = label.match(/#\s*(\d+)\b/);
  if (mHash) return String(parseInt(mHash[1], 10));
  return null;
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

export function scoreAriaCurrentEpisodes(nodes) {
  let best = null;
  for (const node of nodes || []) {
    const label = `${node.ariaLabel || ""} ${node.text || ""}`;
    const ep = parseEpisodeFromLabel(label);
    if (ep === null) continue;
    let score = 0;
    if (node.ariaCurrent) score += 120;
    if (/选择 Episode|Select Episode/i.test(label)) score += 80;
    if (/episode/i.test(label)) score += 10;
    if (/[0-9a-f]{8}/i.test(label)) score += 12;
    if (!best || score > best.score) best = { ep, score };
  }
  return best ? best.ep : null;
}

export function sessionFingerprintFromStrings(...parts) {
  for (const part of parts) {
    if (!part) continue;
    const m = String(part).match(/([0-9a-f]{8})/i);
    if (m) return m[1].toLowerCase();
    const sid = String(part).match(/sess_([0-9a-f]{8})/i);
    if (sid) return sid[1].toLowerCase();
  }
  return null;
}

export function buildEpisodeFingerprintIndex(rootPayload) {
  const byFingerprint = {};
  if (!rootPayload) return { byFingerprint };

  const meta = rootPayload.episode_meta || {};
  for (const [epKey, entry] of Object.entries(meta)) {
    const fp =
      entry.session_fingerprint ||
      sessionFingerprintFromStrings(entry.session_id, entry.task);
    if (!fp) continue;
    byFingerprint[fp] = String(normalizeEpisodeNumber(entry.episode_index ?? epKey));
  }

  const episodes = rootPayload.episodes || {};
  for (const [epKey, epMap] of Object.entries(episodes)) {
    const cam = Object.values(epMap || {})[0];
    if (!cam) continue;
    const fp =
      cam.session_fingerprint || sessionFingerprintFromStrings(cam.session_id, cam.task);
    if (!fp || byFingerprint[fp]) continue;
    byFingerprint[fp] = String(normalizeEpisodeNumber(cam.episode_index ?? epKey));
  }
  return { byFingerprint };
}

export function scoreHeaderNode(node) {
  if (!node || typeof node === "string") return /[0-9a-f]{8}/i.test(node) ? 8 : 0;
  let score = 0;
  if (node.ariaCurrent) score += 100;
  const tag = String(node.tagName || "").toUpperCase();
  if (tag === "H1") score += 60;
  else if (tag === "H2") score += 50;
  else if (tag === "H3") score += 35;
  if (node.role === "heading") score += 15;
  if (node.inMainContent) score += 45;
  if (node.inEpisodeSidebar) score -= 100;
  if (node.inNavigation) score -= 70;
  if (/[0-9a-f]{8}/i.test(node.text || "")) score += 8;
  return score;
}

/**
 * Match session fingerprint (e.g. f2710d2c) from scored header nodes — stable across sidebar DOM order.
 */
export function resolveActiveEpisodeByFingerprint(rootPayload, headerNodes) {
  const { byFingerprint } = buildEpisodeFingerprintIndex(rootPayload);
  const fingerprints = Object.keys(byFingerprint);
  if (!fingerprints.length) return null;

  let best = null;
  for (const fp of fingerprints) {
    const ep = byFingerprint[fp];
    let score = 0;
    for (const node of headerNodes || []) {
      const text = typeof node === "string" ? node : node.text;
      if (!text || text.toLowerCase().indexOf(fp) < 0) continue;
      score += scoreHeaderNode(node);
    }
    if (!best || score > best.score) best = { ep, score, fp };
  }
  if (best && best.score >= 40) return best.ep;
  return null;
}

export function scoreHeaderEpisodes(headerNodes) {
  let best = null;
  for (const node of headerNodes || []) {
    const text = typeof node === "string" ? node : node.text;
    const ep = parseEpisodeFromLabel(text);
    if (ep === null) continue;
    const score = scoreHeaderNode(node);
    if (!best || score > best.score) best = { ep, score };
  }
  return best ? best.ep : null;
}

/**
 * Resolve active episode index from UI context.
 * Priority: URL > aria-current episode button > session fingerprint > header > click > last.
 */
export function resolveActiveEpisodeIndex(ctx = {}) {
  const last = ctx.lastEpisodeIndex != null ? String(ctx.lastEpisodeIndex) : null;
  try {
    const q = new URLSearchParams(ctx.search || "").get("episode");
    if (q !== null && q !== "") return String(normalizeEpisodeNumber(q));
  } catch {
    /* ignore */
  }

  const ariaNodes = ctx.episodeAriaNodes || ctx.ariaCurrentNodes;
  const fromAria = scoreAriaCurrentEpisodes(ariaNodes);
  if (fromAria !== null) return fromAria;

  const headerNodes = ctx.headerNodes || ctx.headerTexts;
  const fromFingerprint = resolveActiveEpisodeByFingerprint(ctx.rootPayload, headerNodes);
  if (fromFingerprint !== null) return fromFingerprint;

  const fromHeaders = scoreHeaderEpisodes(headerNodes);
  if (fromHeaders !== null) return fromHeaders;

  if (ctx.clickedEpisode != null) return String(ctx.clickedEpisode);

  if (ctx.selectedEpisode != null) return String(ctx.selectedEpisode);

  if (last != null) return last;
  return "0";
}

export function resolveEpisodeMap(rootPayload, activeEpisodeIndex) {
  if (!rootPayload || !rootPayload.episodes) {
    return { key: null, map: null, episodeNumber: null };
  }
  const eps = rootPayload.episodes;
  const candidates = episodeKeyCandidates(activeEpisodeIndex);
  for (const key of candidates) {
    if (eps[key]) {
      return {
        key,
        map: eps[key],
        episodeNumber: normalizeEpisodeNumber(key),
      };
    }
  }
  const want = normalizeEpisodeNumber(activeEpisodeIndex);
  for (const key of Object.keys(eps)) {
    const epMap = eps[key];
    const camKey = Object.keys(epMap || {})[0];
    const cam = camKey ? epMap[camKey] : null;
    if (cam && normalizeEpisodeNumber(cam.episode_index) === want) {
      return { key, map: epMap, episodeNumber: want };
    }
  }
  return { key: null, map: null, episodeNumber: null };
}

export function cameraPayloadFromSub(sub, rootPayload) {
  const reprojRight =
    sub.reproj_threshold_px_right ||
    sub.reproj_threshold_px ||
    rootPayload.reproj_threshold_px_right ||
    rootPayload.reproj_threshold_px ||
    RENDER.REPROJ_THRESHOLD;
  const reprojLeft =
    sub.reproj_threshold_px_left ||
    rootPayload.reproj_threshold_px_left ||
    RENDER.REPROJ_THRESHOLD_LEFT;
  return {
    fps: sub.fps || rootPayload.fps || 30,
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
    episode_index: sub.episode_index,
    reproj_threshold_px: reprojRight,
    reproj_threshold_px_left: reprojLeft,
    reproj_threshold_px_right: reprojRight,
  };
}

export function buildLookup(payload) {
  const lookup = new Map();
  const frames = payload.frame_index || [];
  const kp2d = payload.kp2d || [];
  for (let i = 0; i < frames.length; i += 1) {
    lookup.set(Number(frames[i]), kp2d[i] || null);
  }
  return lookup;
}

export function frameRowIndex(payload, frameIdx) {
  const frames = payload.frame_index || [];
  for (let i = 0; i < frames.length; i += 1) {
    if (Number(frames[i]) === frameIdx) return i;
  }
  return -1;
}

export function isValidJoint(u, v) {
  return Number.isFinite(u) && Number.isFinite(v) && (u > 1 || v > 1);
}

export function isValidWrist(flat) {
  return flat && flat.length >= 2 && isValidJoint(flat[0], flat[1]);
}

export function reprojThreshold(payload) {
  let t = payload && payload.reproj_threshold_px_right;
  if (!Number.isFinite(t) || t <= 0) {
    t = payload && payload.reproj_threshold_px;
  }
  return Number.isFinite(t) && t > 0 ? t : RENDER.REPROJ_THRESHOLD;
}

export function reprojThresholdForHand(payload, side) {
  if (side === "left") {
    const tl = payload && payload.reproj_threshold_px_left;
    if (Number.isFinite(tl) && tl > 0) return tl;
  }
  return reprojThreshold(payload);
}

export function metricForHand(handPayload, payload, row, key) {
  if (handPayload && handPayload[key] && handPayload[key][row] !== undefined) {
    return handPayload[key][row];
  }
  if (payload && payload.hands_mode === "both" && payload.hands) {
    return undefined;
  }
  if (payload && payload[key] && payload[key][row] !== undefined) {
    return payload[key][row];
  }
  return undefined;
}

export function shouldDrawHand(side, frameQuality, reprojErr, payload, handConfidence) {
  if (side === "left") {
    if (handConfidence !== undefined && Number(handConfidence) <= 0) return false;
  } else if (
    handConfidence !== undefined &&
    Number(handConfidence) > 0 &&
    Number(handConfidence) < 0.5
  ) {
    return false;
  }
  const threshold = reprojThresholdForHand(payload, side);
  if (frameQuality !== undefined && Number(frameQuality) >= RENDER.QUALITY_LOW) return false;
  if (reprojErr !== undefined && Number(reprojErr) > threshold) return false;
  return true;
}

export function blendAlphaForRow(frameQuality, reprojErr, reprojThresholdPx) {
  let alpha = RENDER.ALPHA_HIGH;
  if (frameQuality !== undefined && Number(frameQuality) >= RENDER.QUALITY_LOW) {
    alpha = RENDER.ALPHA_LOW;
  }
  const threshold = reprojThresholdPx || RENDER.REPROJ_THRESHOLD;
  if (reprojErr !== undefined && Number(reprojErr) > threshold) {
    alpha = Math.min(alpha, RENDER.ALPHA_REPROJ_CAP);
  }
  return alpha;
}

export function alphaForHand(side, handPayload, row, payload) {
  const threshold = reprojThresholdForHand(payload, side);
  const conf = handPayload.hand_confidence ? handPayload.hand_confidence[row] : undefined;
  if (side === "left" && conf !== undefined && Number(conf) > 0) {
    return Math.max(RENDER.ALPHA_LOW, Math.min(1, Number(conf)));
  }
  const fq = handPayload.frame_quality;
  const re = handPayload.reprojection_error;
  return blendAlphaForRow(fq ? fq[row] : undefined, re ? re[row] : undefined, threshold);
}

export function handsToDraw(payload, frameIdx, lookup) {
  const row = frameRowIndex(payload, frameIdx);
  const out = [];
  if (payload.hands_mode === "both" && payload.hands) {
    for (const side of ["left", "right"]) {
      const h = payload.hands[side];
      if (!h || !h.kp2d || row < 0) continue;
      const fq = metricForHand(h, payload, row, "frame_quality");
      const re = metricForHand(h, payload, row, "reprojection_error");
      const conf = h.hand_confidence ? h.hand_confidence[row] : undefined;
      if (!shouldDrawHand(side, fq, re, payload, conf)) continue;
      const flat = h.kp2d[row];
      if (!isValidWrist(flat)) continue;
      out.push({ side, flat, alpha: alphaForHand(side, h, row, payload) });
    }
    if (out.length) return out;
  }
  const flat = lookup.get(frameIdx);
  if (!isValidWrist(flat)) return [];
  const fqLegacy = payload.frame_quality ? payload.frame_quality[row] : undefined;
  const reLegacy = payload.reprojection_error ? payload.reprojection_error[row] : undefined;
  if (!shouldDrawHand("right", fqLegacy, reLegacy, payload, undefined)) return [];
  return [{ side: "right", flat, alpha: blendAlphaForRow(fqLegacy, reLegacy, reprojThreshold(payload)) }];
}

export function resolveEpisodeCameraPayloads(rootPayload, activeEpisodeIndex) {
  if (!rootPayload) return [];
  const resolved = resolveEpisodeMap(rootPayload, activeEpisodeIndex);
  if (!resolved.map) return [];

  if (rootPayload.version === 4) {
    const epMap = resolved.map;
    const list = [];
    const vkeys = rootPayload.video_keys || Object.keys(epMap);
    for (const vkRaw of vkeys) {
      const vk = canonicalVideoKey(vkRaw);
      if (HAND_OVERLAY_EXCLUDE_VIDEO_KEYS.has(vk)) continue;
      const sub = epMap[vkRaw] || epMap[vk];
      if (!sub) continue;
      if (
        sub.episode_index !== undefined &&
        normalizeEpisodeNumber(sub.episode_index) !== resolved.episodeNumber
      ) {
        continue;
      }
      list.push({
        video_key: vk,
        payload: cameraPayloadFromSub(sub, rootPayload),
      });
    }
    return list;
  }

  if (rootPayload.version === 3) {
    const sub = resolved.map;
    return [
      {
        video_key: canonicalVideoKey(sub.video_key || rootPayload.video_key),
        payload: cameraPayloadFromSub(sub, rootPayload),
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

export function validateOverlayPayload(rootPayload) {
  const errors = [];
  if (!rootPayload) {
    errors.push("missing payload");
    return errors;
  }
    if (rootPayload.version === 4) {
    if (!rootPayload.reproj_threshold_px_left) {
      errors.push("v4 missing reproj_threshold_px_left at root");
    }
    if (!rootPayload.reproj_threshold_px_right) {
      errors.push("v4 missing reproj_threshold_px_right at root");
    }
    const episodes = rootPayload.episodes || {};
    if (Object.keys(episodes).length > 1 && !rootPayload.episode_meta) {
      errors.push("v4 multi-episode missing episode_meta");
    }
    for (const [epKey, epMap] of Object.entries(episodes)) {
      for (const [vk, sub] of Object.entries(epMap)) {
        if (!sub.hands) errors.push(`${epKey}/${vk}: missing hands`);
        if (sub.hands_mode !== "both") errors.push(`${epKey}/${vk}: hands_mode not both`);
        if (!sub.reproj_threshold_px_left) errors.push(`${epKey}/${vk}: missing reproj_threshold_px_left`);
        if (sub.episode_index !== undefined && normalizeEpisodeNumber(sub.episode_index) !== normalizeEpisodeNumber(epKey)) {
          errors.push(`${epKey}/${vk}: episode_index mismatch ${sub.episode_index}`);
        }
      }
    }
  }
  return errors;
}
