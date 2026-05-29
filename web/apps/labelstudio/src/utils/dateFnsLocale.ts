import type { Locale } from "date-fns";
import enUS from "date-fns/locale/en-US";
import zhCN from "date-fns/locale/zh-CN";

/** Locale bundle for date-fns format(), aligned with UI locale from i18next. */
export function getDateFnsLocale(language: string): Locale {
  return language === "zh-Hans" ? zhCN : enUS;
}
