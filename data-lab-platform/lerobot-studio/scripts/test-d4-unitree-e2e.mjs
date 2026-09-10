#!/usr/bin/env node
/**
 * D4 acceptance: Unitree fake ingest + writeback skip + ego API.
 */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { robotFeaturesFromModalities } from "../lerobot-converter.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BUILD = path.join(__dirname, "build-unitree-fake-dataset.mjs");
const PY = process.env.PYTHON || "python3";
const EGO_HAND_PIPELINE =
  process.env.EGO_HAND_PIPELINE_ROOT ||
  path.resolve(__dirname, "../../../../ego-hand-pipeline");
const EGO_BACKEND = path.resolve(__dirname, "../../embodied-annotate/backend");
const TMP_ROOT = path.resolve(__dirname, "../../stream-data/.d4-tmp");

const HAND_KEYS = [
  "observation.hand_pose_left",
  "observation.hand_pose_right",
  "observation.hand_conf_left",
  "observation.hand_conf_right",
];

const ROBOT_FEATURE_KEYS = [
  "observation.joint_pos",
  "observation.joint_vel",
  "observation.ee_pose_left",
  "observation.ee_pose_right",
  "action.ee_target_pose_left",
  "action.ee_target_pose_right",
];

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function sha256File(p) {
  if (!fs.existsSync(p)) return null;
  return crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
}

function runNode(script, args, env = {}) {
  const r = spawnSync(process.execPath, [script, ...args], {
    encoding: "utf8",
    env: { ...process.env, ...env },
  });
  assert(r.status === 0, `${script} failed: ${r.stderr || r.stdout}`);
  return r.stdout;
}

function pyExpr(code) {
  const r = spawnSync(PY, ["-c", code], { encoding: "utf8" });
  assert(r.status === 0, r.stderr || r.stdout);
  return r.stdout.trim();
}

function testBuildMinimalDataset(streamRoot) {
  const stationRoot = path.join(streamRoot, "unitree-g1-teleop-001");
  if (fs.existsSync(stationRoot)) {
    fs.rmSync(stationRoot, { recursive: true, force: true });
  }
  runNode(BUILD, [streamRoot, "--fresh"]);
  assert(fs.existsSync(path.join(stationRoot, "meta", "info.json")), "info.json");
  assert(
    fs.existsSync(path.join(stationRoot, "meta", "episodes", "chunk-000", "file-000.parquet")),
    "episodes.parquet",
  );
  assert(
    fs.existsSync(path.join(stationRoot, "meta", "annotations.parquet")),
    "annotations.parquet",
  );
  const dataParquet = path.join(stationRoot, "data", "chunk-000", "file-000.parquet");
  assert(!fs.existsSync(dataParquet), "data.parquet should be absent in minimal mode");
  console.log("✓ minimal dataset structure");
  return stationRoot;
}

function testInfoFeatures(stationRoot) {
  const info = JSON.parse(fs.readFileSync(path.join(stationRoot, "meta", "info.json"), "utf8"));
  const feats = info.features || {};
  for (const key of ROBOT_FEATURE_KEYS) {
    assert(key in feats, `missing feature ${key}`);
  }
  for (const key of HAND_KEYS) {
    assert(!(key in feats), `hand feature must not exist: ${key}`);
  }
  const manifest = JSON.parse(
    fs.readFileSync(
      path.resolve(__dirname, "../../ego-stream-client/samples/manifest-unitree-g1-teleop.json"),
      "utf8",
    ),
  );
  const expected = robotFeaturesFromModalities(manifest.modalities);
  for (const key of Object.keys(expected)) {
    assert(key in feats, `manifest feature missing in info.json: ${key}`);
  }
  console.log("✓ info.json robot features on-demand, no hand_pose");
}

function testEpisodesExtendedInfo(stationRoot) {
  const out = pyExpr(`
import json, pyarrow.parquet as pq
from pathlib import Path
root = Path(${JSON.stringify(stationRoot)})
t = pq.read_table(root / "meta/episodes/chunk-000/file-000.parquet")
row = {n: t[n][0].as_py() for n in t.column_names}
ext = json.loads(row["extended_info"])
assert row["embodiment"] == "robot_teleop_unitree_g1"
assert row["station_id"] == "unitree-g1-teleop-001"
assert ext.get("scene_id") == "lab_table_02"
assert ext.get("robot_sn") == "G1-2024-0876"
print("ok")
`);
  assert(out === "ok", out);
  console.log("✓ episodes.parquet metadata + extended_info");
}

