/**
 * Canonical LeRobot v3 schema helpers (EgoVerse-aligned, modality-extensible).
 *
 * Engineering rules:
 * - Business metadata lives in meta/episodes.parquet (via live index + parquet sync).
 * - Observation scalars registered only through info.json features.
 * - Robot modality columns are registered only when manifest.modalities declares them.
 * - extended_info is always a JSON string.
 */

export const PIPELINE_VERSION = "1.0.0";

/** Core episode metadata columns (meta/episodes.parquet). */
export const EPISODE_META_CORE_KEYS = [
  "station_id",
  "embodiment",
  "task_id",
  "annotation_status",
  "pipeline_version",
];

export const EPISODE_QUALITY_KEYS = [
  "quality_valid_hand_ratio",
  "quality_mean_jitter",
];

export const EPISODE_EXTENDED_INFO_KEY = "extended_info";

/** Human demonstration default scalar features (zero-filled at ingest). */
export const HUMAN_DEMO_SCALAR_FEATURES = {
  "observation.head_pose": {
    dtype: "float32",
    shape: [7],
    names: ["x", "y", "z", "qx", "qy", "qz", "qw"],
  },
  "observation.hand_pose_left": {
    dtype: "float32",
    shape: [63],
    names: null,
  },
  "observation.hand_pose_right": {
    dtype: "float32",
    shape: [63],
    names: null,
  },
  "observation.hand_conf_left": {
    dtype: "float32",
    shape: [1],
    names: null,
  },
  "observation.hand_conf_right": {
    dtype: "float32",
    shape: [1],
    names: null,
  },
};

/** Robot teleop optional features — registered only when modalities declares them. */
export const ROBOT_MODALITY_FEATURE_BUILDERS = {
  ee_pose: () => ({
    "observation.ee_pose_left": {
      dtype: "float32",
      shape: [7],
      names: ["x", "y", "z", "qx", "qy", "qz", "qw"],
    },
    "observation.ee_pose_right": {
      dtype: "float32",
      shape: [7],
      names: ["x", "y", "z", "qx", "qy", "qz", "qw"],
    },
  }),
  joint_pos: (n) => ({
    "observation.joint_pos": {
      dtype: "float32",
      shape: [Number(n)],
      names: null,
    },
  }),
  joint_vel: (n) => ({
    "observation.joint_vel": {
      dtype: "float32",
      shape: [Number(n)],
      names: null,
    },
  }),
  ee_target: () => ({
    "action.ee_target_pose_left": {
      dtype: "float32",
      shape: [7],
      names: ["x", "y", "z", "qx", "qy", "qz", "qw"],
    },
    "action.ee_target_pose_right": {
      dtype: "float32",
      shape: [7],
      names: ["x", "y", "z", "qx", "qy", "qz", "qw"],
    },
  }),
  joint_target: (n) => ({
    "action.joint_target": {
      dtype: "float32",
      shape: [Number(n)],
      names: null,
    },
  }),
};

/**
 * @param {unknown} value
 * @returns {Record<string, unknown>}
 */
export function parseExtendedInfo(value) {
  if (value == null || value === "") return {};
  if (typeof value === "object" && !Array.isArray(value)) {
    return /** @type {Record<string, unknown>} */ (value);
  }
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed) ? parsed : {};
    } catch {
      return {};
    }
  }
  return {};
}

/**
 * @param {Record<string, unknown>} obj
 * @returns {string}
 */
export function serializeExtendedInfo(obj) {
  return JSON.stringify(obj && typeof obj === "object" ? obj : {});
}

/**
 * @param {Record<string, unknown> | null | undefined} manifest
 * @param {string} [stationId]
 * @returns {import('./lerobot-converter.mjs').EpisodeMeta}
 */
