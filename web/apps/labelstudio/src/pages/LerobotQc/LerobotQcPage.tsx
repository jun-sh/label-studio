import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Redirect, useLocation } from "react-router-dom";
import { useUpdatePageTitle } from "@humansignal/core";
import { useProject } from "../../providers/ProjectProvider";
import { useParams } from "../../providers/RoutesProvider";
import { attachEmbedLayoutListeners, mountEmbedLayer } from "../DataViz/datalabEmbedLayer";
import { buildLerobotQcEmbedSrc, canAccessLerobotQc } from "./lerobotQc";
import { PipelineToolbarLinks } from "./LerobotQcToolbar";

import "./LerobotQcPage.scss";

const EMBED_CONFIG = {
  layerId: "datalab-lerobot-qc-layer",
  frameId: "datalab-lerobot-qc-frame",
  bodyDataset: "datalabLerobotQcPage",
} as const;

/**
 * /projects/:id/lerobot-qc — fullscreen iframe for lerobot-qc terminal inspection.
 */
export const LerobotQcPage = () => {
  const { t, i18n } = useTranslation("common");
  const location = useLocation();
  const { project } = useProject();
  const params = useParams();
  const projectId = project?.id ?? params?.id;

  useUpdatePageTitle(t("lerobotQc.page_title"));

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
    mountEmbedLayer(
      EMBED_CONFIG,
      buildLerobotQcEmbedSrc(location.search, project?.description),
      t("lerobotQc.iframe_title"),
    );
  }, [t, i18n.language, location.search, project?.description]);

  if (!projectId) return null;

  if (project?.id && !canAccessLerobotQc(project)) {
    return <Redirect to={`/projects/${projectId}/data`} />;
  }

  return (
    <div className="lerobot-qc-hint lerobot-qc-hint--pipeline">
      <PipelineToolbarLinks />
    </div>
  );
};

LerobotQcPage.path = "/lerobot-qc";
LerobotQcPage.exact = true;
LerobotQcPage.i18nTitleKey = "lerobotQc.page_title";
