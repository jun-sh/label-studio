import { inject, observer } from "mobx-react";
import { useTranslation } from "react-i18next";
import { RadioGroup } from "../../Common/RadioGroup/RadioGroup";
import { IconGrid, IconList } from "@humansignal/icons";
import { Tooltip } from "@humansignal/ui";

const viewInjector = inject(({ store }) => ({
  view: store.currentView,
}));

export const ViewToggle = viewInjector(
  observer(({ view, size, ...rest }) => {
    const { t } = useTranslation("common");
    return (
      <RadioGroup
        size={size}
        value={view.type}
        onChange={(e) => view.setType(e.target.value)}
        {...rest}
        style={{ "--button-padding": "0 var(--spacing-tighter)" }}
      >
        <Tooltip title={t("dm.toolbar.list_view")}>
          <div>
            <RadioGroup.Button value="list" aria-label={t("dm.toolbar.switch_list")}>
              <IconList />
            </RadioGroup.Button>
          </div>
        </Tooltip>
        <Tooltip title={t("dm.toolbar.grid_view")}>
          <div>
            <RadioGroup.Button value="grid" aria-label={t("dm.toolbar.switch_grid")}>
              <IconGrid />
            </RadioGroup.Button>
          </div>
        </Tooltip>
      </RadioGroup>
    );
  }),
);

export const DataStoreToggle = viewInjector(({ view, size, ...rest }) => {
  const { t } = useTranslation("common");
  return (
    <RadioGroup value={view.target} size={size} onChange={(e) => view.setTarget(e.target.value)} {...rest}>
      <RadioGroup.Button value="tasks">{t("dm.toolbar.tasks")}</RadioGroup.Button>
      <RadioGroup.Button value="annotations" disabled>
        {t("dm.toolbar.annotations")}
      </RadioGroup.Button>
    </RadioGroup>
  );
});
