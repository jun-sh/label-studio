import type { History } from "history";

import {
  buildDataVizPath,
  DATA_VIZ_DATASET_MSG,
  DATA_VIZ_NAVIGATE_MSG,
  type DataVizDatasetMessage,
  type DataVizNavigateMessage,
} from "./dataVizRoute";

function pathsEqual(a: string, b: string): boolean {
  const norm = (path: string) => {
    if (path.length > 1 && path.endsWith("/")) return path.slice(0, -1);
    return path || "/";
  };
  return norm(a) === norm(b);
}

export function attachDataVizUrlSync(history: History, frameId: string): () => void {
  if ((window as Window & { __DATALAB_DATA_VIZ_PARENT_SYNC__?: boolean }).__DATALAB_DATA_VIZ_PARENT_SYNC__) {
    return () => {};
  }

  const onMessage = (event: MessageEvent) => {
    if (event.origin !== window.location.origin) return;
    const data = event.data as DataVizDatasetMessage | undefined;
    if (!data || data.type !== DATA_VIZ_DATASET_MSG) return;

    const nextPath = buildDataVizPath(data.datasetId);
    if (pathsEqual(window.location.pathname, nextPath)) return;

    if (data.replace) history.replace(nextPath);
    else history.push(nextPath);
  };

  const onPopState = () => {
    const frame = document.getElementById(frameId) as HTMLIFrameElement | null;
    if (!frame?.contentWindow) return;

    const match = window.location.pathname.match(/^\/data(?:\/([^/]+))?\/?$/);
    const datasetId = match?.[1] ? decodeURIComponent(match[1]) : null;

    try {
      frame.contentWindow.postMessage(
        { type: DATA_VIZ_NAVIGATE_MSG, datasetId } satisfies DataVizNavigateMessage,
        window.location.origin,
      );
    } catch {
      /* ignore */
    }
  };

  window.addEventListener("message", onMessage);
  window.addEventListener("popstate", onPopState);

  return () => {
    window.removeEventListener("message", onMessage);
    window.removeEventListener("popstate", onPopState);
  };
}
