# HaMeR Butterworth 手部时序后处理 — 测试分支说明

测试分支：`test/hamer-joints-bf-postprocess`（data-lab + ego-platform 兄弟仓库各一份）

## 改了什么（局部替换）

对照《HaMeR单帧手部估计部署文档》中的 **时序后处理** 部分（`postprocess_joints.py`），**仅替换** oak 手部 pipeline 里每侧 MANO 轨迹的 **时序平滑** 步骤：

| 环节 | main / release 默认 | 本测试分支 |
|------|---------------------|------------|
| 单帧检测 | MediaPipe hand box | **不变** |
| 单帧 HaMeR | geopavlakos crop 推理 | **不变** |
| 时序平滑 | One-Euro on MANO pose | **缺帧插值 + 腕部跳变剔除 + Butterworth** |
| 文档中的全图 ViTPose/regnety 检测 | — | **未接入**（架构不同，后续可单开实验） |

main 分支默认行为不受影响：`EGO_HANDS_TEMPORAL_SMOOTH` 默认为 `oneeuro`。

## 前置条件

1. **ego-platform** 切到同名校验分支：

```bash
cd ../ego-platform
git checkout test/hamer-joints-bf-postprocess
```

2. **HaMeR / MANO** 与现有 pipeline 相同（`ego-hand-pipeline/third_party/hamer` + `MANO_RIGHT.pkl`）。

3. 采集数据已 ingest，可正常跑 `ego-run-pipeline`。

## 跑测试

```bash
# 推荐：专用包装脚本（自动设置 Butterworth 环境变量）
chmod +x data-lab-platform/scripts/ego-run-pipeline-hamer-bf-test
data-lab-platform/scripts/ego-run-pipeline-hamer-bf-test ego-001

# 或手动导出环境变量后走原命令
export EGO_HANDS_TEMPORAL_SMOOTH=butterworth
export EGO_HANDS_BF_FS=30
export EGO_HANDS_BF_LOWCUT=8.0      # 文档建议 fast motion 可试 10–12
export EGO_HANDS_BF_DELTA=12        # 快速动作可降到 ~10
export EGO_HANDS_BF_OUTLIER_3D=0.12
export EGO_HANDS_BF_OUTLIER_2D=80
EGO_HAMER_BACKEND=geopavlakos data-lab-platform/scripts/ego-run-pipeline ego-001
```

强制重跑手部（清 kp2d sidecar 后重 infer）：

```bash
EGO_HANDS_FORCE=1 data-lab-platform/scripts/ego-run-pipeline-hamer-bf-test ego-001
```

## 验证输出

- `offline/episode_*/infer_manifest.json` 中应出现 `"temporal_smooth": "butterworth"`。
- Viewer 中 `mano_kp2d_uv.*.json` overlay 应与平滑后轨迹一致。
- 对比 main：同一 session 在 One-Euro vs Butterworth 下 overlay 抖动/拖影差异。

## 调参（与文档一致）

| 变量 | 含义 | 默认 |
|------|------|------|
| `EGO_HANDS_BF_FS` | 帧率 | 30 |
| `EGO_HANDS_BF_LOWCUT` | 低通截止 Hz，范围约 `[1, fps/2]` | 8.0 |
| `EGO_HANDS_BF_DELTA` | Butterworth 滑窗长度（帧） | 12 |
| `EGO_HANDS_BF_OUTLIER_3D` | 3D 腕部速度阈值 (m/frame) | 0.12 |
| `EGO_HANDS_BF_OUTLIER_2D` | 2D 腕部速度阈值 (px/frame) | 80 |

## 回到 main 默认流程

```bash
# data-lab
git checkout release/v0.0.12   # 或 main

# ego-platform
cd ../ego-platform && git checkout main
```

不设置 `EGO_HANDS_TEMPORAL_SMOOTH` 即恢复 One-Euro 平滑。
