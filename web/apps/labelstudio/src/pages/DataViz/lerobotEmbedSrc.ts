/**
 * Build LeRobot Studio iframe URL (Data Lab embed + optional dataset deep-link).
 * Matches io-ai.tech style: /lerobot/?url=sample://sensexperience_ego
 */
export function resolveDatasetUrlParam(datasetUrl: string): string {
  if (datasetUrl.startsWith("stream://")) {
    const stationId = datasetUrl.slice("stream://".length).replace(/\/$/, "");
    return `/lerobot/api/stream/${encodeURIComponent(stationId)}/`;
  }
  return datasetUrl;
}

export function buildLerobotEmbedSrc(lang: string, datasetUrl?: string | null): string {
  const params = new URLSearchParams({
    datalab_embed: "1",
    lang,
  });
  if (datasetUrl) {
    params.set("url", resolveDatasetUrlParam(datasetUrl));
    if (datasetUrl.startsWith("stream://")) {
      params.set("datalab_collection", "1");
    }
  }
  return `/lerobot/?${params.toString()}`;
}
