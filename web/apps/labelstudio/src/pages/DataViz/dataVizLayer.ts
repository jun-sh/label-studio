import type { DatalabEmbedConfig } from "./datalabEmbedLayer";
import { attachEmbedLayoutListeners, mountEmbedLayer, syncBounds } from "./datalabEmbedLayer";

export const DATA_VIZ_EMBED: DatalabEmbedConfig = {
  layerId: "datalab-viz-layer",
  frameId: "datalab-viz-frame",
  bodyDataset: "datalabDataPage",
};

export const LAYER_ID = DATA_VIZ_EMBED.layerId;
export const FRAME_ID = DATA_VIZ_EMBED.frameId;

export function mountLayer(src: string, title: string) {
  mountEmbedLayer(DATA_VIZ_EMBED, src, title);
}

export function attachDataVizLayoutListeners() {
  return attachEmbedLayoutListeners(DATA_VIZ_EMBED);
}

export { syncBounds };
