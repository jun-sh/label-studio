import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { PersonalInfo } from "./PersonalInfo";
import { MembershipInfo } from "./MembershipInfo";
import type React from "react";
import { PersonalJWTToken } from "./PersonalJWTToken";
import { PersonalAccessTokenDescription } from "./PersonalAccessToken";
import type { AuthTokenSettings } from "../types";
import { ABILITY, type AuthPermissions } from "@humansignal/core/providers/AuthProvider";
import { ff } from "@humansignal/core";
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
      ].filter(Boolean) as SectionType[]
    );
  }, [canCreateTokens, settings, t]);
};
