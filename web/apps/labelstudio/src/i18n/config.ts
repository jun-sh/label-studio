import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import {
  DEFAULT_UI_LOCALE,
  normalizeUiLocale,
  UI_LOCALE_STORAGE_KEY,
  type SupportedUiLocale,
} from "@humansignal/core";

import enCommon from "./locales/en/common.json";
import zhHansCommon from "./locales/zh-Hans/common.json";

declare global {
  interface Window {
    APP_SETTINGS?: {
      locale?: string | null;
      user?: {
        ui_locale?: string;
      };
    };
    /** Shared i18n instance for embedded bundles (e.g. Data Manager). */
    i18n?: typeof import("i18next").default;
  }
}

function readInitialLocale(): SupportedUiLocale {
  if (typeof window === "undefined") {
    return DEFAULT_UI_LOCALE;
  }
  const rawServer =
    window.APP_SETTINGS?.locale ?? window.APP_SETTINGS?.user?.ui_locale ?? undefined;
  const rawStored = localStorage.getItem(UI_LOCALE_STORAGE_KEY);

  const normServer = normalizeUiLocale(rawServer ?? undefined);
  const normStored = rawStored ? normalizeUiLocale(rawStored) : null;

  // Logged-in HTML always embeds `locale` as a string (defaults to "en"), so the old `??` chain
  // never reached localStorage. If the embed is still default English but this device already
  // selected zh-Hans (saved by languageChanged), honor localStorage.
  if (normStored === "zh-Hans" && normServer === DEFAULT_UI_LOCALE) {
    return "zh-Hans";
  }

  return normalizeUiLocale(rawServer ?? rawStored ?? undefined);
}

void i18n.use(initReactI18next).init({
  resources: {
    en: { common: enCommon },
    "zh-Hans": { common: zhHansCommon },
  },
  lng: readInitialLocale(),
  fallbackLng: DEFAULT_UI_LOCALE,
  defaultNS: "common",
  ns: ["common"],
  interpolation: { escapeValue: false },
  react: { useSuspense: false },
});

if (typeof window !== "undefined") {
  window.i18n = i18n;
}

i18n.on("languageChanged", (lng) => {
  const normalized = normalizeUiLocale(lng);
  if (normalized !== lng) {
    void i18n.changeLanguage(normalized);
    return;
  }
  document.documentElement.lang = normalized;
  document.documentElement.setAttribute("data-ui-locale", normalized);
  try {
    localStorage.setItem(UI_LOCALE_STORAGE_KEY, normalized);
  } catch {
    /* private mode or quota */
  }
});

export default i18n;
