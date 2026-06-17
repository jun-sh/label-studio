import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Redirect } from "react-router-dom";
import { useUpdatePageTitle } from "@humansignal/core";
import { useProject } from "../../providers/ProjectProvider";
import { useParams } from "../../providers/RoutesProvider";
import { attachEmbedLayoutListeners, mountEmbedLayer } from "../DataViz/datalabEmbedLayer";
import { buildEmbodiedAnnotateEmbedSrc, isEmbodiedAnnotateProject } from "./embodiedAnnotate";

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
  const { t, i18n } = useTranslation("common");
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
  }, [t, i18n.language]);

  if (!projectId) return null;

  if (project?.id && !isEmbodiedAnnotateProject(project)) {
    return <Redirect to={`/projects/${projectId}/data`} />;
  }

  return null;
};

EmbodiedAnnotatePage.path = "/embodied";
EmbodiedAnnotatePage.exact = true;
EmbodiedAnnotatePage.i18nTitleKey = "embodiedAnnotate.page_title";
