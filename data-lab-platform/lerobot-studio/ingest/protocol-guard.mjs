/**
 * Session upload protocol guard — block tar.zst / MCAP mixing (P0 commercial).
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { readJson } from "./io.mjs";
import { listSessionSegmentStates } from "./segment-state.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const POLICY_PATH = path.join(__dirname, "..", "config", "station-upload-policy.json");

export const SOURCE_FORMAT = Object.freeze({
  MCAP: "mcap",
  TARZST: "tarzst",
});

let policyCache = null;

function loadStationUploadPolicy() {
  if (policyCache) return policyCache;
  policyCache = readJson(POLICY_PATH, {});
  return policyCache;
}

export function collectSessionSourceFormats(root, sessionId) {
  const formats = new Set();
  for (const state of listSessionSegmentStates(root, sessionId)) {
    const fmt = String(state?.sourceFormat || "").trim();
    if (fmt) formats.add(fmt);
  }
  const lockPath = path.join(root, "state", "sessions", sessionId, "session.source_format");
  const lock = readJson(lockPath, null);
  if (lock?.format) formats.add(String(lock.format));
  return formats;
}

export function resolveSessionSourceFormat(root, sessionId) {
  const formats = collectSessionSourceFormats(root, sessionId);
  if (formats.size === 0) return null;
  if (formats.size === 1) return [...formats][0];
  return "mixed";
}

function stationUploadProtocol(stationId) {
  const allowTar = String(process.env.EGO_ALLOW_TARZST_UPLOAD || "").trim();
  if (allowTar === "1" || allowTar.toLowerCase() === "true") return null;
  const policy = loadStationUploadPolicy();
  const entry = policy?.[stationId];
  const fmt = String(entry?.upload_protocol || process.env.EGO_STATION_UPLOAD_PROTOCOL || "").trim();
  return fmt || null;
}

function protocolError(code, message, extra = {}) {
  const err = new Error(message);
  err.statusCode = 409;
  err.reason = { code, ...extra };
  return err;
}

export function assertSourceFormatCompatible(stationId, root, sessionId, incomingFormat) {
  const incoming = String(incomingFormat || "").trim();
  if (!incoming) {
    throw protocolError("PROTOCOL_UNKNOWN", "missing source format");
  }

  const stationLock = stationUploadProtocol(stationId);
  if (stationLock && stationLock !== incoming) {
    throw protocolError(
      "PROTOCOL_STATION_LOCKED",
      `station ${stationId} locked to ${stationLock}, rejected ${incoming}`,
      { stationId, locked: stationLock, incoming },
    );
  }

  const current = resolveSessionSourceFormat(root, sessionId);
  if (current === "mixed") {
    throw protocolError(
      "PROTOCOL_MIXED",
      `session ${sessionId} already has mixed protocols; reset required`,
      { sessionId, locked: "mixed", incoming },
    );
  }
  if (current && current !== incoming) {
    throw protocolError(
      "PROTOCOL_MIXED",
      `session ${sessionId} locked to ${current}, rejected ${incoming}`,
      { sessionId, locked: current, incoming },
    );
  }
}

export function lockSessionSourceFormat(root, sessionId, format) {
  const lockPath = path.join(root, "state", "sessions", sessionId, "session.source_format");
  const existing = readJson(lockPath, null);
  if (existing?.format && existing.format !== format) return existing;
  const body = {
    sessionId,
    format,
    at: new Date().toISOString(),
  };
  const tmp = `${lockPath}.tmp.${process.pid}.${Date.now()}`;
  fs.mkdirSync(path.dirname(lockPath), { recursive: true });
  fs.writeFileSync(tmp, `${JSON.stringify(body, null, 2)}\n`, "utf8");
  fs.renameSync(tmp, lockPath);
  return body;
}
