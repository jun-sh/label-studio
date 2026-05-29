import type { TFunction } from "i18next";

export type DataLabLinks = {
  documentation: string;
  apiDocs: string;
  changelog: string;
};

/**
 * URLs injected via Django APP_SETTINGS.data_lab_links (from DATA_LAB_* env vars).
 * Empty string means the corresponding UI entry is omitted.
 */
export function getDataLabLinks(): DataLabLinks {
  const raw = window.APP_SETTINGS?.data_lab_links as
    | {
        documentation?: string;
        api_docs?: string;
        changelog?: string;
      }
    | undefined;

  return {
    documentation: (raw?.documentation ?? "").trim(),
    apiDocs: (raw?.api_docs ?? "").trim(),
    changelog: (raw?.changelog ?? "").trim(),
  };
}

export function buildHomeResourceLinks(t: TFunction<"common">): { title: string; url: string }[] {
  const l = getDataLabLinks();
  const items: { title: string; url: string }[] = [];
  if (l.documentation) items.push({ title: t("home.resource_documentation"), url: l.documentation });
  if (l.apiDocs) items.push({ title: t("home.resource_api"), url: l.apiDocs });
  if (l.changelog) items.push({ title: t("home.resource_release_notes"), url: l.changelog });
  return items;
}
