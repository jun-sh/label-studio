import type { FC } from "react";

export type PageProps = {
  children: React.ReactNode;
};

export type PageComponent = FC<PageProps>;

export type PageContext = FC<PageProps>;

export type PageSettings = {
  path: string;
  title?: string | ((options: any) => string);
  /** When set, breadcrumbs use i18n `t(i18nTitleKey)` instead of `title`. */
  i18nTitleKey?: string;
  titleRaw?: string;
  exact?: boolean;
  context?: PageContext;
} & (
  | {
      component?: PageComponent;
      pages?: Page[];
    }
  | {
      layout?: PageLayout;
      routes?: any[];
    }
);

export type PageLayoutSettingt = Omit<PageSettings, "path">;

export type PageLayout = PageLayoutSettingt | (PageComponent & PageLayoutSettingt);

export type Page = PageSettings | (PageComponent & PageSettings);
