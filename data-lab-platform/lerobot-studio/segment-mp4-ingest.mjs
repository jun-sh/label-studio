/**
 * v0.0.8 primary path: concat per-segment MP4 shards (from tar.zst streams/) into LeRobot video files.
 * Replaces _staging JPEG + ffmpeg mux for edge tar.zst uploads.
 */
import fs from "node:fs";
import path from "node:path";
import { unpackFrameBin } from "./frame_bin_codec.mjs";
import {
  concatMp4Files,
  muxH264FileToMp4,
  probeMp4FrameCount,
  probeMp4Resolution,
  trimMp4ToFrameCount,
} from "./mux-exec.mjs";

const DEFAULT_FPS = Number(process.env.STREAM_MUX_FPS || 30);

/** @returns {"segment_mp4"|"staging_mux"} */
export function streamPrimaryPath() {
  const v = String(process.env.STREAM_PRIMARY_PATH || "segment_mp4").trim().toLowerCase();
  return v === "staging_mux" ? "staging_mux" : "segment_mp4";
}

export function isSegmentMp4PrimaryPath() {
  return streamPrimaryPath() === "segment_mp4";
}

export function isStreamFramePushEnabled() {
  const v = String(process.env.STREAM_FRAME_PUSH || "0").trim().toLowerCase();
  return v === "1" || v === "true" || v === "yes";
}

/** Legacy bin→staging JPEG mux; disabled when STREAM_PRIMARY_PATH=segment_mp4 (production). */
export function legacyStagingMuxEnabled() {
  return streamPrimaryPath() === "staging_mux";
}

/** observation_images_camera_front_left → observation.images.camera_front_left */
export function videoKeyFromStreamSafeName(filename) {
  const base = String(filename || "").replace(/\.(mp4|h264)$/i, "");
  if (!base.startsWith("observation_images_")) return null;
  return `observation.images.${base.slice("observation_images_".length)}`;
}

export function listSegmentStreamH264s(extractDir) {
  const streamsDir = path.join(extractDir, "streams");
  if (!fs.existsSync(streamsDir)) return [];
  return fs
    .readdirSync(streamsDir)
    .filter((f) => f.endsWith(".h264"))
    .map((f) => ({
      videoKey: videoKeyFromStreamSafeName(f),
      absPath: path.join(streamsDir, f),
    }))
    .filter((x) => x.videoKey);
}

export function segmentHasStreamH264(extractDir) {
  return listSegmentStreamH264s(extractDir).length > 0;
}

/** Edge JPEG production: frames/*.bin packed in tar.zst (mux on ingest via staging). */
export function segmentHasFrameBins(extractDir) {
  const framesDir = path.join(extractDir, "frames");
  if (!fs.existsSync(framesDir)) return false;
  return fs.readdirSync(framesDir).some((name) => name.endsWith(".bin"));
}

function isH264Payload(buf) {
  return (
    Buffer.isBuffer(buf) &&
    buf.length >= 4 &&
    buf[0] === 0 &&
    buf[1] === 0 &&
    buf[2] === 0 &&
    buf[3] === 1
  );
}

function streamSafeNameFromVideoKey(videoKey) {
  return String(videoKey || "").replace(/\./g, "_");
}

/**
 * DLB1 frame bins may carry per-camera H264 NALs (Plan B capture). Materialize streams/*.h264
 * so prepareSegmentStreamsForIngest can mux segment MP4 shards before ingest.
 * @returns {{ ok: boolean, reason?: string, cameras?: number, frames?: number, skipped?: boolean }}
 */
