/**
 * Pure helpers for episode sidebar date grouping (testable, no DOM).
 */

/** Parse MM-DD from task label, e.g. "EGO-001 · 2a17d33d · 09-07 (173f)". */
export function parseSessionDateFromTaskText(text) {
  if (!text) return null;
  const parts = String(text)
    .replace(/\s+/g, " ")
    .split("·")
    .map((s) => s.trim());
  for (const part of parts) {
    const withClock = part.match(/^(\d{2}-\d{2})(?:\s+\d{2}:\d{2})?(?:\s*\([^)]*\)|\s+\d+f)?$/i);
    if (withClock) return withClock[1];
    if (/^\d{2}-\d{2}$/.test(part)) return part;
  }
  return null;
}

/** Human-readable group header from MM-DD. */
export function formatDateGroupLabel(mmdd, lang = "en") {
  if (!mmdd || !/^\d{2}-\d{2}$/.test(mmdd)) return mmdd || "";
  const [mm, dd] = mmdd.split("-");
  const month = parseInt(mm, 10);
  const day = parseInt(dd, 10);
  if (String(lang || "").toLowerCase().startsWith("zh")) {
    return `${month}月${day}日`;
  }
  return mmdd;
}

/**
 * Build ordered date groups from episode row descriptors.
 * @param {Array<{ episodeIndex: number, date: string|null, label: string }>} rows
 * @returns {Array<{ date: string, label: string, episodeIndices: number[] }>}
 */
export function buildDateGroups(rows) {
  const groups = [];
  const indexByDate = new Map();

  for (const row of rows) {
    const date = row.date || "__unknown__";
    let group = indexByDate.get(date);
    if (!group) {
      group = { date, episodeIndices: [] };
      indexByDate.set(date, group);
      groups.push(group);
    }
    group.episodeIndices.push(row.episodeIndex);
  }

  return groups;
}

export function unknownDateKey() {
  return "__unknown__";
}

export function parseSessionIdFromTaskText(text) {
  const m = String(text || "").match(/sess_[a-f0-9]+/i);
  return m ? m[0] : null;
}

export function formatSessionDateFromIso(iso) {
  if (!iso) return "";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "";
  const mm = String(dt.getMonth() + 1).padStart(2, "0");
  const dd = String(dt.getDate()).padStart(2, "0");
  return `${mm}-${dd}`;
}

export function formatSessionDateTimeFromIso(iso) {
  if (!iso) return "";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "";
  const mm = String(dt.getMonth() + 1).padStart(2, "0");
  const dd = String(dt.getDate()).padStart(2, "0");
  const hh = String(dt.getHours()).padStart(2, "0");
  const mi = String(dt.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

/** Insert local HH:mm after MM-DD token in auto task labels. Idempotent. */
export function appendRecordedClockToTaskLabel(taskText, iso) {
  const raw = String(taskText || "").trim();
  if (!raw || !iso) return raw;
  const stamp = formatSessionDateTimeFromIso(iso);
  if (!stamp) return raw;
  const mmdd = stamp.slice(0, 5);
  if (new RegExp(`${mmdd.replace(/-/g, "\\-")}\\s+\\d{2}:\\d{2}`).test(raw)) {
    return raw;
  }
  const escaped = mmdd.replace(/-/g, "\\-");
  return raw.replace(
    new RegExp(`(·\\s*${escaped})(\\s*·)`, ""),
    `$1 ${stamp.slice(6)}$2`,
  );
}

export function resolveEpisodeDate({ taskText, episodeIndex, uploadDateByIndex }) {
  const fromTask = parseSessionDateFromTaskText(taskText);
  if (fromTask) return fromTask;
  if (
    uploadDateByIndex &&
    uploadDateByIndex[episodeIndex] &&
    /^\d{2}-\d{2}$/.test(uploadDateByIndex[episodeIndex])
  ) {
    return uploadDateByIndex[episodeIndex];
  }
  return null;
}

/** Parse ISO timestamp from ingest registry (session started / segment committed). */
export function parseRecordedAtMs(iso) {
  if (!iso) return null;
  const t = Date.parse(String(iso));
  return Number.isFinite(t) ? t : null;
}

/**
 * Sort sidebar rows by recording time (ascending), then episode_index for ties/missing.
 * @param {Array<{ episodeIndex: number }>} rows
 * @param {Record<number, string>|null} recordedAtByIndex episode_index -> ISO time
 */
export function sortEpisodeRowsByRecordedAt(rows, recordedAtByIndex) {
  const map = recordedAtByIndex || {};
  return rows.slice().sort((a, b) => {
    const ma = parseRecordedAtMs(map[a.episodeIndex]);
    const mb = parseRecordedAtMs(map[b.episodeIndex]);
    if (ma != null && mb != null && ma !== mb) return ma - mb;
    if (ma != null && mb == null) return -1;
    if (ma == null && mb != null) return 1;
    return a.episodeIndex - b.episodeIndex;
  });
}
