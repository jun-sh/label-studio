/**
 * Shared UI locale identifiers for server + web (keep in sync with users.serializers.ALLOWED_UI_LOCALES).
 */
export const DEFAULT_UI_LOCALE = "en";

export const UI_LOCALE_STORAGE_KEY = "ui_locale";

export const SUPPORTED_UI_LOCALES = ["en", "zh-Hans"] as const;

export type SupportedUiLocale = (typeof SUPPORTED_UI_LOCALES)[number];

const ALIAS_MAP: Record<string, SupportedUiLocale> = {
  zh: "zh-Hans",
  "zh-CN": "zh-Hans",
  zh_CN: "zh-Hans",
  "zh-Hans": "zh-Hans",
  en: "en",
};

export function normalizeUiLocale(raw: string | null | undefined): SupportedUiLocale {
  if (raw == null || raw === "") {
    return DEFAULT_UI_LOCALE;
  }
  const mapped = ALIAS_MAP[raw];
  if (mapped) {
    return mapped;
  }
  if ((SUPPORTED_UI_LOCALES as readonly string[]).includes(raw)) {
    return raw as SupportedUiLocale;
  }
  return DEFAULT_UI_LOCALE;
}