export function materializeStreamH264FromFrameBins(extractDir) {
  if (!segmentHasFrameBins(extractDir)) {
    return { ok: false, reason: "no_frame_bins" };
  }
  if (segmentHasStreamMp4(extractDir) || listSegmentStreamH264s(extractDir).length > 0) {
    return { ok: true, skipped: true, cameras: listSegmentStreamMp4s(extractDir).length };
  }
  const framesDir = path.join(extractDir, "frames");
  const streamsDir = path.join(extractDir, "streams");
  ensureDir(streamsDir);
  const binFiles = fs
    .readdirSync(framesDir)
    .filter((name) => name.endsWith(".bin"))
    .sort((a, b) => {
      const na = Number.parseInt(a.replace(/\D/g, ""), 10);
      const nb = Number.parseInt(b.replace(/\D/g, ""), 10);
      return na - nb;
    });
  /** @type {Map<string, Buffer[]>} */
  const chunks = new Map();
  let h264FrameBins = 0;
  for (const binName of binFiles) {
    const binPath = path.join(framesDir, binName);
    let cameras;
    try {
      cameras = unpackFrameBin(fs.readFileSync(binPath));
    } catch {
      continue;
    }
    let sawH264 = false;
    for (const [videoKey, payload] of Object.entries(cameras)) {
      if (!isH264Payload(payload)) continue;
      sawH264 = true;
      const safe = streamSafeNameFromVideoKey(videoKey);
      if (!safe) continue;
      const list = chunks.get(safe) || [];
      list.push(payload);
      chunks.set(safe, list);
    }
    if (sawH264) h264FrameBins += 1;
  }
  if (!chunks.size) {
    return { ok: false, reason: "no_h264_in_bins" };
  }
  for (const [safe, parts] of chunks.entries()) {
    const h264Path = path.join(streamsDir, `${safe}.h264`);
    fs.writeFileSync(h264Path, Buffer.concat(parts));
  }
  return { ok: true, cameras: chunks.size, frames: h264FrameBins };
}

/**
 * Edge uploads streams/*.h264 — mux to MP4 on ingest before segment_mp4 concat.
 * @returns {{ muxed: number, skipped: number, failures: string[] }}
 */
export function prepareSegmentStreamsForIngest(extractDir, expectedRows = 0) {
  if (segmentHasStreamMp4(extractDir)) {
    return { muxed: 0, skipped: listSegmentStreamMp4s(extractDir).length, failures: [] };
  }
  const h264Shards = listSegmentStreamH264s(extractDir);
  if (!h264Shards.length) {
    return { muxed: 0, skipped: 0, failures: [] };
  }
  const failures = [];
  let muxed = 0;
  for (const { videoKey, absPath } of h264Shards) {
    const mp4Path = absPath.replace(/\.h264$/i, ".mp4");
    if (fs.existsSync(mp4Path) && probeMp4FrameCount(mp4Path, { defaultFps: DEFAULT_FPS }) > 0) {
      muxed += 1;
      continue;
    }
    const res = muxH264FileToMp4(absPath, mp4Path, {
      fps: DEFAULT_FPS,
      expectedFrames: expectedRows,
    });
    if (res.ok) {
      muxed += 1;
      try {
        fs.unlinkSync(absPath);
      } catch {
        /* keep h264 if delete fails */
      }
    } else {
      failures.push(`${videoKey}:${res.stderr || "mux_failed"}`);
    }
  }
  return { muxed, skipped: 0, failures };
}

export function listSegmentStreamMp4s(extractDir) {
  const streamsDir = path.join(extractDir, "streams");
  if (!fs.existsSync(streamsDir)) return [];
  return fs
    .readdirSync(streamsDir)
    .filter((f) => f.endsWith(".mp4"))
    .map((f) => ({
      videoKey: videoKeyFromStreamSafeName(f),
      absPath: path.join(streamsDir, f),
    }))
    .filter((x) => x.videoKey);
}

export function segmentHasStreamMp4(extractDir) {
  return listSegmentStreamMp4s(extractDir).length > 0;
}

function segmentMp4StrictParityEnabled() {
  return String(process.env.STREAM_SEGMENT_MP4_STRICT || "1").trim().toLowerCase() !== "0";
}

function probeSegmentMp4Counts(shards) {
  return shards.map(({ videoKey, absPath }) => ({
    videoKey,
    frames: probeMp4FrameCount(absPath, { defaultFps: DEFAULT_FPS }),
  }));
}

