/**
 * MaxInsights-aligned perception module status (9 measures + production loop).
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { readManifest } from "./derive/manifest.mjs";
import { unitJsonPath } from "./derive/unit-paths.mjs";
import { parseExtendedInfo } from "./lerobot-converter.mjs";
import { hasSessionMarker, SESSION_MARKERS } from "./session-markers.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CONFIG_PATH = path.resolve(__dirname, "../config/ego-perception-modules.json");

let cachedConfig = null;

function readJson(filePath, fallback = null) {
  if (!fs.existsSync(filePath)) return fallback;
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

export function loadPerceptionModuleConfig() {
  if (cachedConfig) return cachedConfig;
  cachedConfig = readJson(CONFIG_PATH, { modules: [], production_loop: [], taxonomy: {} });
  return cachedConfig;
}

function dataStorageRoot() {
  const explicit = process.env.DATALAB_ROOT || process.env.EGO_DATALAB_ROOT || "";
  if (explicit) return path.join(explicit, "data-storage");
  return path.resolve(__dirname, "../../data-storage");
}

function corpusRoot(slug) {
  return path.join(dataStorageRoot(), "corpus", slug);
}

function samplesRoot() {
  return path.join(dataStorageRoot(), "samples");
}

function pipelineSessionRoot(stationId, sessionId) {
  return path.join(dataStorageRoot(), "pipeline", stationId, sessionId);
}

function readUnitJson(root, sessionId) {
  return readJson(unitJsonPath(root, sessionId), null);
}

function readInfoFeatures(corpusPath) {
  const info = readJson(path.join(corpusPath, "meta", "info.json"), null);
  return info?.features && typeof info.features === "object" ? info.features : {};
}

function hasL3DepthPreview(slug, sessionId) {
  const candidates = [
    path.join(samplesRoot(), `${slug}_depth_preview.json`),
    path.join(samplesRoot(), `${slug}_hand_kp2d.json`),
    path.join(corpusRoot(slug), "samples", sessionId, "depth_preview.json"),
  ];
  return candidates.some((p) => fs.existsSync(p));
}

function hasImuDerived(root, sessionId) {
  const unit = readUnitJson(root, sessionId);
  if (unit?.artifacts?.["imu.parquet"]) return true;
  const derivedImu = path.join(root, "derived", sessionId, "imu.parquet");
  return fs.existsSync(derivedImu);
}

function probeModuleStatus(module, ctx) {
  switch (module.probe) {
    case "preview_ready":
      return ctx.preview_ready ? "active" : "pending";
    case "l3_depth_preview":
      if (ctx.preview_ready && hasL3DepthPreview(ctx.slug, ctx.sessionId)) return "active";
      if (ctx.preview_ready) return "partial";
      return "pending";
    case "hand_pose":
      if (ctx.pose_ready) return "active";
      if (ctx.preview_ready) return "partial";
      return "pending";
    case "head_pose_feature":
      if ("observation.head_pose" in ctx.features) {
        return ctx.pose_ready ? "active" : "partial";
      }
      return "pending";
    case "imu_derived":
      return hasImuDerived(ctx.root, ctx.sessionId) ? "active" : "pending";
    case "unsupported_hardware":
      return "skipped";
    case "planned":
      return "planned";
    default:
      return "pending";
  }
}

export function getPerceptionModuleStatus(ctx) {
  const config = loadPerceptionModuleConfig();
  return (config.modules || [])
    .slice()
    .sort((a, b) => Number(a.order || 0) - Number(b.order || 0))
    .map((module) => ({
      id: module.id,
      order: module.order,
      label_en: module.label_en,
      label_zh: module.label_zh,
      status: probeModuleStatus(module, ctx),
      pipeline_layers: module.pipeline_layers || [],
      backends: module.backends || [],
      feature_keys: module.feature_keys || [],
    }));
}

function readEpisodeTaxonomy(root, sessionId, slug, episodeIndex, qcManifest) {
  const manifest = readManifest(root);
  const ep = (manifest?.episodes || []).find((e) => e.session_id === sessionId) || {};
  const unit = readUnitJson(root, sessionId);
  const segmentManifest = readJson(
    path.join(root, "raw", "segments", sessionId, "seg_000001", "manifest.json"),
    null,
  );

  const taskId =
    String(ep.task_id || unit?.task_id || segmentManifest?.task_id || "").trim() || null;

  let sceneId = null;
  for (const source of [ep.extended_info, unit?.extended_info, segmentManifest?.extended_info]) {
    if (source == null) continue;
    const ext = parseExtendedInfo(source);
    if (ext.scene_id) {
      sceneId = String(ext.scene_id);
      break;
    }
  }

  let subtaskStatus = "none";
  const subtasksPath = path.join(corpusRoot(slug), "meta", "subtasks.parquet");
  if (fs.existsSync(subtasksPath)) {
    subtaskStatus = "available";
  }

  let instruction = null;
  if (episodeIndex !== null && qcManifest?.instruction_overrides) {
    instruction = qcManifest.instruction_overrides[String(episodeIndex)] || null;
  }
  const tasksPath = path.join(corpusRoot(slug), "meta", "tasks.parquet");
  if (!instruction && fs.existsSync(tasksPath)) {
    instruction = "available_in_tasks_parquet";
  }

  return {
    environment: sceneId,
    task: taskId,
    subtask: subtaskStatus,
    instruction,
  };
}

function productionLoopRow(loop, ctx) {
  let complete = false;
  switch (loop.gate) {
    case "task_id_present":
      complete = Boolean(ctx.taxonomy.task);
      break;
    case "done_upload":
      complete = ctx.done_upload;
      break;
    case "preview_ready":
      complete = ctx.preview_ready;
      break;
    case "qc_approved":
      complete = ctx.qc_approved;
      break;
    case "exportable":
      complete = ctx.preview_ready && ctx.pose_ready && ctx.qc_approved;
      break;
    default:
      complete = false;
  }
  return {
    id: loop.id,
    label_en: loop.label_en,
    label_zh: loop.label_zh,
    complete,
    gate: loop.gate,
  };
}

export function getMaxInsightsPipelineStatus({
  root,
  stationId,
  sessionId,
  slug,
  episodeIndex = null,
  preview_ready = false,
  pose_ready = false,
  qc_approved = false,
  qcManifest = null,
}) {
  const config = loadPerceptionModuleConfig();
  const done_upload = hasSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD);
  const features = readInfoFeatures(corpusRoot(slug));
  const ctx = {
    root,
    stationId,
    sessionId,
    slug,
    preview_ready,
    pose_ready,
    qc_approved,
    features,
  };
  const taxonomy = readEpisodeTaxonomy(root, sessionId, slug, episodeIndex, qcManifest);
  const perception_modules = getPerceptionModuleStatus(ctx);
  const activeCount = perception_modules.filter((m) => m.status === "active").length;
  const production_loop = (config.production_loop || []).map((loop) =>
    productionLoopRow(loop, { ...ctx, taxonomy, done_upload }),
  );

  return {
    model: config.schema_version || "maxinsights_aligned@1.0",
    reference: config.reference || "https://www.maxinsights.ai/#1",
    taxonomy,
    perception_modules,
    active_module_count: activeCount,
    total_module_count: perception_modules.length,
    production_loop,
    production_loop_complete: production_loop.every((step) => step.complete),
  };
}