export function parseManifestToEpisodeMeta(manifest, stationId = "unknown") {
  const m = manifest && typeof manifest === "object" ? manifest : {};
  const modalities =
    m.modalities && typeof m.modalities === "object" && !Array.isArray(m.modalities)
      ? m.modalities
      : {};

  const coreFromManifest = {
    station_id: String(m.station_id ?? m.stationId ?? stationId ?? "unknown"),
    embodiment: String(m.embodiment ?? "human_demo"),
    task_id: String(m.task_id ?? m.taskId ?? ""),
    annotation_status: "raw",
    pipeline_version: String(m.schema_version ?? m.pipeline_version ?? PIPELINE_VERSION),
  };

  const extended = parseExtendedInfo(m.extended_info ?? m.extendedInfo);
  for (const key of ["scene_id", "operator_id", "calibration_id", "capture_mode", "robot_sn"]) {
    const v = m[key];
    if (v != null && String(v).trim() !== "") {
      extended[key] = v;
    }
  }

  return {
    ...coreFromManifest,
    modalities,
    extended_info: serializeExtendedInfo(extended),
    quality_valid_hand_ratio: null,
    quality_mean_jitter: null,
  };
}

/**
 * @typedef {Object} EpisodeMeta
 * @property {string} station_id
 * @property {string} embodiment
 * @property {string} task_id
 * @property {string} annotation_status
 * @property {string} pipeline_version
 * @property {Record<string, unknown>} modalities
 * @property {string} extended_info JSON string
 * @property {number | null} [quality_valid_hand_ratio]
 * @property {number | null} [quality_mean_jitter]
 */

/**
 * @param {Record<string, unknown>} modalities
 * @returns {Record<string, object>}
 */
export function robotFeaturesFromModalities(modalities) {
  const mods = modalities && typeof modalities === "object" ? modalities : {};
  /** @type {Record<string, object>} */
  const out = {};
  if (mods.ee_pose) Object.assign(out, ROBOT_MODALITY_FEATURE_BUILDERS.ee_pose());
  if (mods.joint_pos != null) Object.assign(out, ROBOT_MODALITY_FEATURE_BUILDERS.joint_pos(mods.joint_pos));
  if (mods.joint_vel != null) Object.assign(out, ROBOT_MODALITY_FEATURE_BUILDERS.joint_vel(mods.joint_vel));
  if (mods.ee_target) Object.assign(out, ROBOT_MODALITY_FEATURE_BUILDERS.ee_target());
  if (mods.joint_target != null) {
    Object.assign(out, ROBOT_MODALITY_FEATURE_BUILDERS.joint_target(mods.joint_target));
  }
  return out;
}

/**
 * Merge canonical scalar features into info.features (incremental, non-destructive).
 *
 * @param {Record<string, unknown>} info
 * @param {EpisodeMeta | null | undefined} episodeMeta
 */
export function mergeCanonicalFeatures(info, episodeMeta) {
  if (!info || typeof info !== "object") return info;
  if (!info.features || typeof info.features !== "object") info.features = {};

  const embodiment = String(episodeMeta?.embodiment ?? "human_demo");
  const modalities = episodeMeta?.modalities ?? {};

  if (embodiment === "human_demo" || embodiment.startsWith("human_")) {
    Object.assign(info.features, HUMAN_DEMO_SCALAR_FEATURES);
  }

  Object.assign(info.features, robotFeaturesFromModalities(modalities));
  return info;
}

/**
 * Default vector for a scalar feature key (LeRobot list column).
 *
 * @param {string} key
 * @param {Record<string, unknown>} info
 */
export function defaultFeatureVector(key, info) {
  const spec = info?.features?.[key] || {};
  const shape = Array.isArray(spec.shape) ? spec.shape : [1];
  let size = 1;
  for (const dim of shape) size *= Number(dim) || 1;
  const dtype = String(spec.dtype || "float32");
  const zero = dtype.startsWith("int") ? 0 : 0.0;
  if (key === "observation.pose" && size === 7) return [0, 0, 0, 0, 0, 0, 1];
  if (key === "observation.head_pose" && size === 7) return [0, 0, 0, 0, 0, 0, 1];
  return Array.from({ length: size }, () => zero);
}

/**
 * Add canonical observation placeholders to a jsonl row (incremental).
 *
 * @param {Record<string, unknown>} baseRow
 * @param {Record<string, unknown>} info
 */
export function buildCanonicalFrameRow(baseRow, info) {
  const row = { ...baseRow };
  const features = info?.features || {};
  for (const key of Object.keys(features)) {
    if (features[key]?.dtype === "video") continue;
    if (row[key] !== undefined) continue;
    row[key] = defaultFeatureVector(key, info);
  }
  return row;
}