/** Fail when segment rows.jsonl and per-camera MP4 packet counts diverge. */
export function validateSegmentMp4RowParity(extractDir, rowCount) {
  const shards = listSegmentStreamMp4s(extractDir);
  if (!shards.length) {
    return { ok: false, reason: "no_segment_mp4", videoFrames: 0, rowCount };
  }
  let counts = probeSegmentMp4Counts(shards);
  let vals = counts.map((c) => c.frames).filter((n) => n > 0);
  if (!vals.length) {
    return { ok: false, reason: "mp4_probe_empty", videoFrames: 0, rowCount, counts };
  }
  let minFrames = Math.min(...vals);
  let maxFrames = Math.max(...vals);
  let spread = maxFrames - minFrames;
  const spreadTolerance = Math.max(
    1,
    Number(process.env.STREAM_SEGMENT_MP4_SPREAD_TOLERANCE || "1"),
  );
  if (segmentMp4StrictParityEnabled() && spread > spreadTolerance) {
    for (const shard of shards) {
      const n = probeMp4FrameCount(shard.absPath, { defaultFps: DEFAULT_FPS });
      if (n > minFrames) {
        trimMp4ToFrameCount(shard.absPath, minFrames);
      }
    }
    counts = probeSegmentMp4Counts(shards);
    vals = counts.map((c) => c.frames).filter((n) => n > 0);
    minFrames = Math.min(...vals);
    maxFrames = Math.max(...vals);
    spread = maxFrames - minFrames;
  }
  const target = Math.min(rowCount, minFrames);
  if (segmentMp4StrictParityEnabled()) {
    if (spread > spreadTolerance) {
      throw new Error(
        `segment_mp4_camera_spread spread=${spread} counts=${JSON.stringify(counts)}`,
      );
    }
    if (Math.abs(rowCount - minFrames) > 1) {
      const rowsPath = path.join(extractDir, "rows.jsonl");
      if (rowCount > target && fs.existsSync(rowsPath)) {
        const lines = fs
          .readFileSync(rowsPath, "utf8")
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean);
        if (lines.length > target) {
          fs.writeFileSync(rowsPath, `${lines.slice(0, target).join("\n")}\n`);
        }
      } else if (rowCount < minFrames - 1) {
        throw new Error(
          `segment_mp4_row_parity_mismatch rows=${rowCount} video=${minFrames} counts=${JSON.stringify(counts)}`,
        );
      }
    }
  }
  return { ok: true, videoFrames: target, rowCount: target, counts, spread };
}

