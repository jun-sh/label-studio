/**
 * Unified EGO episode delivery status (preview / pose / qc / export).
 */

import fs from "node:fs";
import path from "node:path";

import { canonicalDatasetKey } from "./qc-dataset-key.mjs";
import { readManifest } from "./derive/manifest.mjs";
import { isPoseReady } from "./derive/provenance.mjs";
import { stationRoot } from "./ingest/station-context.mjs";
import { hasSessionMarker, readSessionMarker, SESSION_MARKERS } from "./session-markers.mjs";

function datalabRoot() {
  return process.env.DATALAB_ROOT || process.env.EGO_DATALAB_ROOT || path.resolve(stationRoot(""), "../..");
}

function dataStorageRoot() {
  if (process.env.DATALAB_ROOT || process.env.EGO_DATALAB_ROOT) {
    return path.join(datalabRoot(), "data-storage");
  }
  return path.join(datalabRoot(), "data-storage");
}

function corpusRoot(stationId, slug) {
  return path.join(dataStorageRoot(), "corpus", slug);
}

function deliveryRoot(stationId) {
  return path.join(dataStorageRoot(), "ego-delivery", stationId);
}

function qcSidecarBase() {
  const base = path.join(dataStorageRoot(), "lerobot-qc");
  const sidecar = path.join(base, "sidecar");
  return fs.existsSync(sidecar) ? sidecar : base;
}

function readJson(filePath, fallback = null) {
  if (!fs.existsSync(filePath)) return fallback;
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

function listSessionIds(root) {
  const sessions = new Set();
  const manifest = readManifest(root);
  for (const ep of manifest?.episodes || []) {
    if (ep?.session_id) sessions.add(ep.session_id);
  }
  const stateDir = path.join(root, "state", "sessions");
  if (fs.existsSync(stateDir)) {
    for (const name of fs.readdirSync(stateDir)) {
      if (name.startsWith("sess_")) sessions.add(name);
    }
  }
  return [...sessions].sort();
}

function findQcManifestForCorpus(corpusPath) {
  const base = qcSidecarBase();
  if (!fs.existsSync(base)) return null;
  const targetKey = canonicalDatasetKey(path.resolve(corpusPath));
  for (const entry of fs.readdirSync(base)) {
    const manifestPath = path.join(base, entry, "qc_manifest.json");
    if (!fs.existsSync(manifestPath)) continue;
    const manifest = readJson(manifestPath, null);
    if (!manifest?.dataset_root) continue;
    const storedKey = canonicalDatasetKey(path.resolve(String(manifest.dataset_root)));
    if (storedKey === targetKey) return manifest;
  }
  return null;
}

function qcStatusForEpisode(qcManifest, episodeIndex) {
  if (!qcManifest) return "pending";
  const review = (qcManifest.reviews || {})[String(episodeIndex)] || {};
  return String(review.status || "pending");
}

function exportMarkerPath(stationId, sessionId) {
  const ordersRoot = path.join(dataStorageRoot(), "ego-delivery", "orders");
  if (fs.existsSync(ordersRoot)) {
    for (const orderId of fs.readdirSync(ordersRoot)) {
      const marker = path.join(
        ordersRoot,
        orderId,
        "episodes",
        sessionId,
        "meta",
        "export_manifest.json",
      );
      if (fs.existsSync(marker)) return marker;
    }
  }
  return path.join(deliveryRoot(stationId), "by-session", sessionId, "meta", "export_manifest.json");
}

function aggregateDeliveryStatus({ preview_ready, pose_ready, qc_approved, exported }) {
  if (exported) return "exported";
  if (preview_ready && pose_ready && qc_approved) return "exportable";
  if (preview_ready && pose_ready) return "pose_ready";
  if (preview_ready) return "preview_ready";
  return "pending";
}

function datasetSlugForStation(stationId, override) {
  if (override) return override;
  const map = { "ego-001": "ego_001" };
  return map[stationId] || stationId.replace(/-/g, "_");
}

export function episodeDeliveryRow(root, stationId, sessionId, options = {}) {
  const slug = datasetSlugForStation(stationId, options.datasetSlug);
  const manifest = readManifest(root);
  const ep = (manifest?.episodes || []).find((e) => e.session_id === sessionId) || {};
  const episodeIndex = Number.isFinite(ep.episode_index) ? ep.episode_index : null;

  const preview_ready = hasSessionMarker(root, sessionId, SESSION_MARKERS.READY);
  const pose_ready = isPoseReady(root, stationId, sessionId);
  const qcManifest = findQcManifestForCorpus(corpusRoot(stationId, slug));
  const qcStatus = episodeIndex === null ? "pending" : qcStatusForEpisode(qcManifest, episodeIndex);
  const qc_approved = qcStatus === "approved";
  const exported = fs.existsSync(exportMarkerPath(stationId, sessionId));

  const readyMarker = readSessionMarker(root, sessionId, SESSION_MARKERS.READY);
  const finalizePath = path.join(
    dataStorageRoot(),
    "pipeline",
    stationId,
    sessionId,
    ".status",
    "finalize.done",
  );
  const sloBSec = Number(process.env.EGO_CONVERT_SLO_B_SEC || 600);
  let convert_slo_b_ok = null;
  if (preview_ready && pose_ready && readyMarker?.at && fs.existsSync(finalizePath)) {
    const readyAt = Date.parse(readyMarker.at);
    const finalizeAt = fs.statSync(finalizePath).mtimeMs;
    if (Number.isFinite(readyAt) && Number.isFinite(finalizeAt)) {
      convert_slo_b_ok = finalizeAt - readyAt <= sloBSec * 1000;
    }
  }

  return {
    session_id: sessionId,
    episode_index: episodeIndex,
    preview_ready,
    pose_ready,
    qc_status: qcStatus,
    qc_approved,
    exported,
    convert_slo_b_ok,
    delivery_status: aggregateDeliveryStatus({ preview_ready, pose_ready, qc_approved, exported }),
    video_codec: ep.video_codec || ep.provenance?.video_codec || null,
    pose_ready_at: fs.existsSync(finalizePath) ? new Date(fs.statSync(finalizePath).mtimeMs).toISOString() : null,
    preview_ready_at: readyMarker?.at || null,
  };
}

export function getEpisodeDeliveryStatus(stationId, options = {}) {
  const root = stationRoot(stationId);
  const sessionFilter = options.sessionId || options.session;
  const sessions = sessionFilter ? [sessionFilter] : listSessionIds(root);
  const episodes = sessions.map((sessionId) =>
    episodeDeliveryRow(root, stationId, sessionId, options),
  );
  return {
    stationId,
    contract: "ego_export_contract@1.0",
    slo_b_seconds: Number(process.env.EGO_CONVERT_SLO_B_SEC || 600),
    episodes,
    updatedAt: new Date().toISOString(),
  };
}
