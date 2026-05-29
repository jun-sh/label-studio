import { inject } from "mobx-react";
import { useTranslation } from "react-i18next";
import { IconChevronLeft } from "@humansignal/icons";
import { cn } from "../../../utils/bem";
import { Button } from "@humansignal/ui";
import { Filters } from "../Filters";
import "./FilterSidebar.scss";

const sidebarInjector = inject(({ store }) => {
  const viewsStore = store.viewsStore;

  return {
    viewsStore,
    sidebarEnabled: viewsStore?.sidebarEnabled,
    sidebarVisible: viewsStore?.sidebarVisible,
  };
});

export const FiltersSidebar = sidebarInjector(({ viewsStore, sidebarEnabled, sidebarVisible }) => {
  const { t } = useTranslation("common");
  return sidebarEnabled && sidebarVisible ? (
    <div className={cn("filters-sidebar").toClassName()}>
      <div className={cn("filters-sidebar").elem("header").toClassName()}>
        <div className={cn("filters-sidebar").elem("extra").toClassName()}>
          <Button
            look="string"
            onClick={() => viewsStore.collapseFilters()}
            tooltip={t("dm.filters.unpin_tooltip")}
            aria-label={t("dm.filters.unpin_aria")}
          >
            <IconChevronLeft width={24} height={24} />
          </Button>
          <div className={cn("filters-sidebar").elem("title").toClassName()}>{t("dm.filters.panel_heading")}</div>
        </div>
      </div>
      <Filters sidebar={true} />
    </div>
  ) : null;
});
FiltersSidebar.displayName = "FiltersSidebar";
