#!/usr/bin/env bash
# B0–B6 acceptance gate for ego-platform (run before oak production).
set -euo pipefail

WORKSPACE="$(cd "$(dirname "$0")/../../.." && pwd)"
EGO_PLATFORM="${WORKSPACE}/ego-platform"
DATALAB="${WORKSPACE}/data-lab"
STATION="${EGO_STATION:-ego-lan-214}"

echo "==> ego-platform pytest"
(cd "${EGO_PLATFORM}" && python3 -m pytest -q)

echo "==> hand overlay regression"
"${DATALAB}/data-lab-platform/scripts/test-hand-overlay.sh"

echo "==> B3 hands quality on corpus (if present)"
CORPUS="${DATALAB}/data-storage/corpus/ego_214_hand_pose"
if [[ -d "${CORPUS}/offline" ]]; then
  python3 << PY
import json
import sys
from pathlib import Path

from ego_platform.lerobot.schema import FRONT_VIDEO_KEYS
from ego_platform.oak.hands.constants import mano_kp2d_json_name
from ego_platform.oak.hands.quality import (
    B3_MIN_VALID_FRAME_RATIO,
    summarize_episode_kp2d_union,
    summarize_hands_quality,
    summarize_observation_hands_npz,
)

corpus = Path("${CORPUS}")
pipeline = Path("${DATALAB}") / "data-storage/pipeline/${STATION}"
ratios_both = []
union_ratios = []
for ep_dir in sorted(corpus.glob("offline/episode_*")):
    kp2d_paths = {}
    for vk in FRONT_VIDEO_KEYS:
        sidecar = ep_dir / mano_kp2d_json_name(vk)
        if sidecar.is_file():
            kp2d_paths[vk] = sidecar
    if not kp2d_paths:
        continue
    union = summarize_episode_kp2d_union(ep_dir, tuple(kp2d_paths.keys()))
    union_ratios.append(float(union.get("ratio_union_both") or 0.0))
    q = summarize_hands_quality(kp2d_paths)
    print(json.dumps({"episode": ep_dir.name, "quality": q, "union": union}, indent=2))

for npz in sorted(pipeline.glob("sess_*/work/hands/hands_features.npz")):
    stats = summarize_observation_hands_npz(npz)
    if stats.get("frames"):
        ratios_both.append(float(stats.get("ratio_both") or 0.0))
        print(json.dumps({"observation_npz": str(npz), "stats": stats}, indent=2))

mean_obs = sum(ratios_both) / len(ratios_both) if ratios_both else 0.0
mean_union = sum(union_ratios) / len(union_ratios) if union_ratios else 0.0
passed = mean_obs >= B3_MIN_VALID_FRAME_RATIO or mean_union >= B3_MIN_VALID_FRAME_RATIO
print(
    json.dumps(
        {
            "mean_observation_ratio_both": round(mean_obs, 4),
            "mean_union_ratio_both": round(mean_union, 4),
            "b3_min": B3_MIN_VALID_FRAME_RATIO,
            "b3_passed": passed,
        },
        indent=2,
    )
)
if not passed:
    print(
        f"FAIL: B3 dual-hand quality below {B3_MIN_VALID_FRAME_RATIO}",
        file=sys.stderr,
    )
    sys.exit(1)
PY
fi

echo "==> B4 track / pose quality on pipeline (if present)"
PIPELINE="${DATALAB}/data-storage/pipeline/${STATION}"
if [[ -d "${PIPELINE}" ]]; then
  python3 << PY
import json
import sys
from pathlib import Path

from ego_platform.oak.track.quality import B4_MIN_ORIENTATION_STD, summarize_pose_features_npz

pipeline = Path("${PIPELINE}")
stats_list = []
for npz in sorted(pipeline.glob("sess_*/work/track/pose_features.npz")):
    stats = summarize_pose_features_npz(npz)
    if stats.get("frames"):
        stats_list.append(stats)
        print(json.dumps({"pose_npz": str(npz), "stats": stats}, indent=2))

if not stats_list:
    print("skip: no pose_features.npz (run with EGO_OAK_TRACK_MODE=vio)")
else:
    mean_std = sum(float(s.get("orientation_std") or 0.0) for s in stats_list) / len(stats_list)
    passed = all(bool(s.get("b4_passed")) for s in stats_list)
    print(
        json.dumps(
            {
                "mean_orientation_std": round(mean_std, 6),
                "b4_min_orientation_std": B4_MIN_ORIENTATION_STD,
                "b4_passed": passed,
            },
            indent=2,
        )
    )
    if not passed:
        print("FAIL: B4 pose quality below threshold", file=sys.stderr)
        sys.exit(1)
