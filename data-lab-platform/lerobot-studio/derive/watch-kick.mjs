/**
 * Q1-3: multi-worker safe derive kick — claim → lock → deriving → pipeline → release claim.
 */
import { acquireDeriverLock, releaseDeriverLock } from "../derive-lock.mjs";
import { runDerivePipelineCore } from "../derive-pipeline.mjs";
import {
  listSessionsEligibleForDerive,
  markSessionDeriving,
  markSessionFailed,
  SESSION_MARKERS,
  hasSessionMarker,
} from "../session-markers.mjs";
import { streamLog } from "../stream-ingest.mjs";
import { claimSession, releaseSessionClaim, resolveDeriveWorkerId } from "./session-claim.mjs";
import { evaluateSessionSegmentLimit } from "./segment-limit.mjs";
import {
  maxDeriveRetryAttempts,
  prepareSessionDeriveRetry,
  readDeriveAttempt,
} from "./session-retry.mjs";
import { reconcileSessionUploadMarkers } from "../ingest/session-coordinator.mjs";

/**
 * @typedef {object} KickDeriveOptions
 * @property {string} [workerId]
 * @property {string} [sessionId] force a session (tests)
 * @property {boolean} [muxOnly]
 * @property {(stationId: string, opts: object) => Promise<object>} [runPipeline]
 */

/**
 * Pick and kick the next pending session using claim + lock guards.
 * Flow: claimSession → acquireDeriverLock → markSessionDeriving → pipeline → releaseSessionClaim
 */
export async function kickDeriveSession(stationId, root, options = {}) {
  const workerId = String(options.workerId || resolveDeriveWorkerId());
  const muxOnly = Boolean(options.muxOnly);
  const runPipeline = options.runPipeline || runDerivePipelineCore;

  const pending = listSessionsEligibleForDerive(root, { maxAttempts: maxDeriveRetryAttempts() });
  if (!pending.length) {
    return { ok: false, reason: "no_pending" };
  }

  const candidates = options.sessionId ? [options.sessionId] : pending;
  let sessionId = null;
  let claimResult = null;

  for (const sid of candidates) {
    if (!pending.includes(sid) && !options.sessionId) continue;
    reconcileSessionUploadMarkers(root, sid);
    if (!hasSessionMarker(root, sid, SESSION_MARKERS.DONE_UPLOAD)) continue;
    if (hasSessionMarker(root, sid, SESSION_MARKERS.READY)) continue;
    if (hasSessionMarker(root, sid, SESSION_MARKERS.DERIVING)) continue;

    if (hasSessionMarker(root, sid, SESSION_MARKERS.FAILED)) {
      const attempt = readDeriveAttempt(root, sid);
      const failed = prepareSessionDeriveRetry(root, sid, { attempt: attempt + 1 });
      streamLog(stationId, "derive_watch_retry_prepare", {
        sessionId: sid,
        workerId,
        attempt: attempt + 1,
        actions: failed.actions,
      });
    }

    const segmentLimit = evaluateSessionSegmentLimit(root, sid);
    if (!segmentLimit.ok) {
      if (segmentLimit.action === "fail") {
        markSessionFailed(root, sid, segmentLimit.reason, { segmentLimit });
        streamLog(stationId, "derive_watch_fail_oversized", {
          sessionId: sid,
          workerId,
          count: segmentLimit.count,
          max: segmentLimit.max,
        });
      } else {
        streamLog(stationId, "derive_watch_defer_oversized", {
          sessionId: sid,
          workerId,
          count: segmentLimit.count,
          max: segmentLimit.max,
        });
      }
      continue;
    }

    claimResult = claimSession(root, sid, workerId);
    if (claimResult.ok) {
      sessionId = sid;
      break;
    }
    streamLog(stationId, "derive_watch_claim_skip", {
      sessionId: sid,
      workerId,
      reason: claimResult.reason,
    });
  }

  if (!sessionId) {
    return {
      ok: false,
      reason: "claim_failed",
      pending: pending.length,
      lastClaim: claimResult,
    };
  }

  streamLog(stationId, "derive_watch_claim", { sessionId, workerId, pending: pending.length });

  try {
    acquireDeriverLock(root, { stationId, sessionId, muxOnly, workerId });
  } catch (err) {
    releaseSessionClaim(root, sessionId, workerId);
    return {
      ok: false,
      reason: "lock_failed",
      sessionId,
      message: String(err?.message || err).slice(0, 200),
    };
  }

  markSessionDeriving(root, sessionId, { stationId, muxOnly, workerId, pid: process.pid, attempt: readDeriveAttempt(root, sessionId) + 1 });

  try {
    const result = await runPipeline(stationId, { sessionId, muxOnly });
    return { ok: Boolean(result?.ok), sessionId, workerId, ...result };
  } finally {
    releaseSessionClaim(root, sessionId, workerId);
    releaseDeriverLock(root, { sessionId, workerId });
  }
}
