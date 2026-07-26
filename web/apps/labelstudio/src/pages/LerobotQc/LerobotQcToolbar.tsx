import { useTranslation } from "react-i18next";
import { NavLink } from "react-router-dom";
import { Space } from "@humansignal/ui";
import { buttonVariant } from "@humansignal/ui";
import { useProject } from "../../providers/ProjectProvider";
import { useParams } from "../../providers/RoutesProvider";
import { canAccessLerobotQc } from "./lerobotQc";

export const LerobotQcToolbarButton = () => {
  const { t } = useTranslation("common");
  const { project } = useProject();
  const params = useParams();
  const projectId = project?.id ?? params?.id;

  if (!projectId || !canAccessLerobotQc(project)) return null;

  return (
    <NavLink
      className={buttonVariant({ size: "small", look: "outlined" })}
      to={`/projects/${projectId}/lerobot-qc`}
    >
      {t("lerobotQc.toolbar_button")}
    </NavLink>
  );
};

export const PipelineToolbarLinks = () => {
  const { t } = useTranslation("common");
  const { project } = useProject();
  const params = useParams();
  const projectId = project?.id ?? params?.id;

  if (!projectId || !canAccessLerobotQc(project)) return null;

  return (
    <Space size="small">
      <NavLink
        className={buttonVariant({ size: "small", look: "outlined" })}
        to={`/projects/${projectId}/embodied`}
      >
        {t("embodiedAnnotate.toolbar_button")}
      </NavLink>
      <NavLink
        className={buttonVariant({ size: "small", look: "outlined" })}
        to={`/projects/${projectId}/lerobot-qc`}
      >
        {t("lerobotQc.toolbar_button")}
      </NavLink>
    </Space>
  );
};
