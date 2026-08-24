# 行业标准 Ego 手部 Pipeline — 测试分支

分支：`test/ego-standard-hand-pipeline`（data-lab + ego-platform）

在 **main 默认流程不变** 的前提下，通过 `EGO_HANDS_STANDARD=1` 启用五阶段标准管线。

## 五阶段映射

| 阶段 | 标准能力 | 本分支实现 | main 默认 |
|------|----------|------------|-----------|
| 1 预处理 | rectify + 帧对齐 + CLAHE + 内参 | rectify/内参 **已有**；CLAHE **新增**（`EGO_HANDS_CLAHE`） | 无 CLAHE |
| 2 检测跟踪 | MediaPipe + Hand-ID + 遮挡状态 | MediaPipe **已有**；Hand-ID/遮挡 **新增** | 每帧重检 |
| 3 HMR | WiLoR 优先 / HaMeR 原型 | `EGO_HMR_BACKEND=hamer\|wilor`（官方 repo 或 wilor-mini；无权重时回退 HaMeR） | HaMeR only |
| 4 后处理 | 遮挡硬规则 + 低通 + 双目 + 拓扑 + B3 | **新增** occlusion-hard / topology / B3 strict 可选 | One-Euro + repair 插值 |
| 5 落盘 | Parquet MANO + valid + 遮挡标签 | **新增** `offline/episode_*/mano_pose.parquet` | JSON sidecar + npz |

## 快速开始

```bash
# ego-platform
cd ../ego-platform && git checkout test/ego-standard-hand-pipeline

# data-lab
cd ../data-lab
chmod +x data-lab-platform/scripts/ego-run-pipeline-standard
data-lab-platform/scripts/ego-run-pipeline-standard ego-001
```

强制重跑：

```bash
EGO_HANDS_FORCE=1 data-lab-platform/scripts/ego-run-pipeline-standard ego-001
```

## 关键环境变量

| 变量 | 默认（standard 模式） | 说明 |
|------|----------------------|------|
| `EGO_HANDS_STANDARD` | `1`（脚本内） | 总开关 |
| `EGO_HANDS_CLAHE` | `1` | 暗光 CLAHE |
| `EGO_HANDS_TRACK` | `1` | Hand-ID + 左右互换纠正 |
| `EGO_HMR_BACKEND` | `hamer` | 显式设 `wilor` 时需 `WILOR_ROOT`；`EGO_HMR_STRICT=1` 强制 WiLoR |
| `WILOR_ROOT` / `WILOR_IMPL` | — | 见下方 WiLoR 部署 |
| `EGO_HANDS_OCCLUSION_HARD` | `1` | LOST 帧不插值、MANO 置空 |
| `EGO_HANDS_TOPOLOGY` | `1` | 骨骼长度/跨度畸形过滤 |
| `EGO_HANDS_WRITE_PARQUET` | `1` | 写 `mano_pose.parquet` |
| `EGO_B3_STRICT` | **`0`** | `1` 时 B3 不通过则 convert 失败（标定后再开） |

## 验证

- `offline/episode_*/infer_manifest.json` → `"standard_pipeline": true`
- `offline/episode_*/mano_pose.parquet` → 含 `occlusion_left/right`, `valid_*`, `hand_id_*`
- `mediapipe_kp2d_uv.*.json` → `joint_visibility` 每帧 21 维（MediaPipe 原生 visibility）

- `.status/hands.json` → `quality.b3_passed`（`EGO_B3_STRICT=1` 时不通过会 fail convert）

## WiLoR 部署

```bash
# 方式 A：官方仓库
git clone https://github.com/rolpotamias/WiLoR.git ../ego-hand-pipeline/third_party/wilor
cd ../ego-hand-pipeline/third_party/wilor
# 下载 pretrained_models/wilor_final.ckpt + model_config.yaml + mano_data/MANO_RIGHT.pkl
export WILOR_ROOT=/path/to/wilor
export WILOR_IMPL=repo

# 或一键克隆仓库（权重仍需手动下载）：
../ego-hand-pipeline/scripts/setup_wilor.sh

# 方式 B：wilor-mini（自动下权重）
pip install git+https://github.com/warmshao/WiLoR-mini
export WILOR_IMPL=mini
```

## 回到 main

```bash
git checkout release/v0.0.12   # data-lab
cd ../ego-platform && git checkout main
```

不设置 `EGO_HANDS_STANDARD` 即恢复原有行为。
