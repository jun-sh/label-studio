import { buttonVariant, Space } from "@humansignal/ui";
import { useUpdatePageTitle } from "@humansignal/core";
import { useTranslation } from "react-i18next";
import { cn } from "apps/labelstudio/src/utils/bem";
import { Link } from "react-router-dom";
import type { Page } from "../../types/Page";
import { EmptyList } from "./@components/EmptyList";

export const ModelsPage: Page = () => {
  const { t } = useTranslation("common");
  useUpdatePageTitle(t("breadcrumbs.models"));

  return (
    <div className={cn("prompter").toClassName()}>
      <EmptyList />
    </div>
  );
};

ModelsPage.title = () => "Models";
ModelsPage.i18nTitleKey = "breadcrumbs.models";
ModelsPage.titleRaw = "Models";
ModelsPage.path = "/models";

ModelsPage.context = () => {
  return (
    <Space size="small">
      <Link to="/prompt/settings" className={buttonVariant({ size: "small" })}>
        Create Model
      </Link>
    </Space>
  );
};
