/**
 * Per-session operational timeline (upload → derive → READY) for SLO / ops.
 */
import fs from "node:fs";
import path from "node:path";

import { writeJsonAtomic } from "./io.mjs";

function timelinePath(root, sessionId) {
  return path.join(root, "live", "session_timelines", `${sessionId}.jsonl`);
}

export function recordSessionTimelineEvent(root, sessionId, event, meta = {}) {
  if (!sessionId || !event) return;
  const line = {
    at: new Date().toISOString(),
    event,
    ...meta,
  };
  const p = timelinePath(root, sessionId);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.appendFileSync(p, `${JSON.stringify(line)}\n`);
  writeJsonAtomic(path.join(root, "live", "session_timelines", "_last.json"), {
    sessionId,
    event,
    at: line.at,
  });
}
