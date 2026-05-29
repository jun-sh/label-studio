import i18n from "i18next";

const NS = "common";

/** Stable key suffix for i18n paths (avoids dots/colons in nested keys). */
export function hotkeyElementSlug(element: string): string {
  return element.replace(/[^a-zA-Z0-9]+/g, "_").replace(/^_|_$/g, "");
}

export function translateHotkeyLabel(element: string, fallback: string): string {
  const slug = hotkeyElementSlug(element);
  return i18n.t(`hotkeys.items.${slug}.label`, { ns: NS, defaultValue: fallback });
}

export function translateHotkeyDescription(element: string, fallback: string | undefined): string | undefined {
  if (fallback === undefined) return undefined;
  const slug = hotkeyElementSlug(element);
  return i18n.t(`hotkeys.items.${slug}.description`, { ns: NS, defaultValue: fallback });
}

export function translateHotkeySectionTitle(sectionId: string, fallback: string): string {
  return i18n.t(`hotkeys.sections.${sectionId}.title`, { ns: NS, defaultValue: fallback });
}

export function translateHotkeySectionDescription(sectionId: string, fallback: string | undefined): string | undefined {
  if (fallback === undefined) return undefined;
  return i18n.t(`hotkeys.sections.${sectionId}.description`, { ns: NS, defaultValue: fallback });
}

export function translateHotkeysModalTitle(): string {
  return i18n.t("hotkeys.modal.title", { ns: NS, defaultValue: "Keyboard Shortcuts" });
}
