import { type ChangeEventHandler, useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ToastType, useToast } from "@humansignal/ui";
import {
  DEFAULT_UI_LOCALE,
  normalizeUiLocale,
  type SupportedUiLocale,
  SUPPORTED_UI_LOCALES,
} from "@humansignal/core";
import { useAuth } from "@humansignal/core/providers/AuthProvider";
import type { WrappedResponse } from "@humansignal/core/lib/api-proxy/types";
import type { APIUser } from "@humansignal/core/types/user";
import styles from "../AccountSettings.module.scss";

export const LanguagePreferences = () => {
  const { t, i18n } = useTranslation("common");
  const toast = useToast();
  const { user, update, refetch } = useAuth();
  const [pending, setPending] = useState(false);

  const currentLocale = useMemo(
    () => normalizeUiLocale(user?.ui_locale ?? DEFAULT_UI_LOCALE),
    [user?.ui_locale],
  );

  const onChange: ChangeEventHandler<HTMLSelectElement> = useCallback(
    async (e) => {
      const next = normalizeUiLocale(e.target.value) as SupportedUiLocale;
      const prev = normalizeUiLocale(user?.ui_locale ?? DEFAULT_UI_LOCALE);

      if (!user || next === prev) return;

      setPending(true);
      await i18n.changeLanguage(next);

      const response = (await update({ ui_locale: next })) as WrappedResponse<APIUser> | undefined;
      if (!response?.$meta?.ok) {
        await i18n.changeLanguage(prev);
        toast?.show({ message: t("account.language.update_failed"), type: ToastType.error });
        setPending(false);
        return;
      }

      if (typeof window !== "undefined" && window.APP_SETTINGS) {
        window.APP_SETTINGS.locale = next;
        if (window.APP_SETTINGS.user) {
          window.APP_SETTINGS.user.ui_locale = next;
        }
      }

      await refetch();
      setPending(false);
    },
    [i18n, refetch, t, toast, update, user],
  );

  return (
    <div className={styles.sectionContent} id="language-preferences">
      <p className="text-neutral-content-subtler text-body-medium">{t("account.language.description")}</p>
      <div className="flex flex-col gap-tight max-w-md">
        <label className="text-body-medium font-medium" htmlFor="ui-locale-select">
          {t("account.language.field_label")}
        </label>
        <select
          id="ui-locale-select"
          className="border rounded px-3 py-2 bg-neutral-background text-neutral-content border-neutral-border"
          value={currentLocale}
          onChange={onChange}
          disabled={pending || !user}
        >
          {SUPPORTED_UI_LOCALES.map((loc) => (
            <option key={loc} value={loc}>
              {loc === "en" ? t("account.language.option_en") : t("account.language.option_zh_hans")}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
};
