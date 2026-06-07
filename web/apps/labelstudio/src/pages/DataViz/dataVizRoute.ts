const DATA_VIZ_BASE = "/data";
const DATASET_SLUG_RE = /^[a-zA-Z0-9_-]+$/;

export const DATA_VIZ_DATASET_MSG = "datalab:data-viz:dataset";
export const DATA_VIZ_NAVIGATE_MSG = "datalab:data-viz:navigate";

export type DataVizDatasetMessage = {
  type: typeof DATA_VIZ_DATASET_MSG;
  datasetId: string | null;
  replace?: boolean;
};

export type DataVizNavigateMessage = {
  type: typeof DATA_VIZ_NAVIGATE_MSG;
  datasetId: string | null;
};

type DatasetRecord = { id: string; name: string };

let datasetCatalogPromise: Promise<DatasetRecord[]> | null = null;

function normalizeLookupKey(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "");
}

export function isValidDatasetSlug(slug: string): boolean {
  return DATASET_SLUG_RE.test(slug);
}

export function datasetIdToSampleUrl(datasetId: string): string {
  return `sample://${datasetId}`;
}

export function parseDatasetFromSampleUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const match = raw.match(/^sample:\/\/([^/?#]+)/i);
  return match ? decodeURIComponent(match[1]) : null;
}

export function parseDataVizPath(pathname: string): { datasetId: string | null } {
  const match = pathname.match(/^\/data\/([^/]+)\/?$/);
  if (!match) return { datasetId: null };
  const datasetId = decodeURIComponent(match[1]);
  return { datasetId: isValidDatasetSlug(datasetId) ? datasetId : null };
}

export function readDatasetQueryParam(search: string): string | null {
  const value = new URLSearchParams(search).get("dataset");
  if (!value || !isValidDatasetSlug(value)) return null;
  return value;
}

export function buildDataVizPath(datasetId: string | null): string {
  if (!datasetId) return DATA_VIZ_BASE;
  return `${DATA_VIZ_BASE}/${encodeURIComponent(datasetId)}`;
}

async function fetchDatasetCatalog(): Promise<DatasetRecord[]> {
  if (!datasetCatalogPromise) {
    datasetCatalogPromise = fetch("/lerobot/api/datasets", { credentials: "same-origin" })
      .then((res) => (res.ok ? res.json() : []))
      .then((rows) => (Array.isArray(rows) ? rows : []))
      .catch(() => []);
  }
  return datasetCatalogPromise;
}

/** Resolve path slug to canonical dataset id (matches id or display name). */
export async function resolveDatasetSlug(slug: string): Promise<string | null> {
  const decoded = decodeURIComponent(slug.trim());
  if (!decoded) return null;

  const catalog = await fetchDatasetCatalog();
  const lookup = normalizeLookupKey(decoded);
  for (const row of catalog) {
    if (row.id === decoded) return row.id;
    if (normalizeLookupKey(row.id) === lookup) return row.id;
    if (normalizeLookupKey(row.name) === lookup) return row.id;
  }

  return isValidDatasetSlug(decoded) ? decoded : null;
}

export function iframeShowsDataset(frame: HTMLIFrameElement, datasetId: string | null, lang: string): boolean {
  try {
    const url = new URL(frame.src, window.location.origin);
    if (url.searchParams.get("datalab_embed") !== "1") return false;
    if ((url.searchParams.get("lang") || "en") !== lang) return false;
    const sample = parseDatasetFromSampleUrl(url.searchParams.get("url"));
    return sample === datasetId;
  } catch {
    return false;
  }
}
