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

type DatasetRecord = { id: string; name: string; httpDatasetUrl?: string; url?: string };

/** Datasets served from HTTP folder, not bundled zip (matches data-page.js). */
const DATASET_URL_OVERRIDES: Record<string, string> = {
  "ego-001": "/lerobot/api/sample/ego_001/dataset/",
  ego_001: "/lerobot/api/sample/ego_001/dataset/",
  egodome: "/lerobot/api/sample/ego_001/dataset/",
};

let datasetCatalogPromise: Promise<DatasetRecord[]> | null = null;

function normalizeLookupKey(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "");
}

export function isValidDatasetSlug(slug: string): boolean {
  return DATASET_SLUG_RE.test(slug);
}

function pickDatasetOpenUrl(entry: DatasetRecord): string {
  if (entry.httpDatasetUrl) return entry.httpDatasetUrl;
  if (entry.url && entry.url.includes("/api/sample/") && entry.url.includes("/dataset/")) {
    return entry.url;
  }
  return `sample://${entry.id}`;
}

export function datasetIdToSampleUrl(datasetId: string): string {
  return DATASET_URL_OVERRIDES[datasetId] || `sample://${datasetId}`;
}

/** Resolve canonical open URL (HTTP dataset folder when not bundled as zip). */
export async function resolveDatasetSampleUrl(datasetId: string): Promise<string> {
  const override = DATASET_URL_OVERRIDES[datasetId];
  if (override) return override;

  const catalog = await fetchDatasetCatalog();
  const row = catalog.find((entry) => entry.id === datasetId);
  if (row) return pickDatasetOpenUrl(row);
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

function normalizeEmbedUrlParam(raw: string | null): string {
  if (!raw) return "";
  if (raw.startsWith("http://") || raw.startsWith("https://")) {
    try {
      return new URL(raw).pathname + new URL(raw).search;
    } catch {
      return raw;
    }
  }
  return raw;
}

export function iframeShowsDataset(
  frame: HTMLIFrameElement,
  datasetId: string | null,
  lang: string,
  datasetUrl?: string | null,
): boolean {
  try {
    const url = new URL(frame.src, window.location.origin);
    if (url.searchParams.get("datalab_embed") !== "1") return false;
    if ((url.searchParams.get("lang") || "en") !== lang) return false;
    const rawParam = url.searchParams.get("url");
    if (!datasetId) return !rawParam;
    if (datasetUrl) {
      const expected = normalizeEmbedUrlParam(
        datasetUrl.startsWith("stream://")
          ? `/lerobot/api/stream/${encodeURIComponent(datasetUrl.slice("stream://".length).replace(/\/$/, ""))}/`
          : datasetUrl,
      );
      const actual = normalizeEmbedUrlParam(rawParam);
      if (expected.startsWith("/lerobot/api/sample/") || expected.startsWith("/lerobot/api/stream/")) {
        return actual === expected;
      }
    }
    const sample = parseDatasetFromSampleUrl(rawParam);
    return sample === datasetId;
  } catch {
    return false;
  }
}
