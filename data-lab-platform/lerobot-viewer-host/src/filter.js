// Sidebar episode filter: search text plus the 高级 duration range, same rules as the old build
// (bounds in seconds, inclusive, `length / fps`; an empty or non-numeric bound is ignored).

function parseBound(value) {
  if (value === '' || value == null) return undefined;
  const n = parseFloat(value);
  return Number.isNaN(n) ? undefined : n;
}

/** True when `row` ({ episodeIndex, label, length }) passes the search text and duration range. */
export function matchesEpisode(row, { query = '', minS = '', maxS = '', fps } = {}) {
  const q = String(query).trim().toLowerCase();
  if (q && !row.label.toLowerCase().includes(q) && !`# ${row.episodeIndex}`.includes(q)) return false;
  const seconds = (row.length || 0) / (fps || 30);
  const min = parseBound(minS);
  const max = parseBound(maxS);
  if (min != null && seconds < min) return false;
  if (max != null && seconds > max) return false;
  return true;
}
