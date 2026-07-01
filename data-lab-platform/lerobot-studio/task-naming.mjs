/**
 * Unified auto task naming for EGO stream ingest (mirrors ego_hand_pipeline.task_naming).
 */

export const LEGACY_PLACEHOLDER_TASK =
  "Perform egocentric manipulation tasks at the laboratory workbench";
export const TASK_LABEL_FALLBACK = "未命名任务";

/** Optional short labels for long custom task strings. */
export const TASK_SHORT_NAMES = {};

export function stationShortName(stationId) {
  const sid = String(stationId || "")
    .trim()
    .toLowerCase();
  const lan = /^ego-lan-(\d+)$/.exec(sid);
  if (lan) return `EGO-${lan[1]}`;
  if (sid.startsWith("ego-")) {
    const parts = sid.slice(4).split("-").filter(Boolean);
    if (parts.length && /^\d+$/.test(parts[parts.length - 1])) {
      return `EGO-${parts[parts.length - 1]}`;
    }
  }
  if (sid) return sid.toUpperCase().replace(/_/g, "-");
  return "EGO";
}

export function sessionIdShort(sessionId) {
  let token = String(sessionId || "").trim();
  if (token.toLowerCase().startsWith("sess_")) token = token.slice(5);
  return token.slice(0, 8).toLowerCase();
}

export function formatSessionDate(createdAt) {
  const dt = createdAt ? new Date(createdAt) : new Date();
  if (Number.isNaN(dt.getTime())) return "";
  const mm = String(dt.getMonth() + 1).padStart(2, "0");
  const dd = String(dt.getDate()).padStart(2, "0");
  return `${mm}-${dd}`;
}

export function autoTaskName({ stationId, sessionId, createdAt }) {
  return `${stationShortName(stationId)} · ${sessionIdShort(sessionId)} · ${formatSessionDate(createdAt)}`;
}

export function isLegacyPlaceholderTask(task) {
  return String(task || "")
    .trim()
    .toLowerCase() === LEGACY_PLACEHOLDER_TASK.toLowerCase();
}

export function resolveTaskName({ explicit, stationId, sessionId, createdAt }) {
  const label = String(explicit ?? "").trim();
  if (label && !isLegacyPlaceholderTask(label)) return label;
  return autoTaskName({ stationId, sessionId, createdAt });
}

export function abbreviateTask(task) {
  const trimmed = String(task || "").trim();
  if (!trimmed) return TASK_LABEL_FALLBACK;
  const key = trimmed.toLowerCase();
  if (TASK_SHORT_NAMES[key]) return TASK_SHORT_NAMES[key];
  if (trimmed.length <= 32) return trimmed;
  return `${trimmed.slice(0, 30)}…`;
}

export function formatEpisodeDisplayTask(baseTask, length) {
  let base = String(baseTask || "").trim();
  if (isLegacyPlaceholderTask(base)) base = "";
  if (!base) base = TASK_LABEL_FALLBACK;
  const n = Math.max(0, Number(length) || 0);
  if (n > 0) return `${base} · ${n}f`;
  return base;
}

export function stationIdFromRoot(root) {
  return String(root || "")
    .split(/[/\\]/)
    .filter(Boolean)
    .pop();
}
