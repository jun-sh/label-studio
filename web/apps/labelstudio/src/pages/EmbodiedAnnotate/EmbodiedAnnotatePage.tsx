import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Redirect, useLocation } from "react-router-dom";
import { useUpdatePageTitle } from "@humansignal/core";
import { buttonVariant } from "@humansignal/ui";
import { Space } from "../../components/Space/Space";
import { useProject } from "../../providers/ProjectProvider";
import { useParams } from "../../providers/RoutesProvider";
import { attachEmbedLayoutListeners, mountEmbedLayer } from "../DataViz/datalabEmbedLayer";
import { buildEmbodiedAnnotateEmbedSrc, isEmbodiedAnnotateProject } from "./embodiedAnnotate";
import { EmbodiedAnnotateToolbarButton } from "./EmbodiedAnnotateToolbar";

import "./EmbodiedAnnotatePage.scss";

const EMBED_CONFIG = {
  layerId: "datalab-embodied-layer",
  frameId: "datalab-embodied-frame",
  bodyDataset: "datalabEmbodiedPage",
} as const;

/**
 * /projects/:id/embodied — fullscreen iframe for lerobot-annotate (via /lerobot-annotate/ proxy).
 */
export const EmbodiedAnnotatePage = () => {
  const { t } = useTranslation("common");
  const { project } = useProject();
  const params = useParams();
  const projectId = project?.id ?? params?.id;

  useUpdatePageTitle(t("embodiedAnnotate.page_title"));

  useEffect(() => {
    document.body.dataset[EMBED_CONFIG.bodyDataset] = "1";
    const detachLayout = attachEmbedLayoutListeners(EMBED_CONFIG);

    return () => {
      delete document.body.dataset[EMBED_CONFIG.bodyDataset];
      detachLayout();
      document.getElementById(EMBED_CONFIG.layerId)?.remove();
    };
  }, []);

  useEffect(() => {
    mountEmbedLayer(EMBED_CONFIG, buildEmbodiedAnnotateEmbedSrc(), t("embodiedAnnotate.iframe_title"));
  }, [t]);

  if (!projectId) return null;

  if (project?.id && !isEmbodiedAnnotateProject(project)) {
    return <Redirect to={`/projects/${projectId}/data`} />;
  }

  return null;
};

EmbodiedAnnotatePage.path = "/embodied";
EmbodiedAnnotatePage.exact = true;
EmbodiedAnnotatePage.i18nTitleKey = "embodiedAnnotate.page_title";

EmbodiedAnnotatePage.context = () => {
  const { t } = useTranslation("common");
  const { project } = useProject();
  const params = useParams();
  const location = useLocation();
  const projectId = project?.id ?? params?.id;
  const onEmbodiedRoute = /\/embodied\/?$/.test(location.pathname);

  if (!projectId) return null;

  return (
    <Space size="small">
      <NavLink
        className={buttonVariant({ size: "small", look: "outlined" })}
        to={`/projects/${projectId}/data`}
      >
        {t("embodiedAnnotate.back_to_data")}
      </NavLink>
      {!onEmbodiedRoute && <EmbodiedAnnotateToolbarButton />}
    </Space>
  );
};
