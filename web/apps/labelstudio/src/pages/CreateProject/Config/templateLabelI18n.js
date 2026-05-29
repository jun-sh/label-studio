/**
 * Maps API template group names and recipe titles (English from YAML) to i18n keys
 * under common.labeling_templates.groups.* and common.labeling_templates.titles.*
 */
export function slugifyTemplateLabel(s) {
  if (!s) return "";
  const trimmed = String(s)
    .trim()
    .replace(/^["']|["']$/g, "");
  return trimmed
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

/** @param {import("i18next").TFunction} t */
export function translateTemplateGroup(name, t) {
  if (!name) return "";
  const slug = slugifyTemplateLabel(name);
  return t(`labeling_templates.groups.${slug}`, { defaultValue: name });
}

/** @param {import("i18next").TFunction} t */
export function translateTemplateTitle(title, t) {
  if (!title) return "";
  const slug = slugifyTemplateLabel(title);
  return t(`labeling_templates.titles.${slug}`, { defaultValue: title });
}