/**
 * Episode-level metadata columns for parquet sync.
 *
 * @param {EpisodeMeta | null | undefined} episodeMeta
 * @param {Record<string, unknown>} [episodeIndexEntry]
 */
export function episodeMetaParquetColumns(episodeMeta, episodeIndexEntry = {}) {
  const entryMeta =
    episodeIndexEntry.episode_meta && typeof episodeIndexEntry.episode_meta === "object"
      ? episodeIndexEntry.episode_meta
      : {};
  const merged = {
    station_id: "unknown",
    embodiment: "human_demo",
    task_id: "",
    annotation_status: "raw",
    pipeline_version: PIPELINE_VERSION,
    extended_info: "{}",
    quality_valid_hand_ratio: null,
    quality_mean_jitter: null,
    ...(episodeMeta || {}),
    ...entryMeta,
  };

  return {
    station_id: String(merged.station_id ?? "unknown"),
    embodiment: String(merged.embodiment ?? "human_demo"),
    task_id: String(merged.task_id ?? ""),
    annotation_status: String(merged.annotation_status ?? "raw"),
    pipeline_version: String(merged.pipeline_version ?? PIPELINE_VERSION),
    extended_info: serializeExtendedInfo(parseExtendedInfo(merged.extended_info)),
    quality_valid_hand_ratio:
      merged.quality_valid_hand_ratio == null ? null : Number(merged.quality_valid_hand_ratio),
    quality_mean_jitter: merged.quality_mean_jitter == null ? null : Number(merged.quality_mean_jitter),
  };
}

/**
 * Attach episode meta to a live episodes-index entry (for parquet sync).
 *
 * @param {Record<string, unknown>} entry
 * @param {EpisodeMeta | null | undefined} episodeMeta
 */
export function attachEpisodeMetaToIndexEntry(entry, episodeMeta) {
  if (!episodeMeta) return entry;
  return {
    ...entry,
    episode_meta: episodeMetaParquetColumns(episodeMeta, entry),
  };
}

/**
 * Merge session-level episode meta (later manifest wins on non-empty fields).
 *
 * @param {EpisodeMeta | null | undefined} prev
 * @param {EpisodeMeta | null | undefined} next
 */
export function mergeEpisodeMeta(prev, next) {
  if (!prev) return next || null;
  if (!next) return prev;
  const extended = {
    ...parseExtendedInfo(prev.extended_info),
    ...parseExtendedInfo(next.extended_info),
  };
  return {
    station_id: next.station_id !== "unknown" ? next.station_id : prev.station_id,
    embodiment: next.embodiment || prev.embodiment,
    task_id: next.task_id || prev.task_id,
    annotation_status: prev.annotation_status === "raw" ? next.annotation_status : prev.annotation_status,
    pipeline_version: next.pipeline_version || prev.pipeline_version,
    modalities: { ...prev.modalities, ...next.modalities },
    extended_info: serializeExtendedInfo(extended),
    quality_valid_hand_ratio: next.quality_valid_hand_ratio ?? prev.quality_valid_hand_ratio ?? null,
    quality_mean_jitter: next.quality_mean_jitter ?? prev.quality_mean_jitter ?? null,
  };
}

/**
 * Bootstrap dataset schema on new session (info.json features + live episode meta).
 *
 * @param {Record<string, unknown>} info
 * @param {EpisodeMeta | null | undefined} episodeMeta
 */
export function bootstrapDatasetSchema(info, episodeMeta) {
  return mergeCanonicalFeatures(info, episodeMeta);
}

/**
 * Prepare segment ingest: parse manifest + build episode meta.
 *
 * @param {Record<string, unknown>} manifest
 * @param {string} stationId
 */
export function prepareSegmentEpisodeMeta(manifest, stationId) {
  return parseManifestToEpisodeMeta(manifest, stationId);
}

/**
 * @param {string} root dataset root (for documentation; skeleton written by parquet sync)
 */
export function annotationsParquetRelPath() {
  return "meta/annotations.parquet";
}
