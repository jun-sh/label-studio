/**
 * Stable dataset identity across host / container mount prefixes (mirrors qc_store.py).
 */

export function canonicalDatasetKey(datasetRoot) {
  const parts = String(datasetRoot).split("/").filter(Boolean);
  let start = 0;
  for (let index = 0; index < parts.length; index += 1) {
    const part = parts[index];
    const lowered = part.toLowerCase();
    if (lowered === "bookduo" || lowered === "host-media" || part.startsWith("BookDuo")) {
      start = index + 1;
    }
  }
  const tail = parts.slice(start);
  if (tail.length >= 2) {
    return `${tail[tail.length - 2]}/${tail[tail.length - 1]}`;
  }
  if (tail.length === 1) {
    return tail[0];
  }
  const name = parts[parts.length - 1];
  return name || "unknown";
}