PY
fi

echo "==> B5 language annotations on corpus (if present)"
if [[ -d "${CORPUS}/offline" ]]; then
  python3 << PY
import json
import sys
from pathlib import Path

from ego_platform.oak.language_annotate.quality import B5_MIN_COVERAGE_RATIO, summarize_annotations_jsonl
from ego_platform.oak.language_annotate.segments import episode_num_frames

corpus = Path("${CORPUS}")
stats_list = []
for ep_dir in sorted(corpus.glob("offline/episode_*")):
    ann = ep_dir / "annotations.jsonl"
    if not ann.is_file():
        print(json.dumps({"episode": ep_dir.name, "missing": "annotations.jsonl"}, indent=2))
        continue
    ep_idx = int(ep_dir.name.split("_")[-1])
    num_frames = episode_num_frames(corpus, ep_idx)
    stats = summarize_annotations_jsonl(ann, num_frames=num_frames)
    stats_list.append(stats)
    print(json.dumps({"episode": ep_dir.name, "stats": stats}, indent=2))

if not stats_list:
    print("FAIL: no annotations.jsonl in corpus offline episodes", file=sys.stderr)
    sys.exit(1)

passed = all(bool(s.get("b5_passed")) for s in stats_list)
print(
    json.dumps(
        {
            "episodes": len(stats_list),
            "b5_min_coverage": B5_MIN_COVERAGE_RATIO,
            "b5_passed": passed,
        },
        indent=2,
    )
)
if not passed:
    print("FAIL: B5 language annotation quality below threshold", file=sys.stderr)
    sys.exit(1)
PY
fi

echo "==> B6 default backend (oak production)"
python3 << PY
import os
import subprocess
import sys
from pathlib import Path

from ego_platform.pipeline_defaults import (
    DEFAULT_OAK_LANGUAGE_MODE,
    DEFAULT_OAK_MODE,
    DEFAULT_OAK_TRACK_MODE,
    DEFAULT_PIPELINE_BACKEND,
)

sessions_py = Path("${DATALAB}") / "data-lab-platform/scripts/ego-pipeline-sessions.py"
run_pipeline = Path("${DATALAB}") / "data-lab-platform/scripts/ego-run-pipeline"

checks = {
    "pipeline_defaults": DEFAULT_PIPELINE_BACKEND == "oak",
    "oak_mode": DEFAULT_OAK_MODE == "hands",
    "track_mode": DEFAULT_OAK_TRACK_MODE == "vio",
    "language_mode": DEFAULT_OAK_LANGUAGE_MODE == "auto",
    "ego_run_pipeline": "EGO_PIPELINE_BACKEND:-oak}" in run_pipeline.read_text(encoding="utf-8"),
    "ego_run_oak_mode": "EGO_OAK_MODE:-hands}" in run_pipeline.read_text(encoding="utf-8"),
}

env = os.environ.copy()
env.pop("EGO_PIPELINE_BACKEND", None)
proc = subprocess.run(
    [sys.executable, str(sessions_py), "pending", "ego-lan-214", "--datalab-root", "${DATALAB}"],
    capture_output=True,
    text=True,
    env=env,
)
checks["sessions_default_oak"] = proc.returncode == 0

import json
print(json.dumps({"b6_checks": checks, "b6_passed": all(checks.values())}, indent=2))
if not all(checks.values()):
    print("FAIL: B6 default backend not oak", file=sys.stderr)
    sys.exit(1)
PY

echo "==> B7 viewer publish gate (if samples deployed)"
SLUG="$(python3 "${DATALAB}/data-lab-platform/scripts/ego-pipeline-sessions.py" slug "${STATION}" --datalab-root "${DATALAB}" 2>/dev/null || echo "ego_214_hand_pose")"
HTTP_DATASET="${DATALAB}/data-storage/samples/${SLUG}/dataset"
if [[ -d "${HTTP_DATASET}" ]]; then
  bash "${DATALAB}/data-lab-platform/scripts/ego-viewer-publish-gate.sh" "${SLUG}"
fi

echo "==> oak finalize markers"
python3 "${DATALAB}/data-lab-platform/scripts/ego-pipeline-sessions.py" pending "${STATION}" \
  --datalab-root "${DATALAB}" --backend oak | while read -r sid; do
  echo "WARN: session missing finalize.done: ${sid}" >&2
done

echo "==> ego-platform acceptance OK"
