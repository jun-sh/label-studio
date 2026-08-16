/**
 * v0.0.8 primary path: concat per-segment MP4 shards (from tar.zst streams/) into LeRobot video files.
 * Replaces _staging JPEG + ffmpeg mux for edge tar.zst uploads.
 */
import fs from "node:fs";
import path from "node:path";
import { concatMp4Files, probeMp4FrameCount } from "./mux-exec.mjs";

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

/** observation_images_camera_front_left → observation.images.camera_front_left */
export function videoKeyFromStreamSafeName(filename) {
  const base = String(filename || "").replace(/\.mp4$/i, "");
  if (!base.startsWith("observation_images_")) return null;
  return `observation.images.${base.slice("observation_images_".length)}`;
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

/** v0.0.9+: primary path rejects archives without segment MP4 (no staging fallback). */
export function assertSegmentMp4Archive(extractDir) {
  if (!isSegmentMp4PrimaryPath()) return;
  if (!segmentHasStreamMp4(extractDir)) {
    throw new Error(
      "segment_mp4_required: tar.zst must contain streams/*.mp4 when STREAM_PRIMARY_PATH=segment_mp4",
    );
  }
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
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
    let publishedFrames = 0;
    if (fs.existsSync(outFile)) {
      const listPath = `${outFile}.segment.${segmentId}.concat.txt`;
      const concatRes = await concatMp4Files(outFile, absPath, outFile, listPath);
      try {
        if (fs.existsSync(listPath)) fs.unlinkSync(listPath);
      } catch {
        /* ignore */
      }
      if (!concatRes?.ok) {
        streamLog(stationId, "segment_mp4_concat_fail", {
          sessionId,
          segmentId,
          videoKey,
          stderr: String(concatRes?.stderr || "").slice(0, 200),
        });
        results.push({ videoKey, ok: false });
        continue;
      }
      publishedFrames = probeMp4FrameCount(outFile, { defaultFps: DEFAULT_FPS });
    } else {
      fs.copyFileSync(absPath, outFile);
      publishedFrames = probeMp4FrameCount(outFile, { defaultFps: DEFAULT_FPS });
    }
    setChunkArtifactStatus(root, rel, "finished", publishedFrames);
    results.push({ videoKey, ok: true, frames: publishedFrames });
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
