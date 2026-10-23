#!/usr/bin/env node
/**
 * P0 ops: reconcile upload gates + clear stale DERIVING for one station.
 * Usage: node scripts/p0-reconcile-station-sessions.mjs ego-002
 */
import fs from "node:fs";
import path from "node:path";

import {
  maybeMarkSessionDoneUpload,
  reconcileSessionUploadMarkers,
} from "../ingest/session-coordinator.mjs";
import {
  clearStaleSessionDeriving,
  hasSessionMarker,
  SESSION_MARKERS,
} from "../session-markers.mjs";
import { stationRoot, STREAM_ROOT } from "../stream-ingest.mjs";

const stationId = (process.argv[2] || "ego-002").trim();
const root = stationRoot(stationId);

function listSessionIds() {
  const ids = new Set();
  const rawRoot = path.join(root, "raw", "segments");
  if (fs.existsSync(rawRoot)) {
    for (const name of fs.readdirSync(rawRoot)) {
      if (!name.startsWith(".")) ids.add(name);
    }
  }
  const stateRoot = path.join(root, "state", "sessions");
  if (fs.existsSync(stateRoot)) {
    for (const name of fs.readdirSync(stateRoot)) {
      if (name.startsWith("sess_")) ids.add(name);
    }
  }
  return [...ids].sort();
}

if (!fs.existsSync(STREAM_ROOT)) {
  console.error(`STREAM_ROOT missing: ${STREAM_ROOT}`);
  process.exit(1);
}

const rows = [];
for (const sessionId of listSessionIds()) {
  const stale = clearStaleSessionDeriving(root, sessionId);
  const upload = reconcileSessionUploadMarkers(root, sessionId);
  let marked = false;
  if (upload.phase === "INGEST_COMPLETE" || (upload.gate.complete && !hasSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD))) {
    marked = maybeMarkSessionDoneUpload(root, sessionId, { source: "p0_reconcile" }).marked;
  }
  let phase = upload.phase;
  if (marked) phase = "DONE_UPLOAD";
  if (hasSessionMarker(root, sessionId, SESSION_MARKERS.READY)) phase = "READY";
  else if (hasSessionMarker(root, sessionId, SESSION_MARKERS.DERIVING)) phase = "DERIVING";
  else if (hasSessionMarker(root, sessionId, SESSION_MARKERS.FAILED)) phase = "FAILED";
  else if (hasSessionMarker(root, sessionId, SESSION_MARKERS.DONE_UPLOAD)) phase = "DONE_UPLOAD";

  rows.push({
    sessionId,
    phase,
    staleDeriving: stale.cleared,
    uploadActions: upload.actions,
    complete: upload.gate.complete,
    gaps: upload.gate.sealReconcile?.deliveryQuality?.gaps?.length ?? 0,
    issues: upload.gate.sealReconcile?.issues?.map((i) => i.code).join(",") || "",
  });
}

console.log(`station=${stationId} sessions=${rows.length}`);
for (const r of rows) {
  console.log(
    `${r.sessionId.slice(0, 18)} phase=${r.phase} complete=${r.complete} gaps=${r.gaps} issues=${r.issues}${r.staleDeriving ? " stale_deriving_cleared" : ""}${r.uploadActions.length ? ` actions=${r.uploadActions.join(",")}` : ""}`,
  );
}

const stuck = rows.filter((r) => !["READY", "DONE_UPLOAD", "DERIVING"].includes(r.phase) && r.complete);
if (stuck.length) {
  console.log(`note: ${stuck.length} session(s) gate-complete but not queued — check seal/issues above`);
}
