/**
 * Idempotent derive retry policy (Phase 0 zero-failure baseline).
 */
import fs from "node:fs";
import path from "node:path";

import {
  clearSessionMarker,
  hasSessionMarker,
  readSessionMarker,
  SESSION_MARKERS,
} from "../session-markers.mjs";
import { unitDir } from "./unit-paths.mjs";

export const DERIVE_RETRYABLE_CODES = new Set([
  "GATE_INTERNAL_ERROR",
  "DERIVE_TRANSIENT",
  "MUX_TRANSIENT",
  "DERIVE_FAILED",
]);

export const DERIVE_NON_RETRYABLE_CODES = new Set([
  "PARQUET_INDEX_GAP",
  "SEGMENT_LIMIT_EXCEEDED",
  "MUX_FRAME_MISMATCH",
  "MUX_DECODE_FAILED",
  "BROWSER_NOT_PLAYABLE",
  "RECONCILE_EXCEEDED",
  "INGEST_FAILED",
  "G1",
  "G2",
  "G2b",
  "G3",
  "G7",
]);

export function maxDeriveRetryAttempts() {
  return Math.max(1, Number(process.env.DERIVE_MAX_RETRY_ATTEMPTS || 2));
}

export function deriveFailureCode(reason) {
  if (!reason) return "";
  if (typeof reason === "string") return reason;
  return String(reason.code || reason.checkId || "");
}

export function isDeriveFailureRetryable(reason) {
  const code = deriveFailureCode(reason);
  if (DERIVE_NON_RETRYABLE_CODES.has(code)) return false;
  if (DERIVE_RETRYABLE_CODES.has(code)) return true;
  return false;
}

export function readDeriveAttempt(root, sessionId) {
  const deriving = readSessionMarker(root, sessionId, SESSION_MARKERS.DERIVING);
  if (deriving?.attempt) return Number(deriving.attempt);
  const failed = readSessionMarker(root, sessionId, SESSION_MARKERS.FAILED);
  if (failed?.attempt) return Number(failed.attempt);
  return 0;
}

/** Clear FAILED/DERIVING markers and partial unit scratch before a retry. */
export function prepareSessionDeriveRetry(root, sessionId, { attempt = null } = {}) {
  const actions = [];
  if (hasSessionMarker(root, sessionId, SESSION_MARKERS.FAILED)) {
    clearSessionMarker(root, sessionId, SESSION_MARKERS.FAILED);
    actions.push("cleared_failed");
  }
  if (hasSessionMarker(root, sessionId, SESSION_MARKERS.DERIVING)) {
    clearSessionMarker(root, sessionId, SESSION_MARKERS.DERIVING);
    actions.push("cleared_deriving");
  }
  const partial = unitDir(root, sessionId);
  if (fs.existsSync(partial)) {
    const unitJson = path.join(partial, "unit.json");
    if (!fs.existsSync(unitJson)) {
      fs.rmSync(partial, { recursive: true, force: true });
      actions.push("removed_partial_unit");
    }
  }
  const tmpPartial = path.join(root, "derived", `${sessionId}.tmp`);
  if (fs.existsSync(tmpPartial)) {
    fs.rmSync(tmpPartial, { recursive: true, force: true });
    actions.push("removed_unit_tmp");
  }
  return { sessionId, actions, attempt };
}

export function structuredDeriveFailure(reason, fallbackCode = "DERIVE_FAILED") {
  if (typeof reason === "object" && reason?.code) {
    return {
      code: reason.code,
      message: String(reason.message || reason.code).slice(0, 500),
      category: reason.category || "derive",
      checkId: reason.checkId || null,
    };
  }
  return {
    code: fallbackCode,
    message: String(reason || fallbackCode).slice(0, 500),
    category: "derive",
  };
}
