# EGO 数据管线 — MaxInsights 对齐方案

> **参考：** [MaxInsights — The Next Frontier / 10M](https://www.maxinsights.ai/#1)  
> **版本：** `maxinsights_aligned@1.0`  
> **生产基线：** `ego-001`（130 采集 / 34 平台）

本文档将 MaxInsights 公开的 Physical AI / Egocentric 数据管线模型，映射到 Data Lab 现有实现，并定义可观测契约。

---

## 1. 两个维度：规模 × 密度（Section 01）

MaxInsights 提出通往 10M 小时的两条轴：

| 维度 | MaxInsights 表述 | Data Lab 对应 |
|------|------------------|---------------|
| **Experience Volume** | 更大规模的人类经验 | `stream/` 热层 ingest + `corpus/` 聚合 + `ego-archive/` 冷层 |
| **Experience Density** | 每小时内可学习的物理结构 | L2–L4 感知 enrich + annotation 子任务轴 + export 白名单 |

**核心原则（已落地）：** 原始视频只是起点；pose、geometry、contact、temporal structure 通过 derive → L2 → L3/L4 逐层注入。

---

## 2. 四维语义 taxonomy（Section 01）

MaxInsights 用 **Environment → Task → Subtask → Instruction** 组织经验。

| 层级 | MaxInsights | Data Lab 字段 | 写入阶段 |
|------|-------------|---------------|----------|
| Environment | 场景/环境多样性 | `extended_info.scene_id` | 采集 manifest |
| Task | 任务定义 | `task_id` | 采集 manifest |
| Subtask | 操作相位 | `meta/subtasks.parquet` | embodied-annotate L2 |
| Instruction | 自然语言指令 | `meta/tasks.parquet` + QC override | 标注 / QC |

采集端示例见 `ego-stream-client/samples/manifest-human-demo.json`。

---

## 3. 生产闭环（Section 03）

MaxInsights：**task design → capture → QA → structured outputs** 在同一生产环内连接。

```text
[Task Design]  manifest.task_id + scene_id
      ↓
[Capture]      130 OAK 4-cam + 200Hz IMU → MCAP/tar.zst
      ↓
[Upload]       ego-upload → stream-ingest → session.DONE_UPLOAD
      ↓
[Processing]   derive-worker → session.READY → ego-process L2/L3
      ↓
[QA]           /qc/ terminal review
      ↓
[Structured]   L4 pose + annotation complete → ego-export-delivery
```

**日常 SOP（最简）：**

```bash
# 130
ego-upload ego-001

# 34（自动或手动）
ego-process ego-001
ego-process ego-001 --export   # 商业交付
```

闭环状态通过 API 观测：

```bash
curl -s http://127.0.0.1:8080/lerobot/api/collection/stations/ego-001/delivery-status \
  | python3 -m json.tool
```

响应新增字段：

- `pipeline_model`: `maxinsights_aligned@1.0`
- `production_loop[]`: 五步闭环完成度
- `episodes[].taxonomy`: Environment / Task / Subtask / Instruction
- `episodes[].perception_modules[]`: 9 项感知模块状态

---

## 4. 九项感知模块（Section 05）

配置源：`config/ego-perception-modules.json`  
运行时探测：`lerobot-studio/perception-modules.mjs`

| # | 模块 | 状态 | 后端 | 管线层 |
|---|------|------|------|--------|
| 01 | Video Understanding | ✅ active @ derive | derive-worker | derive, annotation |
| 02 | Depth | ⚠️ partial @ L3 | Depth Anything V2 | L3 preview |
| 03 | Object Tracking | 📋 planned | — | L4 |
| 04 | Hand Poses | ✅ L3 partial / L4 active | MediaPipe → WiLoR/HaMeR | L3, L4 |
| 05 | Upper Body Poses | ⚠️ partial | head_pose feature | L2, L4 |
| 06 | Tactile Force | ⏭ skipped | 无触觉硬件 | — |
| 07 | Localization | ✅ active @ IMU | 200Hz IMU + derive | derive, L2 |
| 08 | Segmentation | 📋 planned | SAM2（ml-services） | L4 |
| 09 | Point Cloud | 📋 planned | OAK depth / SLAM | L4 |

**模块状态枚举：**

| status | 含义 |
|--------|------|
| `active` | 该 session 已有可用输出 |
| `partial` | 预览级 / 占位级（如 MediaPipe kp2d） |
| `pending` | 管线可达但未完成 |
| `planned` | 路线图内，尚未接入主路径 |
| `skipped` | 硬件/embodiment 不支持 |

---

## 5. 动作复杂度（Section 06）

MaxInsights 三级动作覆盖：**Easy → Medium → Hard**。

Data Lab 通过 annotation schema 的 subtask 相位表达复杂度：

| 级别 | 示例 subtask 链 | schema |
|------|-----------------|--------|
| Easy | reach → contact → release | 单步可重复 |
| Medium | + pre_grasp → lift → place | `ego_human_demo_v1` 8 相位 |
| Hard | 长 horizon + recovery | 多 session 任务族 + outcome=partial |

Schema：`embodied-annotate/docs/schemas/ego_human_demo_v1.annotation_schema.json`

---

## 6. 与现有文档的关系

| 文档 | 关系 |
|------|------|
| [`ego-core-pipeline.md`](./ego-core-pipeline.md) | 最简主路径（derive + ego-process） |
| [`ego-export-contract.md`](./ego-export-contract.md) | 商业交付白名单 |
| [`ego-mcap-pi-migration-plan.md`](./ego-mcap-pi-migration-plan.md) | MCAP/H.264 采集对齐 Physical Intelligence |
| **本文档** | MaxInsights 产品模型 → 工程映射 |

---

## 7. 端到端拓扑（对齐 MaxInsights 生产环）

```text
┌────────────── 130 Edge ──────────────┐
│ OAK 4-cam 30fps + IMU 200Hz          │
│ manifest: task_id, scene_id          │
│ ego-upload → MCAP/H.264 segments     │
└──────────────────┬───────────────────┘
                   │ HTTP upload
                   ▼
┌────────────── 34 Platform ───────────┐
│ stream-ingest → derive-worker        │
│   9 measures: video + IMU (active)   │
│ ego-process:                         │
│   L2 rectify corpus                  │
│   L3 MediaPipe + depth (partial)     │
│   L4 WiLoR pose (commercial)         │
│ /qc/ + /lerobot-annotate/            │
│ ego-export-delivery (structured)     │
└──────────────────────────────────────┘
```

---

## 8. 差距与路线图

| 能力 | 当前 | 下一步 |
|------|------|--------|
| Object Tracking | 未接入 | SAM2 + tracker sidecar @ L4 |
| Segmentation | ml-services 存在 | 接入 `ego-delivery-run --algo sam2` |
| Point Cloud | 未接入 | OAK depth socket → corpus feature |
| Tactile | 无硬件 | 跳过；robot teleop embodiment 扩展 |
| 生产环自动串联 | QC/annotation 为旁路 | `EGO_EXPORT_REQUIRE_*` 默认开启；watcher 触发 annotate 队列 |
| 多站编排 | 单站 CLI | registry DB + 450K/h 级调度（远期） |

---

## 9. 关键文件

| 路径 | 用途 |
|------|------|
| `config/ego-perception-modules.json` | 9 模块 + 生产环定义 |
| `lerobot-studio/perception-modules.mjs` | 模块状态探测 |
| `lerobot-studio/delivery-status.mjs` | 统一 episode 交付 + MaxInsights 视图 |
| `scripts/ego-process` | 后处理主入口 |
| `scripts/ego-export-delivery.py` | 结构化商业导出 |

---

*关联：[`ego-001-使用手册.md`](./ego-001-使用手册.md) · [`ego-core-pipeline.md`](./ego-core-pipeline.md)*