/** v0.0.9+: primary path requires segment streams (MP4/H264 on edge, or JPEG frame_bin muxed here). */
export function assertSegmentMp4Archive(extractDir, expectedRows = 0) {
  if (!isSegmentMp4PrimaryPath()) return;
  if (segmentHasFrameBins(extractDir)) return;
  const prep = prepareSegmentStreamsForIngest(extractDir, expectedRows);
  if (prep.failures.length) {
    throw new Error(
      `segment_h264_mux_failed: ${prep.failures.slice(0, 3).join("; ")}`,
    );
  }
  const minMp4 = Math.max(1, Number(process.env.STREAM_SEGMENT_MP4_MIN || 4));
  const mp4Count = listSegmentStreamMp4s(extractDir).length;
  if (!segmentHasStreamMp4(extractDir) || mp4Count < minMp4) {
    throw new Error(
      `segment_mp4_required: need >=${minMp4} streams/*.mp4 (got ${mp4Count}); edge may ship streams/*.h264`,
    );
  }
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

const mp4ConcatLocks = new Map();

function mp4ConcatLockKey(root, videoKey) {
  return `${root}::${videoKey}`;
}

async function withMp4ConcatLock(root, videoKey, fn) {
  const key = mp4ConcatLockKey(root, videoKey);
  const prev = mp4ConcatLocks.get(key) || Promise.resolve();
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const chain = prev.then(() => gate);
  mp4ConcatLocks.set(key, chain);
  await prev;
  try {
    return await fn();
  } finally {
    release();
    if (mp4ConcatLocks.get(key) === chain) {
      mp4ConcatLocks.delete(key);
    }
  }
}

/**
 * Append segment MP4 shards to LeRobot per-camera video files.
 * @param {object} hooks - { videoArtifactRel, setChunkArtifactStatus, streamLog }
 */
export async function ingestSegmentMp4Shards(
  root,
  stationId,
  sessionId,
  segmentId,
  extractDir,
  hooks,
) {
  const { videoArtifactRel, setChunkArtifactStatus, streamLog } = hooks;
  const shards = listSegmentStreamMp4s(extractDir);
  if (!shards.length) {
    return { ok: false, reason: "no_segment_mp4", cameras: 0, results: [] };
  }

  const results = [];
  for (const { videoKey, absPath } of shards) {
    const rel = videoArtifactRel(videoKey);
    const outFile = path.join(root, rel);
    ensureDir(path.dirname(outFile));
    const shardFrames = probeMp4FrameCount(absPath, { defaultFps: DEFAULT_FPS });
    const ingestOne = async () => {
      let publishedFrames = 0;
      const beforeFrames = fs.existsSync(outFile)
        ? probeMp4FrameCount(outFile, { defaultFps: DEFAULT_FPS })
        : 0;
      if (fs.existsSync(outFile)) {
        const existingRes = probeMp4Resolution(outFile);
        const shardRes = probeMp4Resolution(absPath);
        if (
          existingRes &&
          shardRes &&
          (existingRes.width !== shardRes.width || existingRes.height !== shardRes.height)
        ) {
          streamLog(stationId, "segment_mp4_resolution_reset", {
            sessionId,
            segmentId,
            videoKey,
            existing: `${existingRes.width}x${existingRes.height}`,
            shard: `${shardRes.width}x${shardRes.height}`,
          });
          fs.unlinkSync(outFile);
          fs.copyFileSync(absPath, outFile);
          publishedFrames = probeMp4FrameCount(outFile, { defaultFps: DEFAULT_FPS });
        } else {
        const listPath = `${outFile}.segment.${segmentId}.concat.txt`;
        const tmpOut = `${outFile}.concat.${segmentId}.tmp.mp4`;
        try {
          if (fs.existsSync(tmpOut)) fs.unlinkSync(tmpOut);
        } catch {
          /* ignore */
        }
        const concatRes = await concatMp4Files(outFile, absPath, tmpOut, listPath, { segmentMp4: true });
        try {
          if (fs.existsSync(listPath)) fs.unlinkSync(listPath);
        } catch {
          /* ignore */
        }
        if (!concatRes?.ok) {
          try {
            if (fs.existsSync(tmpOut)) fs.unlinkSync(tmpOut);
          } catch {
            /* ignore */
          }
          streamLog(stationId, "segment_mp4_concat_fail", {
            sessionId,
            segmentId,
            videoKey,
            stderr: String(concatRes?.stderr || "").slice(0, 200),
          });
          return { videoKey, ok: false };
        }
        fs.renameSync(tmpOut, outFile);
        publishedFrames = probeMp4FrameCount(outFile, { defaultFps: DEFAULT_FPS });
        }
      } else {
        fs.copyFileSync(absPath, outFile);
        publishedFrames = probeMp4FrameCount(outFile, { defaultFps: DEFAULT_FPS });
      }
      const legacyTolerance =
        String(process.env.STREAM_SEGMENT_MP4_LEGACY_TOLERANCE || "0").trim().toLowerCase() === "1";
      const minExpected = legacyTolerance
        ? beforeFrames + Math.max(1, Math.floor(shardFrames * 0.85))
        : beforeFrames + shardFrames;
      if (beforeFrames > 0 && publishedFrames < minExpected - (legacyTolerance ? 0 : 1)) {
        streamLog(stationId, "segment_mp4_concat_short", {
          sessionId,
          segmentId,
          videoKey,
          beforeFrames,
          shardFrames,
          publishedFrames,
          minExpected,
        });
        return { videoKey, ok: false, frames: publishedFrames };
      }
      setChunkArtifactStatus(root, rel, "finished", publishedFrames);
      return { videoKey, ok: true, frames: publishedFrames };
    };
    const row = await withMp4ConcatLock(root, videoKey, ingestOne);
    results.push(row);
  }

  const ok = results.some((r) => r.ok);
  streamLog(stationId, "segment_mp4_ingest", {
    sessionId,
    segmentId,
    cameras: results.length,
    ok,
    frames: results.filter((r) => r.ok).map((r) => ({ k: r.videoKey, n: r.frames })),
  });
  return { ok, cameras: results.length, results };
}

export function archiveSegmentTarZst(root, sessionId, segmentId, archivePath) {
  const destDir = path.join(root, "raw", "segments", sessionId);
  ensureDir(destDir);
  const dest = path.join(destDir, `${segmentId}.tar.zst`);
  if (!fs.existsSync(dest)) {
    fs.copyFileSync(archivePath, dest);
  }
  return dest;
}
