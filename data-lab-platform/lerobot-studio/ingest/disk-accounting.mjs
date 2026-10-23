/**
 * Hot-tier disk usage accounting for stream stations.
 *
 * Production model: O(1) incremental updates on ingest/purge; full `du` only as
 * scheduled background reconciliation — never on the upload HTTP critical path.
 */

import fs from "node:fs";
import path from "node:path";

import { writeJsonAtomic } from "./io.mjs";
import { stationRoot } from "./station-context.mjs";

function housekeepingPath(root) {
  return path.join(root, "live", "disk-housekeeping.json");
}

function readHousekeeping(root) {
  try {
    return JSON.parse(fs.readFileSync(housekeepingPath(root), "utf8"));
  } catch {
    return null;
  }
}

function writeHousekeeping(root, patch) {
  const prev = readHousekeeping(root) || {};
  const next = { ...prev, ...patch, at: new Date().toISOString() };
  writeJsonAtomic(housekeepingPath(root), next);
}

/**
 * @returns {number | null} tracked usage bytes, or null if never initialized
 */
export function getTrackedUsageBytes(root) {
  const hk = readHousekeeping(root);
  if (!hk || typeof hk.usageBytes !== "number" || !Number.isFinite(hk.usageBytes)) {
    return null;
  }
  return Math.max(0, Math.floor(hk.usageBytes));
}

/** Record bytes added (+) or removed (-) on disk for this station. */
export function recordStationDiskDelta(root, stationId, deltaBytes, meta = {}) {
  if (!Number.isFinite(deltaBytes) || deltaBytes === 0) return;
  const hk = readHousekeeping(root);
  let base = 0;
  if (hk && typeof hk.usageBytes === "number" && Number.isFinite(hk.usageBytes)) {
    base = hk.usageBytes;
  }
  const usageBytes = Math.max(0, Math.floor(base + deltaBytes));
  writeHousekeeping(root, {
    stationId,
    usageBytes,
    usageSource: "incremental",
    lastDeltaBytes: Math.floor(deltaBytes),
    lastDeltaReason: meta.reason || null,
    lastDeltaAt: new Date().toISOString(),
  });
}

export function recordStationUsageReconcile(root, stationId, usageBytes, meta = {}) {
  writeHousekeeping(root, {
    stationId,
    usageBytes: Math.max(0, Math.floor(usageBytes)),
    usageSource: "reconcile",
    lastReconcileAt: new Date().toISOString(),
    ...meta,
  });
}

export function stationRootFromId(stationId) {
  return stationRoot(stationId);
}
