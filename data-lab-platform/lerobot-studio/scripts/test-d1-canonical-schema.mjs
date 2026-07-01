#!/usr/bin/env node
/**
 * D1 smoke: canonical schema + parquet sync (no full tar.zst required).
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

import {
  bootstrapDatasetSchema,
  buildCanonicalFrameRow,
  mergeCanonicalFeatures,
  parseManifestToEpisodeMeta,
  robotFeaturesFromModalities,
} from "../lerobot-converter.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "../../stream-data/ego-lan-214");
const SYNC = path.join(__dirname, "sync-stream-parquet.py");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function testManifestParsing() {
  const human = JSON.parse(
    fs.readFileSync(
      path.resolve(__dirname, "../../ego-stream-client/samples/manifest-human-demo.json"),
      "utf8",
    ),
  );
  const meta = parseManifestToEpisodeMeta(human, "ego-lan-214");
  assert(meta.station_id === "ego-lan-214", "human station_id");
  assert(meta.embodiment === "human_demo", "human embodiment");
  assert(meta.task_id === "fold_towel_001", "human task_id");
  const ext = JSON.parse(meta.extended_info);
  assert(ext.scene_id === "kitchen_table_01", "extended_info scene");

  const unitree = JSON.parse(
    fs.readFileSync(
      path.resolve(__dirname, "../../ego-stream-client/samples/manifest-unitree-g1-teleop.json"),
      "utf8",
    ),
  );
  const umeta = parseManifestToEpisodeMeta(unitree, "unitree-g1-teleop-001");
  assert(umeta.embodiment === "robot_teleop_unitree_g1", "unitree embodiment");
  const ufeat = robotFeaturesFromModalities(umeta.modalities);
  assert("observation.joint_pos" in ufeat, "unitree joint_pos feature");
  assert(!("observation.hand_pose_left" in ufeat), "unitree no hand_pose");
  console.log("✓ manifest parsing");
}

function testHumanFeatures() {
  const info = bootstrapDatasetSchema(
    { features: { "observation.state": { dtype: "float32", shape: [6] } } },
    parseManifestToEpisodeMeta({ embodiment: "human_demo" }, "ego-lan-214"),
  );
  assert("observation.hand_pose_left" in info.features, "hand_pose_left registered");
  assert("observation.head_pose" in info.features, "head_pose registered");
  const row = buildCanonicalFrameRow({ frame_index: 0 }, info);
  assert(row["observation.hand_pose_left"].length === 63, "hand_pose_left zeros");
  console.log("✓ human feature registration");
}

function testStreamDataParquetSync() {
  if (!fs.existsSync(path.join(ROOT, "meta", "info.json"))) {
    console.log("⊘ skip stream-data sync (no sample at", ROOT, ")");
    return;
  }
  const py = process.env.PYTHON || "python3";
  const r = spawnSync(py, [SYNC, ROOT], { encoding: "utf8" });
  assert(r.status === 0, `sync failed: ${r.stderr || r.stdout}`);
  const ann = path.join(ROOT, "meta", "annotations.parquet");
  const ep = path.join(ROOT, "meta", "episodes", "chunk-000", "file-000.parquet");
  assert(fs.existsSync(ann), "annotations.parquet exists");
  assert(fs.existsSync(ep), "episodes.parquet exists");
  const checkAnn = spawnSync(
    py,
    [
      "-c",
      `import pyarrow.parquet as pq; t=pq.read_table(${JSON.stringify(ann)}); assert "episode_index" in t.column_names, t.column_names`,
    ],
    { encoding: "utf8" },
  );
  assert(checkAnn.status === 0, `annotations schema: ${checkAnn.stderr || checkAnn.stdout}`);
  console.log("✓ parquet sync on stream-data sample");
}

function main() {
  testManifestParsing();
  testHumanFeatures();
  testStreamDataParquetSync();
  console.log("\nD1 smoke OK");
}

main();
