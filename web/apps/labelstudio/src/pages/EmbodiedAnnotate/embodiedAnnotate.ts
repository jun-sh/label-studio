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

/** User-visible description for embodied projects (hides system marker / legacy path lines). */
export function stripEmbodiedMarkerFromDescription(description: string | null | undefined): string {
  if (!description) return "";
  const pathFromMarker = parseEmbodiedDatasetPath(description);
  return description
    .split(/\r?\n/)
    .filter((line) => {
      const trimmed = line.trim();
      if (trimmed.includes(EMBODIED_ANNOTATE_TAG)) return false;
      if (pathFromMarker && trimmed === pathFromMarker) return false;
      return true;
    })
    .join("\n")
    .trim();
}

/** Re-attach system marker before persisting embodied project description. */
export function ensureEmbodiedMarkerInDescription(userDescription?: string | null): string {
  return buildEmbodiedProjectDescription(userDescription);
}

const EMBED_QUERY_KEYS = ["collection", "package", "episode", "episodeIndex"] as const;

/** Params forwarded from the Label Studio URL into the annotate iframe. */
export function readEmbodiedEmbedQueryParams(search?: string): URLSearchParams {
  const raw = search ?? (typeof window !== "undefined" ? window.location.search : "");
  return new URLSearchParams(raw);
}

export function buildEmbodiedAnnotateEmbedSrc(search?: string): string {
  const params = new URLSearchParams({ datalab_embed: "1" });
  const parentParams = readEmbodiedEmbedQueryParams(search);

  EMBED_QUERY_KEYS.forEach((key) => {
    const value = parentParams.get(key);
    if (value) params.set(key, value);
  });

  if (typeof window !== "undefined") {
    const raw =
      window.i18n?.language ??
      window.APP_SETTINGS?.user?.ui_locale ??
      window.APP_SETTINGS?.locale ??
      localStorage.getItem("ui_locale") ??
      "en";
    params.set("lang", String(raw).toLowerCase().startsWith("zh") ? "zh-Hans" : "en");
  }

  return `/lerobot-annotate/?${params.toString()}`;
}

/** Settings sidebar paths hidden for embodied-annotate projects (Labeling Interface, Annotation). */
const EMBODIED_HIDDEN_SETTINGS_PATHS = new Set(["/labeling", "/annotation"]);

export function isEmbodiedSettingsMenuItemHidden(settingsPage: { path?: string }): boolean {
  return settingsPage.path != null && EMBODIED_HIDDEN_SETTINGS_PATHS.has(settingsPage.path);
}

export function getProjectSettingsMenuItems<T extends { path?: string }>(
  allItems: T[],
  project: { description?: string | null } | null | undefined,
): T[] {
  if (!isEmbodiedAnnotateProject(project)) return allItems;
  return allItems.filter((item) => !isEmbodiedSettingsMenuItemHidden(item));
}
