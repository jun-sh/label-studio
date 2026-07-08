# Ego Platform 分块改造计划

> **原则**：旧链路持续可用；新链路按块交付、可灰度、可回滚。  
> **目标**：EgoVerse 式 `Extractor → LeRobotWriter → Registry → Corpus`，LeRobot v3.0 为唯一落盘格式。

## 为什么必须分块

| 风险 | 分块对策 |
|------|----------|
| 一次替换导致 214→Viewer 全断 | `EGO_PIPELINE_BACKEND=legacy` 默认保持现网 |
| hand pose / VIO 算法周期长 | 算法块独立交付，Writer/Registry 先稳定 |
| 商业验收需要可审计 | 每块有 `.done` 标记、Registry 状态、单测 |
| 数据不可丢 | `corpus/` append 幂等；旧 `ego-hand-pipeline` 并行保留至 P3 |

## 分块路线图

| 块 | 代号 | 交付物 | 验收 | 依赖 |
|----|------|--------|------|------|
| **B0** | 骨架 | `ego-platform` 包、Registry、LeRobotWriter、测试 | `pytest` 绿；mock episode 写入合法 `meta/info.json` | 无 |
| **B1** | 接入 | corpus **append** 幂等 + hand_kp2d 导出 + `ego-run-pipeline` oak 灰度 | 双 session append 后 corpus 帧数累加 | B0 |
| **B2** | 几何 | `rectify` 鱼眼校正 | rectified mp4 + `K_rect` meta | B1 |
| **B3** | 手部 | `hands`: detect → HaMeR → stereo opt | `observation.hands` 126D 有效率 >85% | B2 |
| **B4** | 运动 | `track`: 双目 VIO → `observation.pose` | pose 与帧对齐 | B2 |
| **B5** | 语义 | `language_annotate` 并行 | `annotations.jsonl` span | B1 |
| **B6** | 下线 | 废弃 `ego-hand-pipeline` 主路径 | 默认 `backend=oak` | B3+B4 |

## 切换开关

```bash
# 现网（默认）
EGO_PIPELINE_BACKEND=legacy ego-run-pipeline ego-lan-214

# 新管线（灰度）
EGO_PIPELINE_BACKEND=oak ego-run-pipeline ego-lan-214
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
- 旧管线（B6 前保留）：`../ego-hand-pipeline/`
- 平台 CLI：`data-lab-platform/scripts/ego-run-pipeline`
