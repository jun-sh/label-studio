import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { PersonalInfo } from "./PersonalInfo";
import { EmailPreferences } from "./EmailPreferences";
import { PersonalAccessToken, PersonalAccessTokenDescription } from "./PersonalAccessToken";
import { MembershipInfo } from "./MembershipInfo";
import { HotkeysManager } from "./Hotkeys";
import type React from "react";
import { PersonalJWTToken } from "./PersonalJWTToken";
import type { AuthTokenSettings } from "../types";
import { ABILITY, type AuthPermissions } from "@humansignal/core/providers/AuthProvider";
import { ff } from "@humansignal/core";
import { Badge } from "@humansignal/ui";
import { LanguagePreferences } from "./LanguagePreferences";

export type SectionType = {
  title: string | React.ReactNode;
  id: string;
  component: React.FC;
  description?: React.FC;
};

export const useAccountSettingsSections = (
  settings: AuthTokenSettings | null,
  permissions: AuthPermissions,
): SectionType[] => {
  const { t } = useTranslation("common");
  const canCreateTokens = permissions.can(ABILITY.can_create_tokens);

  return useMemo(() => {
    if (!settings) {
      return [];
    }

    return (
      [
        {
          title: t("account.section.personal_info"),
          id: "personal-info",
          component: PersonalInfo,
        },
        {
          title: t("account.section.language"),
          id: "language",
          component: LanguagePreferences,
        },
        {
          title: (
            <div className="flex items-center gap-tight">
              <span>{t("account.section.hotkeys")}</span>
              <Badge variant="beta" style="solid" shape="rounded">
                Beta
              </Badge>
            </div>
          ),
          id: "hotkeys",
          component: HotkeysManager,
          description: () => t("account.hotkeys_description"),
        },
        {
          title: t("account.section.email_preferences"),
          id: "email-preferences",
          component: EmailPreferences,
        },
        {
          title: t("account.section.membership_info"),
          id: "membership-info",
          component: MembershipInfo,
        },
        settings.api_tokens_enabled &&
          canCreateTokens &&
          ff.isActive(ff.FF_AUTH_TOKENS) && {
            title: t("account.section.personal_access_token"),
            id: "personal-access-token",
            component: PersonalJWTToken,
            description: PersonalAccessTokenDescription,
          },
        settings.legacy_api_tokens_enabled &&
          canCreateTokens && {
            title: ff.isActive(ff.FF_AUTH_TOKENS) ? t("account.section.legacy_token") : t("account.section.access_token"),
            id: "legacy-token",
            component: PersonalAccessToken,
            description: PersonalAccessTokenDescription,
          },
      ].filter(Boolean) as SectionType[]
    );
  }, [canCreateTokens, settings, t]);
};
