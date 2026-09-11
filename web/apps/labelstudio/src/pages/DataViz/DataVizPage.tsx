import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useHistory } from "react-router-dom";
import { useUpdatePageTitle } from "@humansignal/core";

import { useFixedLocation, useParams } from "../../providers/RoutesProvider";
import { attachDataVizLayoutListeners, FRAME_ID, LAYER_ID, mountLayer } from "./dataVizLayer";
import {
  buildDataVizPath,
  iframeShowsDataset,
  parseDataVizPath,
  readDatasetQueryParam,
  resolveDatasetSampleUrl,
  resolveDatasetSlug,
} from "./dataVizRoute";
import { attachDataVizUrlSync } from "./dataVizUrlSync";
import { buildLerobotEmbedSrc } from "./lerobotEmbedSrc";

import "./DataVizPage.scss";

/**
 * /data visualizer: fixed overlay (#datalab-viz-layer) aligned to the main content area.
 * Django template data_viz.html runs the same logic on full page load.
 */
export const DataVizPage = () => {
  const { t, i18n } = useTranslation("common");
  const history = useHistory();
  const location = useFixedLocation();
  const params = useParams();
  const [resolvedDatasetId, setResolvedDatasetId] = useState<string | null>(null);
  const [resolvedDatasetUrl, setResolvedDatasetUrl] = useState<string | null>(null);

  const rawDatasetSlug = useMemo(() => {
    if (params.datasetId) return decodeURIComponent(String(params.datasetId));
    const fromPath = parseDataVizPath(location.pathname).datasetId;
    if (fromPath) return fromPath;
    return readDatasetQueryParam(location.search);
  }, [params.datasetId, location.pathname, location.search]);

  useUpdatePageTitle(t("dataViz.page_title"));

  useEffect(() => {
    const queryDataset = readDatasetQueryParam(location.search);
    if (!queryDataset || params.datasetId) return;
    history.replace(buildDataVizPath(queryDataset));
  }, [history, location.search, params.datasetId]);

  useEffect(() => {
    let cancelled = false;

    if (!rawDatasetSlug) {
      setResolvedDatasetId(null);
      setResolvedDatasetUrl(null);
      return;
    }

    resolveDatasetSlug(rawDatasetSlug).then((id) => {
      if (cancelled) return;
      setResolvedDatasetId(id);
      if (id && id !== rawDatasetSlug) {
        history.replace(buildDataVizPath(id));
      }
    });

    return () => {
      cancelled = true;
    };
  }, [history, rawDatasetSlug]);

  useEffect(() => {
    let cancelled = false;

    if (!resolvedDatasetId) {
      setResolvedDatasetUrl(null);
      return;
    }

    resolveDatasetSampleUrl(resolvedDatasetId).then((url) => {
      if (cancelled) return;
      setResolvedDatasetUrl(url);
    });

    return () => {
      cancelled = true;
    };
  }, [resolvedDatasetId]);

  useEffect(() => {
    document.body.dataset.datalabDataPage = "1";
    const detachLayout = attachDataVizLayoutListeners();
    const detachUrlSync = attachDataVizUrlSync(history, FRAME_ID);

    return () => {
      delete document.body.dataset.datalabDataPage;
      detachLayout();
      detachUrlSync();
      document.getElementById(LAYER_ID)?.remove();
    };
  }, [history]);

  useEffect(() => {
    const lang = i18n.language?.toLowerCase().startsWith("zh") ? "zh" : "en";
    const datasetUrl = resolvedDatasetUrl;
    const src = buildLerobotEmbedSrc(lang, datasetUrl);
    const title = t("dataViz.page_title");
    const frame = document.getElementById(FRAME_ID) as HTMLIFrameElement | null;

    if (!frame || !iframeShowsDataset(frame, resolvedDatasetId, lang, datasetUrl)) {
      mountLayer(src, title);
    }
  }, [i18n.language, resolvedDatasetId, resolvedDatasetUrl, t]);

  return null;
};

DataVizPage.path = "/data";
DataVizPage.exact = true;
DataVizPage.i18nTitleKey = "dataViz.page_title";
DataVizPage.routes = () => [
  {
    path: "/:datasetId",
    exact: true,
    component: DataVizPage,
  },
];
