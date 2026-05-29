import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useUpdatePageTitle } from "@humansignal/core";

import { attachDataVizLayoutListeners, LAYER_ID, mountLayer } from "./dataVizLayer";
import { buildLerobotEmbedSrc } from "./lerobotEmbedSrc";

import "./DataVizPage.scss";

/**
 * /data visualizer: fixed overlay (#datalab-viz-layer) aligned to the main content area.
 * Django template data_viz.html runs the same logic on full page load.
 */
export const DataVizPage = () => {
  const { t, i18n } = useTranslation("common");

  useUpdatePageTitle(t("dataViz.page_title"));

  useEffect(() => {
    const lang = i18n.language?.toLowerCase().startsWith("zh") ? "zh" : "en";
    const src = buildLerobotEmbedSrc(lang);
    const title = t("dataViz.page_title");

    document.body.dataset.datalabDataPage = "1";
    mountLayer(src, title);
    const detachLayout = attachDataVizLayoutListeners();

    return () => {
      delete document.body.dataset.datalabDataPage;
      detachLayout();
      document.getElementById(LAYER_ID)?.remove();
    };
  }, [i18n.language, t]);

  return null;
};

DataVizPage.path = "/data";
DataVizPage.exact = true;
DataVizPage.i18nTitleKey = "dataViz.page_title";
