# ego-platform B0–B6 发布说明（2026-07-08）

## 摘要

`ego-run-pipeline` **默认切换为 oak 管线**。旧 `ego-hand-pipeline` 仍可通过 `EGO_PIPELINE_BACKEND=legacy` 回滚。

## 生产默认

```bash
ego-run-pipeline ego-lan-214
```

等价于：

| 变量 | 值 |
|------|-----|
| `EGO_PIPELINE_BACKEND` | `oak` |
| `EGO_OAK_MODE` | `hands` |
| `EGO_OAK_TRACK_MODE` | `vio` |
| `EGO_OAK_LANGUAGE_MODE` | `auto` |

## 管线步骤（oak）

```text
precheck → rectify → track → language → hands → extract → write_lerobot → append → publish → finalize
```

产物：

- **Registry**：`data-storage/registry/episodes.db`
- **Session 工作区**：`data-storage/pipeline/<station>/<session>/`
- **母库**：`data-storage/corpus/ego_214_hand_pose/`
- **Viewer zip**：`data-storage/samples/ego_214_hand_pose.zip`
- **离线 sidecar**：`corpus/.../offline/episode_XXX/`（kp2d、rectified、annotations.jsonl）

## 验收门槛（`ego-platform-acceptance.sh`）

| 块 | 指标 |
|----|------|
| B3 | 126D 双手有效率均值 ≥ 85%（pipeline `hands_features.npz`） |
| B4 | pose 四元数单位化 + orientation_std |
| B5 | 每 episode 有 `annotations.jsonl`，覆盖率 ≥ 50% |
| B6 | 默认 backend = oak |

## 回滚

```bash
export EGO_PIPELINE_BACKEND=legacy
ego-run-pipeline ego-lan-214
```

完成标记从 `finalize.done`（oak）变为 `publish.done`（legacy）。

## 运维备忘

- **断点续跑**：失败后原样重跑 `ego-run-pipeline`（`.status/*.done` 幂等）
- **强制重跑子模块**：`EGO_HANDS_FORCE=1` / `EGO_TRACK_FORCE=1` / `EGO_LANGUAGE_FORCE=1`
- **仅补 finalize**：`ego-oak-convert --station ... --session ... --finalize-only`
- **HaMeR 真推理**：`EGO_HAMER_BACKEND=geopavlakos`（需 ego-hand-pipeline venv）

## 相关文档

- [分块改造计划](./ego-platform-migration.md)
- [214 操作手册](./ego-lan-214-pipeline-runbook.md)
