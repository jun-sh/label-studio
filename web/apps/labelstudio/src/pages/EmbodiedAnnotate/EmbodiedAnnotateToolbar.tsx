import { useTranslation } from "react-i18next";
import { NavLink } from "react-router-dom";
import { buttonVariant } from "@humansignal/ui";
import { useProject } from "../../providers/ProjectProvider";
import { useParams } from "../../providers/RoutesProvider";
import { isEmbodiedAnnotateProject } from "./embodiedAnnotate";

export function EmbodiedAnnotateToolbarButton() {
  const { t } = useTranslation("common");
  const { project } = useProject();
  const params = useParams();
  const projectId = project?.id ?? params?.id;

  if (!projectId || !isEmbodiedAnnotateProject(project)) return null;

  return (
      <NavLink
        className={buttonVariant({ size: "small", look: "outlined" })}
        to={`/projects/${projectId}/embodied`}
      >
      {t("embodiedAnnotate.toolbar_button")}
    </NavLink>
  );
}
