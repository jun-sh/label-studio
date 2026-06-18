import { useTranslation } from "react-i18next";
import { SidebarMenu } from "../../components/SidebarMenu/SidebarMenu";
import { useProject } from "../../providers/ProjectProvider";
import { getProjectSettingsMenuItems } from "../EmbodiedAnnotate/embodiedAnnotate";
import { WebhookPage } from "../WebhookPage/WebhookPage";
import { DangerZone } from "./DangerZone";
import { GeneralSettings } from "./GeneralSettings";
import { AnnotationSettings } from "./AnnotationSettings";
import { LabelingSettings } from "./LabelingSettings";
import { MachineLearningSettings } from "./MachineLearningSettings/MachineLearningSettings";
import { PredictionsSettings } from "./PredictionsSettings/PredictionsSettings";
import { StorageSettings } from "./StorageSettings/StorageSettings";
import "./settings.scss";

const SETTINGS_MENU_ITEMS = [
  GeneralSettings,
  LabelingSettings,
  AnnotationSettings,
  MachineLearningSettings,
  PredictionsSettings,
  StorageSettings,
  WebhookPage,
  DangerZone,
];

export const MenuLayout = ({ children, ...routeProps }) => {
  const { t } = useTranslation("common");
  const { project } = useProject();
  const menuItems = getProjectSettingsMenuItems(SETTINGS_MENU_ITEMS, project);

  return (
    <SidebarMenu
      menuItems={menuItems}
      path={routeProps.match.url}
      t={t}
      children={children}
    />
  );
};

const pages = {
  AnnotationSettings,
  LabelingSettings,
  MachineLearningSettings,
  PredictionsSettings,
  StorageSettings,
  WebhookPage,
  DangerZone,
};

export const SettingsPage = {
  title: "Settings",
  i18nTitleKey: "breadcrumbs.settings",
  path: "/settings",
  exact: true,
  layout: MenuLayout,
  component: GeneralSettings,
  pages,
};
