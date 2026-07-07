# ego-lan-214 批次 3：LeRobot 官方 Parquet 写入 — 设计约束

## 前置条件

- 批次 1：`DERIVE_EXTRACT_BACKEND=python` ✅
- 批次 2：`DERIVE_MUX_BACKEND=fluent` ✅（默认已切换）

## 三个硬性约束

1. **最小依赖**：仅引入 LeRobot **dataset 子模块**；镜像构建排除 `torch` 等训练重型依赖（优先 `pyarrow` 语义等价 + 官方 schema validate）。
2. **版本对齐**：`lerobot` 包版本必须与现网 viewer 使用的 **LeRobot v3** 严格一致，保证双向读写兼容。
3. **legacy 回退**：`DERIVE_PARQUET_BACKEND=legacy|lerobot`（默认 `legacy`），验收标准为 **语义等价 + 官方 validate 通过**，不要求字节级一致。

## 计划开关

| 变量 | 默认 | 说明 |
|------|------|------|
| `DERIVE_PARQUET_BACKEND` | `legacy` | `legacy` = 现有 `append-segment-parquet.py`；`lerobot` = 官方 dataset writer |

## 首批改动范围

- `scripts/append-segment-parquet.py` — legacy 路径（默认，不变）
- `scripts/append-segment-parquet-lerobot.py` — **已实现**：复用 legacy merge + v3 schema 校验；`lerobot` 包可用时调用官方 `validate`
- `stream-ingest.mjs` — `DERIVE_PARQUET_BACKEND` 分支（`resolveAppendParquetScript`）
- `requirements-parquet-lerobot.txt` — 版本 pin（与 viewer 对齐后启用灰度）
- 验收：`RUN_PARQUET_BATCH3=1` 纳入扩展验收（待写）

## 灰度前待办

1. 在镜像中 `pip install` 对齐 viewer 的 `lerobot` 版本（`requirements-parquet-lerobot.txt`）
2. `DERIVE_PARQUET_BACKEND=lerobot` 全量 re-derive 对比 `ingest_row_count` / episode 索引
3. 回退 `legacy` 后 phase1 验收 100% PASS

## 验收（草案）

- 21 段派生后 `ingest_row_count`、episode 索引与 legacy 一致
- LeRobot 官方 `validate` / schema 检查通过
- 回退 `DERIVE_PARQUET_BACKEND=legacy` 后全量验收 100% PASS
