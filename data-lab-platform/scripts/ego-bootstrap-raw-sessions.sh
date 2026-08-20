#!/usr/bin/env bash
# Bootstrap DERIVE_PENDING + DONE_UPLOAD from preserved raw/segments/*.tar.zst after 34 reset.
set -euo pipefail

STATION="${STATION_ID:-ego-001}"
INGEST="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"

log() { echo "[bootstrap-raw] $*"; }
die() { echo "[bootstrap-raw] ERROR: $*" >&2; exit 1; }

docker ps --format '{{.Names}}' | grep -q "^${INGEST}$" || die "container ${INGEST} not running"

log "bootstrapping ${STATION} from raw/segments"
docker exec "${INGEST}" node --input-type=module -e "
import fs from 'node:fs';
import path from 'node:path';
import { bootstrapDatasetSchema } from '/app/lerobot-converter.mjs';
import { videoKeysForStation } from '/app/ingest/staging-materialize.mjs';
import { transitionSegmentState, SEGMENT_INGEST_STATUS } from '/app/ingest/segment-state.mjs';
import { markSessionUploadDone } from '/app/session-markers.mjs';
import { ensureDir, writeJsonAtomic } from '/app/ingest/io.mjs';
import { readManifestFromArchive } from '/app/derive/frame-map.mjs';

const stationId = '${STATION}';
const root = '/srv/stream/' + stationId;
const rawRoot = path.join(root, 'raw', 'segments');
if (!fs.existsSync(rawRoot)) throw new Error('missing raw/segments');

const features = {};
for (const key of videoKeysForStation(stationId)) {
  features[key] = {
    dtype: 'video',
    shape: [1200, 1920, 3],
    names: ['height', 'width', 'channels'],
    info: {
      'video.height': 1200,
      'video.width': 1920,
      'video.codec': 'h264',
      'video.pix_fmt': 'yuv420p',
      'video.is_depth_map': false,
      'video.fps': 30,
      'video.channels': 3,
      has_audio: false,
    },
  };
}
features['observation.state'] = { dtype: 'float32', shape: [6], names: ['gx','gy','gz','ax','ay','az'] };
features['observation.pose'] = { dtype: 'float32', shape: [7], names: ['x','y','z','qx','qy','qz','qw'] };
features['observation.hands'] = { dtype: 'float32', shape: [63], names: null };
features.action = { dtype: 'float32', shape: [1], names: null };
features['observation.imu_accel'] = { dtype: 'float32', shape: [3], names: ['x','y','z'] };
features['observation.imu_gyro'] = { dtype: 'float32', shape: [3], names: ['x','y','z'] };
features['observation.imu_timestamp'] = { dtype: 'float64', shape: [1], names: null };

const infoPath = path.join(root, 'meta', 'info.json');
if (!fs.existsSync(infoPath)) {
  const info = bootstrapDatasetSchema({
    codebase_version: 'v3.0',
    robot_type: 'oak_4p_ego',
    total_episodes: 1,
    total_frames: 0,
    total_tasks: 1,
    chunks_size: 1000,
    fps: 30,
    splits: { train: '0:1' },
    data_path: 'data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet',
    video_path: 'videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4',
    features,
  }, null);
  ensureDir(path.join(root, 'meta'));
  writeJsonAtomic(infoPath, info);
}

let sessions = 0;
let segments = 0;
for (const sessionId of fs.readdirSync(rawRoot).sort()) {
  const sessDir = path.join(rawRoot, sessionId);
  if (!fs.statSync(sessDir).isDirectory()) continue;
  const archives = fs.readdirSync(sessDir).filter((n) => n.endsWith('.tar.zst')).sort();
  if (!archives.length) continue;
  for (const archive of archives) {
    const segmentId = archive.replace(/\\.tar\\.zst$/, '');
    const archivePath = path.join(sessDir, archive);
    const manifest = readManifestFromArchive(archivePath);
    const frameCount = Number(manifest?.frame_count || manifest?.frameCount || 0);
    if (frameCount <= 0) throw new Error(sessionId + '/' + segmentId + ': frame_count=0');
    transitionSegmentState(root, sessionId, segmentId, SEGMENT_INGEST_STATUS.DERIVE_PENDING, {
      frame_count: frameCount,
      integrity: { ok: true },
    });
    segments += 1;
  }
  markSessionUploadDone(root, sessionId, { stationId, source: 'bootstrap-raw' });
  sessions += 1;
}
console.log(JSON.stringify({ ok: true, sessions, segments }));
"

log "done"
