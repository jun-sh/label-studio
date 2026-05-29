import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useHistory, useLocation } from "react-router-dom";
import { useUpdatePageTitle } from "@humansignal/core";

import type { DatalabEmbedConfig } from "../DataViz/datalabEmbedLayer";
import { attachEmbedLayoutListeners, mountEmbedLayer } from "../DataViz/datalabEmbedLayer";
import { attachDataVizLayoutListeners, mountLayer as mountVizLayer } from "../DataViz/dataVizLayer";
import { buildLerobotEmbedSrc } from "../DataViz/lerobotEmbedSrc";

import "../DataViz/DataVizPage.scss";
import "./CollectionPage.scss";

const COLLECTION_EMBED: DatalabEmbedConfig = {
  layerId: "datalab-collection-layer",
  frameId: "datalab-collection-frame",
  bodyDataset: "datalabCollectionPage",
};

type CollectionStation = {
  id: string;
  name: string;
  online: boolean;
  datasetUrl?: string;
};

const DEFAULT_STATION_DATASET_URL = "sample://sensexperience_ego";

function resolveDatasetUrl(station: CollectionStation | null): string | undefined {
  if (!station?.online) return undefined;
  return station.datasetUrl || DEFAULT_STATION_DATASET_URL;
}

/**
 * /collection — layer 1: native station list iframe.
 * /collection?station=<id> — layer 2: same LeRobot viewer as /data (online stations only).
 */
export const CollectionPage = () => {
  const { t, i18n } = useTranslation("common");
  const location = useLocation();
  const history = useHistory();
  const stationId = new URLSearchParams(location.search).get("station");
  const [station, setStation] = useState<CollectionStation | null>(null);

  const lang = i18n.language?.toLowerCase().startsWith("zh") ? "zh" : "en";
  const datasetUrl = resolveDatasetUrl(station);
  const vizReady = Boolean(stationId && station?.online && datasetUrl);

  useUpdatePageTitle(
    vizReady ? t("collection.viz_page_title", { name: station?.name ?? "" }) : t("collection.page_title"),
  );

  useEffect(() => {
    if (!stationId) {
      setStation(null);
      return;
    }

    let cancelled = false;

    fetch(`/lerobot/api/collection/stations/${encodeURIComponent(stationId)}`, {
      credentials: "same-origin",
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((data: CollectionStation | null) => {
        if (cancelled) return;
        if (!data?.online) {
          history.replace(CollectionPage.path);
          return;
        }
        setStation(data);
      })
      .catch(() => {
        if (!cancelled) history.replace(CollectionPage.path);
      });

    return () => {
      cancelled = true;
    };
  }, [stationId, history]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const payload = event.data;
      if (payload?.type !== "datalab:collection:select-station") return;
      const id = payload.stationId;
      if (typeof id !== "string" || !id) return;
      if (event.origin && event.origin !== "null" && event.origin !== window.location.origin) {
        return;
      }
      history.push(`${CollectionPage.path}?station=${encodeURIComponent(id)}`);
    };

    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [history]);

  useEffect(() => {
    const removeLayers = () => {
      document.getElementById(COLLECTION_EMBED.layerId)?.remove();
      document.getElementById("datalab-viz-layer")?.remove();
      delete document.body.dataset.datalabCollectionPage;
      delete document.body.dataset.datalabDataPage;
      delete document.body.dataset.datalabCollectionViz;
    };

    const listSrc = "/_datalab/collection-embed.html?v=7";
    const mountList = () => {
      document.body.dataset.datalabCollectionPage = "1";
      delete document.body.dataset.datalabDataPage;
      delete document.body.dataset.datalabCollectionViz;
      document.getElementById("datalab-viz-layer")?.remove();
      mountEmbedLayer(COLLECTION_EMBED, listSrc, t("collection.iframe_title"));
      return attachEmbedLayoutListeners(COLLECTION_EMBED);
    };

    if (!stationId) {
      const detach = mountList();
      return () => {
        detach();
        removeLayers();
      };
    }

    if (!vizReady || !station) {
      const detach = mountList();
      return () => {
        detach();
      };
    }

    document.body.dataset.datalabCollectionPage = "1";
    document.body.dataset.datalabDataPage = "1";
    document.body.dataset.datalabCollectionViz = "1";
    document.getElementById(COLLECTION_EMBED.layerId)?.remove();

    const src = buildLerobotEmbedSrc(lang, datasetUrl);
    mountVizLayer(src, t("dataViz.iframe_title"));
    const detach = attachDataVizLayoutListeners();

    return () => {
      detach();
      removeLayers();
    };
  }, [stationId, vizReady, station, datasetUrl, lang, t]);

  return null;
};

CollectionPage.path = "/collection";
CollectionPage.exact = true;
CollectionPage.i18nTitleKey = "collection.page_title";
