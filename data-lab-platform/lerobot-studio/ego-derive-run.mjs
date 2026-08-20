#!/usr/bin/env node
/**
 * Standalone derive CLI — runs outside stream-ingest HTTP process.
 * Usage:
 *   node ego-derive-run.mjs run --station ego-001 [--session sess_...] [--mux-only]
 */
import process from "node:process";
import { runDerivePipelineBlocking, pickPrimarySessionId } from "./derive-pipeline.mjs";
import { computeDeriveStatusFromDisk, stationRoot, streamLog } from "./stream-ingest.mjs";

function parseArgs(argv) {
  const args = { command: "run", station: "", session: "", muxOnly: false, json: false, repair: false };
  const rest = [...argv];
  if (rest[0] && !rest[0].startsWith("-")) {
    args.command = rest.shift();
  }
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i];
    if (a === "--station" || a === "-s") args.station = rest[++i] || "";
    else if (a === "--session") args.session = rest[++i] || "";
    else if (a === "--mux-only") args.muxOnly = true;
    else if (a === "--json") args.json = true;
    else if (a === "--repair") args.repair = true;
    else if (a === "--help" || a === "-h") args.help = true;
  }
  if (!args.station) {
    args.station = process.env.STATION_ID || process.env.EGO_STATION_ID || "ego-001";
  }
  return args;
}

function printHelp() {
  console.log(`ego-derive run — standalone derive worker (commercial path)

Usage:
  node ego-derive-run.mjs run --station <id> [--session <sess>] [--mux-only] [--json]

  node ego-derive-run.mjs rebuild-view --station <id> [--json]
  node ego-derive-run.mjs fsck --station <id> [--repair] [--json]

Environment:
  STREAM_DATA_ROOT   default /srv/stream
  DERIVE_LAYOUT      unit (default) | legacy (deprecated, ignored)
  DERIVE_EXTRACT_BACKEND / DERIVE_MUX_BACKEND / DERIVE_PARQUET_BACKEND

Markers written under state/sessions/<session>/:
  session.DONE_UPLOAD → session.DERIVING → session.READY | session.FAILED
`);
}

async function cmdRun(args) {
  const stationId = args.station.trim();
  const sessionId = args.session.trim() || pickPrimarySessionId(stationId);
  if (!sessionId) {
    const err = { ok: false, error: "no_session", stationId };
    if (args.json) console.log(JSON.stringify(err));
    else console.error("no session with raw segments for station", stationId);
    process.exit(2);
  }

  streamLog(stationId, "ego_derive_cli_start", { sessionId, muxOnly: args.muxOnly, pid: process.pid });
  const startedAt = Date.now();

  try {
    const result = await runDerivePipelineBlocking(stationId, {
      sessionId,
      muxOnly: args.muxOnly,
    });
    const elapsedMs = Date.now() - startedAt;
    const disk = computeDeriveStatusFromDisk(stationId, sessionId);
    const payload = { ...result, elapsedMs, disk };
    if (args.json) {
      console.log(JSON.stringify(payload, null, 2));
    } else {
      const phase = result.ok ? "READY" : result.phase || "FAILED";
      console.log(
        `[ego-derive] ${stationId} · ${sessionId.slice(0, 12)}… → ${phase} (${Math.round(elapsedMs / 1000)}s)`,
      );
      if (disk.total > 0) {
        console.log(`  markers ${disk.markers}/${disk.total} · parquet ${disk.parquetRows} · mp4Ok=${disk.mp4Ok}`);
      }
    }
    process.exit(result.ok ? 0 : 1);
  } catch (err) {
    const elapsedMs = Date.now() - startedAt;
    if (err?.code === "DERIVER_LOCKED") {
      const payload = { ok: false, error: "deriver_locked", lock: err.lock, elapsedMs };
      if (args.json) console.log(JSON.stringify(payload));
      else console.error(`[ego-derive] locked: pid ${err.lock?.pid} since ${err.lock?.startedAt}`);
      process.exit(3);
    }
    streamLog(stationId, "ego_derive_cli_fail", {
      sessionId,
      message: String(err?.message || err).slice(0, 300),
    });
    if (args.json) console.log(JSON.stringify({ ok: false, error: String(err?.message || err), elapsedMs }));
    else console.error("[ego-derive] failed:", err?.message || err);
    process.exit(1);
  }
}

async function cmdStatus(args) {
  const stationId = args.station.trim();
  const sessionId = args.session.trim() || pickPrimarySessionId(stationId);
  if (!sessionId) {
    console.error("no session");
    process.exit(2);
  }
  const root = stationRoot(stationId);
  const disk = computeDeriveStatusFromDisk(stationId, sessionId);
  const { readSessionMarker, SESSION_MARKERS } = await import("./session-markers.mjs");
  const markers = Object.fromEntries(
    Object.values(SESSION_MARKERS).map((m) => [m, readSessionMarker(root, sessionId, m)]),
  );
  const payload = { stationId, sessionId, disk, sessionMarkers: markers };
  if (args.json) console.log(JSON.stringify(payload, null, 2));
  else {
    console.log(`${stationId} · ${sessionId}`);
    console.log(`  disk phase=${disk.phase} markers=${disk.markers}/${disk.total} parquet=${disk.parquetRows}`);
    for (const [name, val] of Object.entries(markers)) {
      if (val) console.log(`  ${name}: ${val.at || "present"}`);
    }
  }
}

async function cmdRebuildView(args) {
  const stationId = args.station.trim();
  const root = stationRoot(stationId);
  const { runDerivePipelineUnit } = await import("./derive/pipeline-unit.mjs");
  const result = await runDerivePipelineUnit(stationId, { rebuildViewOnly: true });
  if (args.json) console.log(JSON.stringify(result, null, 2));
  else console.log(`[ego-derive] rebuild-view ${stationId} episodes=${result.manifest?.episodes?.length ?? 0}`);
  process.exit(result.ok ? 0 : 1);
}

async function cmdFsck(args) {
  const stationId = args.station.trim();
  const root = stationRoot(stationId);
  const { runUnitFsck } = await import("./derive/pipeline-unit.mjs");
  const result = await runUnitFsck(root, stationId, { repair: Boolean(args.repair) });
  if (args.json) console.log(JSON.stringify(result, null, 2));
  else {
    console.log(`[ego-derive] fsck ${stationId} ok=${result.ok} issues=${result.issues.length}`);
    for (const issue of result.issues) console.log(`  - ${issue.session_id}: ${issue.issue}`);
  }
  process.exit(result.ok ? 0 : 1);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    printHelp();
    return;
  }
  if (args.command === "status") {
    await cmdStatus(args);
    return;
  }
  if (args.command === "run") {
    await cmdRun(args);
    return;
  }
  if (args.command === "rebuild-view") {
    await cmdRebuildView(args);
    return;
  }
  if (args.command === "fsck") {
    await cmdFsck(args);
    return;
  }
  printHelp();
  process.exit(2);
}

main();
