import type { TFunction } from "i18next";

type ColumnLike = {
  id?: string;
  title?: string;
  parent?: { id?: string } | null;
};

const TASK_COLUMN_KEYS: Record<string, string> = {
  id: "dm.columns.id",
  inner_id: "dm.columns.inner_id",
  completed_at: "dm.columns.completed_at",
  total_annotations: "dm.columns.total_annotations",
  cancelled_annotations: "dm.columns.cancelled_annotations",
  total_predictions: "dm.columns.total_predictions",
  annotators: "dm.columns.annotators",
  annotations_results: "dm.columns.annotations_results",
  annotations_ids: "dm.columns.annotations_ids",
  predictions_score: "dm.columns.predictions_score",
  predictions_model_versions: "dm.columns.predictions_model_versions",
  predictions_results: "dm.columns.predictions_results",
  file_upload: "dm.columns.file_upload",
  storage_filename: "dm.columns.storage_filename",
  created_at: "dm.columns.created_at",
  updated_at: "dm.columns.updated_at",
  updated_by: "dm.columns.updated_by",
  avg_lead_time: "dm.columns.avg_lead_time",
  draft_exists: "dm.columns.draft_exists",
  data: "dm.columns.data",
  reviews_accepted: "dm.columns.reviews_accepted",
  reviews_rejected: "dm.columns.reviews_rejected",
  ground_truth: "dm.columns.ground_truth",
  comment_count: "dm.columns.comment_count",
  unresolved_comment_count: "dm.columns.unresolved_comment_count",
};

const DATA_FIELD_KEYS: Record<string, string> = {
  video: "dm.columns.data_video",
  image: "dm.columns.data_image",
  audio: "dm.columns.data_audio",
  text: "dm.columns.data_text",
  html: "dm.columns.data_html",
  htm: "dm.columns.data_html",
  pdf: "dm.columns.data_pdf",
  csv: "dm.columns.data_csv",
  tsv: "dm.columns.data_tsv",
  json: "dm.columns.data_json",
  hypertext: "dm.columns.data_hypertext",
  timeseries: "dm.columns.data_timeseries",
};

/**
 * Localize Data Manager column titles from API (English defaults).
 */
export function translateDmColumnTitle(column: ColumnLike, t: TFunction<"common">): string {
  const id = column.id ?? "";
  const parentId = column.parent?.id;

  if (parentId === "data" && DATA_FIELD_KEYS[id]) {
    return t(DATA_FIELD_KEYS[id]);
  }

  if (TASK_COLUMN_KEYS[id]) {
    return t(TASK_COLUMN_KEYS[id]);
  }

  return column.title ?? "";
}
