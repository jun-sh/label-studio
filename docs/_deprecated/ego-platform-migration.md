# Ego Platform 分块改造计划

> **原则**：旧链路持续可用；新链路按块交付、可灰度、可回滚。  
> **目标**：EgoVerse 式 `Extractor → LeRobotWriter → Registry → Corpus`，LeRobot v3.0 为唯一落盘格式。

## 为什么必须分块

| 风险 | 分块对策 |
|------|----------|
| 一次替换导致 214→Viewer 全断 | B6 前 `legacy` 默认；回滚仍可用 `EGO_PIPELINE_BACKEND=legacy` |
| hand pose / VIO 算法周期长 | 算法块独立交付，Writer/Registry 先稳定 |
| 商业验收需要可审计 | 每块有 `.done` 标记、Registry 状态、单测 |
| 数据不可丢 | `corpus/` append 幂等；旧 `ego-hand-pipeline` 并行保留至 P3 |

## 分块路线图

| 块 | 代号 | 交付物 | 验收 | 依赖 | 状态 |
|----|------|--------|------|------|------|
| **B0** | 骨架 | `ego-platform` 包、Registry、LeRobotWriter、测试 | `pytest` 绿 | 无 | ✅ |
| **B1** | 接入 | corpus append + hand_kp2d + oak 管线 | 双 session 累加 | B0 | ✅ |
| **B2** | 几何 | `rectify` | rectified mp4 + meta | B1 | ✅ |
| **B3** | 手部 | detect → HaMeR → 126D | 126D 双手有效率 ≥85% | B2 | ✅ |
| **B4** | 运动 | `track` / VIO | pose 对齐（IMU 朝向 v1） | B2 | ✅ |
| **B5** | 语义 | `language_annotate` | annotations.jsonl | B1 | ✅ |
| **B6** | 下线 | 默认 `backend=oak` | 废弃 legacy 主路径 | B3+B4+B5 | ✅ |

## 进度快照（2026-07-08）

- [x] B0 Registry + LeRobotWriter + pytest
- [x] B1 corpus append + overlay export + `ego-run-pipeline` oak 接入
- [x] B2 rectify（部分复用 legacy rectified）
- [x] B3 hands 验收：`ego-platform-acceptance.sh`（126D 双手有效率 ≥85% + overlay）
- [x] B4 track：`EGO_OAK_TRACK_MODE=vio` IMU 朝向积分 → `observation.pose`（episode-local，frame0=identity）
- [x] B5 language_annotate：`EGO_OAK_LANGUAGE_MODE=auto` → `offline/episode_XXX/annotations.jsonl`（parquet 优先，否则 task 模板）
- [x] B6 默认 `EGO_PIPELINE_BACKEND=oak`（`hands` + `vio` + `language_annotate`）

**B0–B6 已全部交付**（2026-07-08）。发布说明见 [ego-platform-release-2026-07-08.md](./ego-platform-release-2026-07-08.md)。

**运维**：已发布但未 `finalize.done` 的 session 可执行：

```bash
ego-oak-convert --station ego-lan-214 --session sess_... --finalize-only
```

## 切换开关

```bash
# 现网（B6 默认）
ego-run-pipeline ego-lan-214

# 等价显式配置
EGO_PIPELINE_BACKEND=oak \
EGO_OAK_MODE=hands \
EGO_OAK_TRACK_MODE=vio \
EGO_OAK_LANGUAGE_MODE=auto \
ego-run-pipeline ego-lan-214

# 回滚旧管线
EGO_PIPELINE_BACKEND=legacy ego-run-pipeline ego-lan-214
```

## 目录约定（34 平台）

```text
data-storage/
├── registry/episodes.db      # Episode Registry（B0）
├── corpus/ego_214_hand_pose/ # LeRobot v3 母库（B1+）
├── raw/ego-lan-214/          # 原始段（B1+，逐步替代 stream 双写）
└── samples/                  # 发布 zip（不变）
```

## 块间契约

1. **Registry 先行**：每个 session 必须先 `register` 再 `convert`
2. **Writer 唯一**：LeRobot v3 只通过 `ego_platform.lerobot.writer.LeRobotWriter` 写入
3. **算法不直写磁盘**：`rectify/track/hands` 只产出内存特征 dict，由 Writer 统一落盘
4. **幂等**：`episode_hash` 去重；`processing_status=done` 则 skip

## 回滚

任意块出问题：

1. `export EGO_PIPELINE_BACKEND=legacy`
2. 重新 `ego-run-pipeline`（旧 `ego-postprocess.sh` 仍可用）
3. Registry 中失败行标 `processing_error`，不污染 corpus

## 相关仓库

- 新管线：`../ego-platform/`
- 旧管线（回滚用）：`../ego-hand-pipeline/`
- 发布说明：[ego-platform-release-2026-07-08.md](./ego-platform-release-2026-07-08.md)
- 平台 CLI：`data-lab-platform/scripts/ego-run-pipeline`