function testWritebackSkips(stationRoot) {
  const cli = path.join(EGO_HAND_PIPELINE, "src", "ego_hand_pipeline", "cli", "lerobot_writeback.py");
  assert(fs.existsSync(cli), `writeback CLI not found: ${cli}`);
  const r = spawnSync(
    PY,
    [cli, "--dataset-path", stationRoot, "--episode-index", "0", "--hamer-backend", "stub"],
    {
      encoding: "utf8",
      cwd: EGO_HAND_PIPELINE,
      env: {
        ...process.env,
        PYTHONPATH: path.join(EGO_HAND_PIPELINE, "src"),
      },
    },
  );
  assert(r.status === 0, `writeback should exit 0: ${r.stderr || r.stdout}`);
  assert(
    (r.stderr || "").includes("跳过") || (r.stderr || "").includes("非人类"),
    "writeback should report skip for non-human dataset",
  );
  console.log("✓ ehp-lerobot-writeback auto-skip (exit 0)");
}

function resolveEgoPython() {
  const venvPy = path.join(EGO_BACKEND, ".venv", "bin", "python");
  if (fs.existsSync(venvPy)) return venvPy;
  return PY;
}

function testEgoApi(stationRoot) {
  const egoPy = resolveEgoPython();
  const code = `
import json
import sys
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(EGO_BACKEND)})
from fastapi.testclient import TestClient
from app import app

root = ${JSON.stringify(stationRoot)}
client = TestClient(app)

load = client.get("/api/ego/load", params={"datasetPath": root, "episodeIndex": 0})
assert load.status_code == 200, load.text
body = load.json()
assert body["episode_meta"]["embodiment"] == "robot_teleop_unitree_g1"
assert "observation.joint_pos" in body["features"]
assert "observation.hand_pose_left" not in body["features"]
assert body["total_frames"] > 0

hp = client.get("/api/ego/hand_poses", params={"datasetPath": root, "episodeIndex": 0})
assert hp.status_code == 200
assert hp.json()["poses"] == []

save = client.post("/api/ego/save", json={
    "dataset_path": root,
    "episode_index": 0,
    "subtasks": [
        {"start_frame": 0, "end_frame": 14, "subtask_index": 0, "subtask_name": "approach"},
        {"start_frame": 15, "end_frame": 29, "subtask_index": 1, "subtask_name": "grasp"},
    ],
})
assert save.status_code == 200, save.text
assert save.json()["updated_rows"] == 2
print("ok")
`;
  const r = spawnSync(egoPy, ["-c", code], {
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: EGO_BACKEND },
  });
  assert(r.status === 0, r.stderr || r.stdout);
  assert(r.stdout.trim() === "ok", r.stdout);
  console.log("✓ /api/ego/load + save on robot dataset");
}

function testWithIngestColumns(streamRoot) {
  const ingestRoot = path.join(streamRoot, "unitree-g1-teleop-001-ingest");
  const ingestStream = path.join(streamRoot, ".ingest-parent");
  fs.mkdirSync(ingestStream, { recursive: true });
  // Use a dedicated stream root so station id folder does not collide.
  const fakeStation = path.join(ingestStream, "unitree-g1-teleop-001");
  if (fs.existsSync(fakeStation)) {
    fs.rmSync(fakeStation, { recursive: true, force: true });
  }
  runNode(BUILD, [ingestStream, "--fresh", "--with-ingest"], { D4_FAKE_FRAMES: "5" });
  const dataParquet = path.join(fakeStation, "data", "chunk-000", "file-000.parquet");
  assert(fs.existsSync(dataParquet), "with-ingest should create data.parquet");
  const cols = pyExpr(`
import pyarrow.parquet as pq
from pathlib import Path
t = pq.read_table(Path(${JSON.stringify(dataParquet)}))
cols = set(t.column_names)
for k in ${JSON.stringify(ROBOT_FEATURE_KEYS)}:
    assert k in cols, f"missing data col {k}"
for k in ${JSON.stringify(HAND_KEYS)}:
    assert k not in cols, f"hand col present {k}"
print(t.num_rows)
`);
  assert(Number(cols) === 5, `expected 5 data rows, got ${cols}`);
  fs.rmSync(ingestStream, { recursive: true, force: true });
  console.log("✓ with-ingest: joint/ee data cols present, hand cols absent");
}

function main() {
  ensureTmp();
  const streamRoot = TMP_ROOT;
  const stationRoot = testBuildMinimalDataset(streamRoot);
  testInfoFeatures(stationRoot);
  testEpisodesExtendedInfo(stationRoot);
  testWritebackSkips(stationRoot);
  testEgoApi(stationRoot);
  testWithIngestColumns(streamRoot);

  console.log("\nD4 E2E OK");
}

function ensureTmp() {
  fs.mkdirSync(TMP_ROOT, { recursive: true });
}

main();
