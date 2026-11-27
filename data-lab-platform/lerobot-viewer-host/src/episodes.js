// Loads what the sidebar needs: info.json, meta/episodes/*.parquet and (stream only) episode-dates.json.
import { asyncBufferFromUrl, parquetReadObjects } from 'hyparquet';
import {
  appendRecordedClockToTaskLabel,
  formatDateGroupLabel,
  parseSessionDateFromTaskText,
  sortEpisodeRowsByRecordedAt,
} from '../../lerobot-studio/branding/overlay-episode-groups-lib.mjs';

export const UNKNOWN_DATE = '__unknown__';

async function readEpisodeRows(files) {
  const parts = files.filter((f) => /^meta\/episodes\/.+\.parquet$/.test(f.logicalPath));
  const rows = [];
  for (const f of parts) {
    const file = await asyncBufferFromUrl({ url: f.presignedUrl, byteLength: f.sizeBytes ?? undefined });
    rows.push(...(await parquetReadObjects({ file, columns: ['episode_index', 'length', 'tasks'] })));
  }
  return rows.map((r) => ({
    episodeIndex: Number(r.episode_index),
    length: Number(r.length),
    task: Array.isArray(r.tasks) ? r.tasks.join(', ') : String(r.tasks ?? ''),
  }));
}

async function readEpisodeDates(datesUrl) {
  if (!datesUrl) return { dateByIndex: {}, recordedAtByIndex: {} };
  try {
    const res = await fetch(datesUrl, { cache: 'no-store' });
    const payload = res.ok ? await res.json() : { episodes: [] };
    const dateByIndex = {};
    const recordedAtByIndex = {};
    for (const ep of payload.episodes || []) {
      if (ep?.episode_index == null) continue;
      const idx = Number(ep.episode_index);
      if (/^\d{2}-\d{2}$/.test(ep.date || '')) dateByIndex[idx] = ep.date;
      const recorded = ep.recorded_at || ep.uploaded_at;
      if (recorded) recordedAtByIndex[idx] = recorded;
    }
    return { dateByIndex, recordedAtByIndex };
  } catch {
    return { dateByIndex: {}, recordedAtByIndex: {} };
  }
}

/** Same rules as the old overlay: upload date first, then MM-DD in the task label. */
export async function loadSidebarModel({ files, infoUrl, datesUrl }) {
  const [info, episodes, dates] = await Promise.all([
    fetch(infoUrl, { cache: 'no-store' }).then((r) => r.json()),
    readEpisodeRows(files),
    readEpisodeDates(datesUrl),
  ]);
  const rows = sortEpisodeRowsByRecordedAt(
    episodes.map((e) => ({
      ...e,
      label: appendRecordedClockToTaskLabel(e.task, dates.recordedAtByIndex[e.episodeIndex]),
      date: dates.dateByIndex[e.episodeIndex] || parseSessionDateFromTaskText(e.task) || UNKNOWN_DATE,
    })),
    dates.recordedAtByIndex,
  );
  const groups = [];
  const byDate = new Map();
  for (const row of rows) {
    let g = byDate.get(row.date);
    if (!g) {
      g = { date: row.date, label: formatDateGroupLabel(row.date, 'zh'), rows: [] };
      byDate.set(row.date, g);
      groups.push(g);
    }
    g.rows.push(row);
  }
  return { info, episodes, rows, groups };
}

export function formatClock(frames, fps) {
  const total = Math.floor(frames / (fps || 30));
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}
