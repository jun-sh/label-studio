# 431132 人形搬箱具身数据标注 — 商业标准与落地规划

> **版本：** v1.0（规划定稿）  
> **完整总纲（含 GR00T/OpenPI 训练篇）：** [`431132-commercial-annotation-master.md`](./431132-commercial-annotation-master.md)  
> **数据包：** `limx_box_transport / 431132`（780 ep）  
> **对标：** Tesla Optimus / Figure / 智元等量产人形搬箱数据架构  
> **状态：** P1 技能自动推导已落地；**P1.5 逐箱 cycle 字段**（`skill_cycles[]` + `box_cycle` 自动汇总）已落地；P2 高精子集待实施

---

## 目录

1. [核心前置结论](#1-核心前置结论)
2. [行业商业化标注现状](#2-行业商业化标注现状)
3. [431132 现状诊断](#3-431132-现状诊断)
4. [四层标注架构（L0–L3）](#4-四层标注架构l0l3)
5. [双层任务拆解范式](#5-双层任务拆解范式)
6. [落地实施方案（P0 / P1 / P2）](#6-落地实施方案p0--p1--p2)
7. [两大核心问题定论](#7-两大核心问题定论)
8. [排期与依赖](#8-排期与依赖)
9. [文档索引](#9-文档索引)

---

## 1. 核心前置结论

以下为大厂量产具身数据的统一共识，**不做二选一**：

| # | 共识 | 说明 |
|---|------|------|
| 1 | **双层标注** | 粗粒度原子技能 + 细粒度时序相位，同时存在 |
| 2 | 分工明确 | 细相位 → 动作时序、接触时机、失败定位；粗原子 → 技能库、策略分层 |
| 3 | 拒绝整段宏标签 | 单一「搬箱」标签信息损耗过大，无法量产迭代 |
| 4 | 先标准后增值 | 全量跑通基础标准；高价值子集做高精标注 |
| 5 | 视觉优先 | Optimus 量产路线弱化全量动捕/力传感，强化相位语义与失败分类；与 431132 单头摄高度匹配 |

**原则：现有 8 类时序标签不删、不改、不替换；仅叠加新维度。**

---

## 2. 行业商业化标注现状

### 2.1 大厂统一四层架构

所有商用搬箱、物料转运任务均采用四级分层，区别仅在于 L3 是否全量开启：

| 层级 | 名称 | 全量比例 | 核心用途 |
|------|------|----------|----------|
| **L0** | 任务全局层 | 100% | 任务文本、整集成败、搬箱数、任务/场景 ID；筛选、加权、统计 |
| **L1** | 原子技能层 | 100% | 3 段不可再分商用原子技能；任务规划、技能组合 |
| **L2** | 时序相位层 | 100% | 细粒度动作切片；手部操作、接触、抬升、放置时机 |
| **L3** | 高精物理层 | Top 5%–10% | 6D 位姿、精准接触帧、力反馈；高成本，非全量 |

### 2.2 Optimus/Figure 搬箱固定范式

针对「人形机器人将物料框搬运至流水线」：

```
完整任务 = 3 大原子技能（策略层） + 8 细粒度相位（动作层）
```

| 原子技能（L1） | 语义 | 对应细相位（L2） |
|----------------|------|------------------|
| `approach_skill` | 从原位移动至箱体可操作范围（纯行走，无手部交互） | `idle` 结束 → `pre_grasp` 开始前：`reach`（及箱间 `idle`） |
| `grasp_skill` | 伸手预抓 → 接触 → 稳定抬升离地 | `pre_grasp` → `contact` → `lift` |
| `place_skill` | 持箱行走 → 对位流水线 → 下降放置 → 松手 | `transport` → `place` → `release` |

细相位 8 类（与现有 schema 一致）：

`idle` → `reach` → `pre_grasp` → `contact` → `lift` → `transport` → `place` → `release`

### 2.3 大厂核心取舍

1. **摒弃**：整段标为单一「搬箱」宏动作；无限细分相位（成本爆炸、泛化差）。
2. **视觉优先**：单/多头摄 + 时序语义为主力；6D/力传感仅子集。
3. **负样本刚需**：标准化失败样本与成功样本配比（目标约 1:1）；细化失败原因用于课程学习。

---

## 3. 431132 现状诊断

### 3.1 优势（符合商用基线）

| 项 | 状态 |
|----|------|
| 8 类时序相位 | ✅ 与工业 pick-and-place 体系对齐，**无需替换** |
| L0 基础 | ✅ 任务文本、outcome、box_cycle |
| 采集形态 | ✅ 单头摄纯视觉，契合 Optimus 视觉时序路线 |
| 标注工具 | ✅ Schema 驱动、时间轴拖边/吸附（Phase A/B 已落地） |

### 3.2 短板（待 P0–P2 补齐）

| 短板 | 影响 | 对应阶段 |
|------|------|----------|
| 无原子技能分层（L1） | 无法技能组合训练 | **P1 ✅** 自动推导 + 抽检 |
| 无逐箱失败归因 | 难做失败分析 | **P1.5 ✅** `skill_cycles[].success` / `fail_reason` |
| 相位边界无统一规范 | reach / pre_grasp 一致性差 | **P0** |
| 无标准化失败体系 | 负样本价值低 | P2（子集）+ 逐步全量 fail_reason |
| 元数据缺失 | 多物体/多场景泛化弱 | P2 子集 |
| 无关键接触帧 | 接触时序精度受限 | P2 子集 |

---

## 4. 四层标注架构（L0–L3）

### L0 — 任务全局（100% 全量 · 商用底线）

| 字段 | 来源 | 431132 状态 |
|------|------|-------------|
| 任务自然语言 | 数据集 `task` | ✅ 已有 |
| Episode 成败 | `outcome` | ✅ 已有 |
| 成功搬箱数 | `box_cycle` | ✅ 已有 |
| 任务 ID | `task_family` / collection | ✅ manifest |
| 场景 ID | `scene_id` | ⏳ P2 |
| 备注 / 数据问题 | `notes` | ✅ 已有 |

### L1 — 原子技能（100% 全量 · P1 叠加）

每箱周期 3 段，**由 L2 相位推导或独立标注**（推荐：工具自动从 8 类推导 + 人工抽检）：

| 技能 ID | 起止判据 |
|---------|----------|
| `approach_skill` | 上一段 `idle`（或 episode 起点）→ `pre_grasp` 起始帧 |
| `grasp_skill` | `pre_grasp` 起始 → `lift` 结束 |
| `place_skill` | `transport` 起始 → `release` 结束 |

存储形态（规划，未实现）：`episodes[n].skill_segments[]` 或 `cycles[].skills[]`。

### L2 — 时序相位（100% 全量 · 当前主工作）

现有 `subtasks[]`，8 类不变。边界规范见 [`431132-phase-boundary-spec.md`](./431132-phase-boundary-spec.md)。

### L3 — 高精物理（5%–20% 子集 · P2）

| 维度 | 说明 |
|------|------|
| `fail_reason` 枚举 | 滑脱、碰撞、对位偏差、放置偏移、中途掉箱等 |
| 关键帧 | 首次接触帧、最后释放帧 |
| `cycle_id` / `box_id` / `scene_id` | 周期与物体/场景 |
| 6D 位姿、力反馈 | **暂不标**；待多视/触觉硬件升级 |

---

## 5. 双层任务拆解范式

### 5.1 训练用途对照

| 训练目标 | 使用数据层 |
|----------|------------|
| 端到端搬箱 VLA / 长时序策略 | L0 + L2（8 相位） |
| 技能库、分层规划、组合任务 | L0 + L1（3 原子） |
| 接触时机、精细动作收敛 | L2 + L3 关键帧（子集） |
| 课程学习、负样本 | L0 `outcome` + `fail_reason` |

### 5.2 一集多箱的数据形态（目标）

```
Episode
├── outcome, box_cycle, task_text          # L0
├── subtasks[]                             # L2：全时间轴 8 类
└── cycles[]                               # P1+：每箱一轮
    ├── cycle_id
    ├── skill_segments[]                   # L1：approach / grasp / place
    ├── fail_reason?                       # P2
    └── keyframes?                         # P2：first_contact, last_release
```

**P1 最小可行：** 若暂不建 `cycles[]`，可由后端脚本从 `subtasks[]` 按 `reach…release` 模式切分并导出 `skill_segments`（需统一边界规范先行）。

---

## 6. 落地实施方案（P0 / P1 / P2）

### P0 — 全量商用基线（立即执行，零代码）

**目标：** 780 集达到大厂入门质量；统一标注员判据。

| 动作 | 交付物 |
|------|--------|
| 固化 8 类相位 | 不修改 schema |
| 输出边界规范 | [`431132-phase-boundary-spec.md`](./431132-phase-boundary-spec.md) |
| 培训对齐 | 更新 [`431132-annotation-sop-training.md`](./431132-annotation-sop-training.md) |
| 全量标注 L0 + L2 | outcome、box_cycle、8 类时间轴 |
| 试点 30 ep | 组长对齐 outcome / box_cycle / 边界 |

**结论：** `idle` … `release` **完全合适，无需调整标签集合。**

### P1 — 全量叠加原子技能（**已落地** · 方式 B 自动推导）

**目标：** 双层商用数据结构；不覆盖现有 subtask 数据。

| 动作 | 说明 | 状态 |
|------|------|------|
| Schema 扩展 | `skill_labels`（3 类）+ `skill_derivation` | ✅ |
| 标注方式 | 由 8 类自动推导 3 段 + `skill_review` 抽检 | ✅ |
| 存储 | `skill_segments[]`、`skill_cycles[]` 并列 `subtasks[]` | ✅ |
| 导出 | `skill_index` 列 + `skills.parquet` | ✅ |
| UI | L1 只读预览；改 L2 边界即更新 L1 | ✅ P1.1 |

**原子边界（与 §2.2 一致）：**

- `approach_skill`：`reach` 段（至 `pre_grasp` 前）
- `grasp_skill`：`pre_grasp` → `lift`（含）
- `place_skill`：`transport` → `release`（含）

### P1.5 — 逐箱 cycle 元数据（**已落地**）

**目标：** 每箱独立记录成败与失败原因；`box_cycle` 自动汇总成功箱数。

| 动作 | 说明 |
|------|------|
| `cycle_fields` | `success`（bool）、`fail_reason`（enum） |
| 存储 | `skill_cycles[]` 每轮含 `start`/`end`/`complete` + 用户字段 |
| `box_cycle` | 保存时由 `success=true` 的 cycle 数自动写入（可手动改） |
| UI | 「逐箱结果」面板：每箱成功/失败 + 失败原因 |

### P2 — 子集高精增值（10%–20% 优质样本）

**目标：** 高端数据商品；不做全量 6D/力传感。

| 动作 | 说明 |
|------|------|
| `fail_reason` 枚举 | 全量 episode 可逐步推广；高精质检先子集 |
| 关键帧 | 每 cycle 首次 `contact` 帧、最后 `release` 帧 |
| 元数据 | `cycle_id`、`scene_id`、`box_id`（极简） |
| 子目标语言 | 可选：「拿起第 N 箱」「放到传送带」 |

**暂不标：** 6D 位姿、触觉力、双臂协同、关节关键点。

---

## 7. 两大核心问题定论

### Q1：8 类时间轴是否合适？任务如何拆分？

**答：完全合适。** 工业搬箱最优细粒度时序方案之一，**无需修改标签 ID**。

**商用拆分：** 双层架构，非二选一：

- **底层（L2）：** 8 类时序相位全覆盖  
- **上层（L1）：** 3 类原子技能分段（P1 叠加）

### Q2：Optimus/Figure 商用必标 / 选标清单

| 类别 | 内容 | 431132 |
|------|------|--------|
| **100% 必标** | 任务语言、episode 成败、搬箱周期、8 相位 | P0 ✅ 进行中 |
| **100% 必标（P1）** | 3 类原子技能分段 | **✅ 自动推导** |
| **100% 必标（P1.5）** | 逐箱 success / fail_reason | **✅ 已上线** |
| **100% 必标（P1+）** | episode 级失败标签 | 建议 P2 初全量 |
| **子集选标** | 关键接触帧、场景/物体 ID、子目标语言 | P2 |
| **暂不标** | 6D、力反馈、双臂、关节关键点 | 后续硬件迭代 |

---

## 8. 排期与依赖

```
紧急（本周）
  └─ P0：边界规范发布 + 培训试点 30 ep

短期（1–2 周，规划评审后）
  └─ P1 设计评审：schema v2、存储、UI 第二轨道 vs 自动推导
  └─ P1 开发：annotation_schema + 导出脚本

中期（标注并行）
  └─ 全量 L0+L2 标注推进（目标 780 ep）
  └─ P1 上线后回溯或并行标 skill_segments

后期
  └─ P2：抽 78–156 ep（10%–20%）高精子集
  └─ fail_reason 全量推广、关键帧子集
```

### 依赖关系

| 阶段 | 依赖 |
|------|------|
| P0 | 无代码依赖 |
| P1 | P0 边界规范稳定；schema spec 更新 |
| P2 | P1 cycle 切分逻辑稳定 |

### 代码改动范围（规划，未启动）

| 组件 | P1 可能改动 |
|------|-------------|
| `collection.manifest.json` | `box_transport_v2` |
| `annotation_schema.py` | `skill_labels`、校验 |
| `app.js` | 技能轨道 UI 或「自动推导预览」 |
| `lerobot_annotations.json` | `skill_segments`、`cycles` |
| 导出 | `subtasks.parquet` / skill 列 |

**P0 明确不改代码。**

---

## 10. 极简落地终稿（2026-07-14）

> **决策：** 780 ep · 1–3 人自用标注场景下，**不堆平台化运营基建**；聚焦训练信号纯度与训练导出链路。  
> L0 + L1 + L1.5 + L2 **已对齐 Figure / Tesla Optimus 2026 量产范式**，无需重构。

### 10.1 真刚需（已实现，保留）

| 能力 | 训练价值 |
|------|----------|
| L2 八阶相位时序 | 长时序主监督；接触/抬升/放置时机 |
| L1.5 逐箱 cycle + `fail_reason` | 稠密 reward、负样本、课程学习 |
| L1 三技能自动 + `subgoal` | 分层 VLA / GR00T / π 技能解耦 |
| L0 + `box_cycle` 自动 + W-EP 软校验 | 数据纯度、逻辑一致性 |
| 进度状态（已标/草稿/未标） | 780 ep 有序推进 |

### 10.2 明确不做（本迭代暂停）

QC 工作台、Export gate、IAA、任务锁定、预标注、L3 高精、scene/box ID、Ego 模块、Dashboard、版本 diff 等 **交付型冗余** — 小体量场景收益极低，**不排期**。

### 10.3 唯一工程补齐（训练链路） — ✅ 2026-07-14 已落地

| # | 项 | 状态 |
|---|-----|------|
| 1 | Embed 深链 `collection`/`package`/`episode` 透传 | ✅ `embodiedAnnotate.ts` + `EmbodiedAnnotatePage` |
| 2 | 后端 subtask 校验（V-01..V-03） | ✅ `validate_subtasks` + `set_episode_annotations` |
| 3 | 导出 `skill_cycles.parquet` + `skills.parquet` 含 `subgoal` | ✅ `export_dataset` |

### 10.4 非代码主线

1. **30 ep 分层试点** — 统一 L2 边界 SOP、`partial` / `fail_reason` 标准  
2. **消融实验** — 实证「L2 + L1.5」相对粗粒度 baseline 对 GR00T/π 的增益

### 10.5 对外表述

当前 431132 标注架构在单任务、单目视觉、小体量场景下，是**最高性价比、最贴合工业量产**的具身数据方案。核心价值：**时序因果正确、逐轮成败可信、标签纯净可用**。

---

## 9. 文档索引

| 文档 | 用途 |
|------|------|
| 本文 | 商业标准、四层架构、P0/P1/P2 总规划 |
| [`431132-phase-boundary-spec.md`](./431132-phase-boundary-spec.md) | 8 类相位边界判据（P0 执行） |
| [`431132-annotation-sop-training.md`](./431132-annotation-sop-training.md) | 标注员操作手册 |
| [`annotation-schema-spec.md`](./annotation-schema-spec.md) | Schema 规范与 v2 扩展规划 |
| [`schemas/box_transport_v1.annotation_schema.json`](./schemas/box_transport_v1.annotation_schema.json) | 当前生效 schema 快照 |

---

*文档版本：2026-07-14 · 维护：数据平台 / embodied-annotate*
