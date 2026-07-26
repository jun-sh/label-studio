import { parseEmbodiedDatasetPath } from "../EmbodiedAnnotate/embodiedAnnotate";

/** Marker in project.description for LeRobot QC terminal inspection projects */
export const LEROBOT_QC_TAG = "[lerobot-qc]";

/** Optional dataset path after marker: `[lerobot-qc] /path/to/dataset` */
export const LEROBOT_QC_PATH_MARKER = /\[lerobot-qc\]\s*(\S+)/i;

export function parseLerobotQcDatasetPath(description: string | null | undefined): string | null {
  if (!description) return null;
  const match = description.match(LEROBOT_QC_PATH_MARKER);
  return match?.[1] ?? parseEmbodiedDatasetPath(description);
}

export function isLerobotQcProject(project: { description?: string | null } | null | undefined): boolean {
  const description = project?.description ?? "";
  return description.includes(LEROBOT_QC_TAG);
}

/** QC route is also available for embodied-annotate pipeline projects. */
export function canAccessLerobotQc(project: { description?: string | null } | null | undefined): boolean {
  const description = project?.description ?? "";
  return description.includes(LEROBOT_QC_TAG) || description.includes("[embodied-annotate]");
}

export function buildLerobotQcProjectDescription(userDescription?: string | null): string {
  const text = (userDescription ?? "").trim();
  if (text.includes(LEROBOT_QC_TAG)) return text;
  return text ? `${LEROBOT_QC_TAG}\n${text}` : LEROBOT_QC_TAG;
}

export function stripLerobotQcMarkerFromDescription(description: string | null | undefined): string {
  if (!description) return "";
  const pathFromMarker = parseLerobotQcDatasetPath(description);
  return description
    .split(/\r?\n/)
    .filter((line) => {
      const trimmed = line.trim();
      if (trimmed.includes(LEROBOT_QC_TAG)) return false;
      if (pathFromMarker && trimmed === pathFromMarker) return false;
      return true;
    })
    .join("\n")
    .trim();
}

const EMBED_QUERY_KEYS = ["datasetPath", "local_path", "collection", "package", "episode", "episodeIndex"] as const;

export function readLerobotQcEmbedQueryParams(search?: string): URLSearchParams {
  const raw = search ?? (typeof window !== "undefined" ? window.location.search : "");
  return new URLSearchParams(raw);
}

export function buildLerobotQcEmbedSrc(search?: string, projectDescription?: string | null): string {
  const params = new URLSearchParams({ datalab_embed: "1" });
  const parentParams = readLerobotQcEmbedQueryParams(search);

  EMBED_QUERY_KEYS.forEach((key) => {
    const value = parentParams.get(key);
    if (value) params.set(key, value);
  });

  const datasetPath = parseLerobotQcDatasetPath(projectDescription ?? "");
  if (datasetPath && !params.has("datasetPath")) {
    params.set("datasetPath", datasetPath);
  }

  if (typeof window !== "undefined") {
    const raw =
      window.i18n?.language ??
      window.APP_SETTINGS?.user?.ui_locale ??
      window.APP_SETTINGS?.locale ??
      localStorage.getItem("ui_locale") ??
      "en";
    params.set("lang", String(raw).toLowerCase().startsWith("zh") ? "zh-Hans" : "en");
  }

  return `/qc/?${params.toString()}`;
}
