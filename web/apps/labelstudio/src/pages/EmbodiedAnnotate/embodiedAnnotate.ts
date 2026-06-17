/** Marker line in project.description for embodied (lerobot-annotate) projects */
export const EMBODIED_ANNOTATE_TAG = "[embodied-annotate]";

/** Optional dataset path after the marker: `[embodied-annotate] /path/to/dataset` */
export const EMBODIED_ANNOTATE_PATH_MARKER = /\[embodied-annotate\]\s*(\S+)/i;

/** @deprecated use EMBODIED_ANNOTATE_PATH_MARKER */
export const EMBODIED_ANNOTATE_MARKER = EMBODIED_ANNOTATE_PATH_MARKER;

export function parseEmbodiedDatasetPath(description: string | null | undefined): string | null {
  if (!description) return null;
  const match = description.match(EMBODIED_ANNOTATE_PATH_MARKER);
  return match?.[1] ?? null;
}

export function isEmbodiedAnnotateProject(project: { description?: string | null } | null | undefined): boolean {
  const description = project?.description ?? "";
  return description.includes(EMBODIED_ANNOTATE_TAG);
}

/** Build description for a new embodied project (marker only; path optional via annotate Connect UI). */
export function buildEmbodiedProjectDescription(userDescription?: string | null): string {
  const text = (userDescription ?? "").trim();
  if (text.includes(EMBODIED_ANNOTATE_TAG)) return text;
  return text ? `${EMBODIED_ANNOTATE_TAG}\n${text}` : EMBODIED_ANNOTATE_TAG;
}

export function buildEmbodiedAnnotateEmbedSrc(): string {
  return "/lerobot-annotate/?datalab_embed=1";
}
