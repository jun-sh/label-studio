#!/usr/bin/env node
/**
 * D4: Build minimal Unitree G1 teleop fake dataset via ingest + converter.
 *
 * Default (--minimal): session_start (converter bootstraps info.json) → episode index
 * → stub video → meta-only parquet sync → no data.parquet rows.
 *
 * Usage:
 *   node scripts/build-unitree-fake-dataset.mjs [station_root] [--with-ingest]
 *
 * station_root defaults to stream-data/unitree-g1-teleop-001 (under STREAM_DATA_ROOT parent).
 */
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import {
  attachEpisodeMetaToIndexEntry,
  prepareSegmentEpisodeMeta,
} from "../lerobot-converter.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const STATION_ID = "unitree-g1-teleop-001";
const MANIFEST_PATH = path.resolve(
  __dirname,
  "../../ego-stream-client/samples/manifest-unitree-g1-teleop.json",
);
const SYNC_SCRIPT = path.join(__dirname, "sync-stream-parquet.py");
const PY = process.env.PYTHON || "python3";
const VIDEO_KEY = "observation.images.camera_front_left";
const N_FRAMES = Number(process.env.D4_FAKE_FRAMES || 30);

/** Minimal valid JPEG (1x1). */
const MINI_JPEG = Buffer.from(
  "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAn/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCwAB//2Q==",
  "base64",
);

function parseArgs(argv) {
  const withIngest = argv.includes("--with-ingest");
  const fresh = argv.includes("--fresh");
  const positional = argv.filter((a) => !a.startsWith("--"));
  const defaultStreamRoot = path.resolve(__dirname, "../../stream-data");
  return {
    streamRoot: path.resolve(positional[0] || defaultStreamRoot),
    withIngest,
    fresh,
  };
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function writeJson(p, obj) {
  ensureDir(path.dirname(p));
  fs.writeFileSync(p, `${JSON.stringify(obj, null, 2)}\n`, "utf8");
}

function segmentImageKey(frameIndex, videoKey) {
  return `${frameIndex}__${videoKey.replace(/\./g, "_")}`;
}

async function waitMs(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

function writeEpisodesIndex(stationRoot, sessionId, segmentId, episodeMeta, length) {
  const entry = attachEpisodeMetaToIndexEntry(
    {
      episode_index: 0,
      segment_id: segmentId,
      session_id: sessionId,
      dataset_from_index: 0,
      dataset_to_index: length,
      length,
      source: "d4_fake",
      title: `Unitree fake · ${length}f`,
      committed_at: new Date().toISOString(),
    },
    episodeMeta,
  );
  writeJson(path.join(stationRoot, "live", "episodes-index.json"), {
    version: 1,
    episodes: [entry],
  });
  writeJson(path.join(stationRoot, "live", "episode-meta.json"), episodeMeta);
  return entry;
}

function createStubVideo(stationRoot, fps, nFrames) {
  const out = path.join(
    stationRoot,
    "videos",
    VIDEO_KEY,
    "chunk-000",
    "file-000.mp4",
  );
  ensureDir(path.dirname(out));
  const duration = Math.max(0.1, nFrames / fps);
  const ff = spawnSync(
    "ffmpeg",
    [
      "-y",
      "-f",
      "lavfi",
      "-i",
      `color=c=black:s=640x480:d=${duration}`,
      "-vf",
      `fps=${fps}`,
      "-c:v",
      "libx264",
      "-preset",
      "ultrafast",
      "-pix_fmt",
      "yuv420p",
      out,
    ],
    { encoding: "utf8" },
  );
  if (ff.status !== 0) {
    console.warn("⊘ ffmpeg unavailable, skipping stub mp4:", ff.stderr?.slice(0, 200));
    return false;
  }
  return fs.existsSync(out);
}

function runParquetSync(stationRoot, metaOnly) {
  const args = metaOnly ? ["--meta-only", stationRoot] : [stationRoot];
  const r = spawnSync(PY, [SYNC_SCRIPT, ...args], { encoding: "utf8" });
  if (r.status !== 0) {
    throw new Error(`sync-stream-parquet failed: ${r.stderr || r.stdout}`);
  }
}

async function main() {
  const { streamRoot, withIngest, fresh } = parseArgs(process.argv.slice(2));
  const stationRoot = path.join(streamRoot, STATION_ID);
  process.env.STREAM_DATA_ROOT = streamRoot;

  if (fresh && fs.existsSync(stationRoot)) {
    fs.rmSync(stationRoot, { recursive: true, force: true });
  }

  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
  const episodeMeta = prepareSegmentEpisodeMeta(manifest, STATION_ID);
  const sessionId = manifest.session_id;
  const segmentId = manifest.segment_id;

  const { handleStreamUpload } = await import("../stream-ingest.mjs");

  const start = handleStreamUpload(STATION_ID, {
    action: "session_start",
    sessionId,
    task: "Unitree G1 teleop · D4 fake",
    videoShapes: { [VIDEO_KEY]: [480, 640] },
    episodeMeta,
  });
  if (!start?.ok) throw new Error("session_start failed");

  const infoPath = path.join(stationRoot, "meta", "info.json");
  const info = JSON.parse(fs.readFileSync(infoPath, "utf8"));
  const fps = Number(info.fps || 30);

  if (withIngest) {
    const images = {};
    const frames = [];
    for (let i = 0; i < N_FRAMES; i += 1) {
      frames.push({ frameIndex: i, timestampNs: i * Math.round(1e9 / fps) });
      images[segmentImageKey(i, VIDEO_KEY)] = MINI_JPEG;
    }
    const seg = handleStreamUpload(STATION_ID, {
      action: "segment",
      sessionId,
      segmentId,
      frames,
      images,
      endFrameIndex: N_FRAMES - 1,
      episodeMeta,
      ingestSource: "d4_fake",
    });
    if (!seg?.ok) throw new Error("segment upload failed");
    await waitMs(800);
    runParquetSync(stationRoot, false);
  } else {
    info.total_frames = N_FRAMES;
    info.total_episodes = 1;
    fs.writeFileSync(infoPath, `${JSON.stringify(info, null, 2)}\n`, "utf8");
    writeEpisodesIndex(stationRoot, sessionId, segmentId, episodeMeta, N_FRAMES);
    createStubVideo(stationRoot, fps, N_FRAMES);
    runParquetSync(stationRoot, true);
    const dataParquet = path.join(stationRoot, "data", "chunk-000", "file-000.parquet");
    if (fs.existsSync(dataParquet)) {
      fs.unlinkSync(dataParquet);
    }
  }

  console.log(JSON.stringify({
    ok: true,
    stationId: STATION_ID,
    stationRoot,
    mode: withIngest ? "with-ingest" : "minimal",
    totalFrames: N_FRAMES,
    manifest: MANIFEST_PATH,
  }, null, 2));
  process.exit(0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
