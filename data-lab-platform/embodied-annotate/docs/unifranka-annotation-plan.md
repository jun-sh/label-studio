# UniFranka 标注规划（对标 Tesla Optimus / Figure 01）

> **版本：** v1.0 · 2026-07-20  
> **集合：** `unifranka`（Franka Panda · LeRobot v3.0）  
> **配套：** [任务族映射表](./unifranka-task-family-map.json) · [pick_place 边界规范](./unifranka-pick-place-phase-boundary-spec.md) · [Schema 目录](./schemas/)

---

## 1. 定稿原则

| 原则 | 说明 |
|------|------|
| 物理包不动 | `681458`…`681520` 目录保留，不重构存储 |
| 逻辑层拆分 | 用 `task_family` + `schema_id` 做元数据隔离 |
| L0 | `task_text`（LeRobot `tasks.parquet`）只读，作为语言条件 |
| L1 | 按 `task_family` 定义原子技能，**不**复用搬箱三技能 |
| L1.5 | **UniFranka 全局禁用** `skill_cycles` / `box_cycle` |
| L2 | 同族内统一相位；跨族 schema 隔离 |
| 训练 | VLA 用 L0；BC 相位仅在**同 task_family** 内混训 |

---

## 2. 任务族与 Schema

| task_family | schema_id | 适用场景 | Episodes（全库） |
|-------------|-----------|----------|------------------|
| `pick_place` | `pick_place_v1` | 抓取—搬运—放置、扔、取放餐具 | 4570 |
| `pour_transfer` | `pour_transfer_v1` | 倾倒液体/颗粒、勺取转移 | 78 |
| `tool_use` | `tool_use_v1` | 切、戳、搅拌 | 63 |
| `wipe_clean` | `wipe_clean_v1` | 擦桌、扫地、清洁 | 178 |
| `articulate_drag` | `articulate_drag_v1` | 拖盘、拖碗 | 66 |
| `articulation` | `articulation_v1` | 翻转叠杯、折叠毛巾 | 45 |

**80 条唯一语言任务 → 族映射见：** [`unifranka-task-family-map.json`](./unifranka-task-family-map.json)

---

## 3. 分包标注范围

| 包 | Episodes | 任务数 | 族分布 | 标注范围 |
|----|----------|--------|--------|----------|
| **681458** | 1000 | 3 | 100% pick_place | **全量 L2** |
| **681496** | 1000 | 2 | 100% pick_place | **全量 L2** |
| **681520** | 1000 | 6 | 100% pick_place | **全量 L2**（多段往返用多轮 L2 相位，无 cycle 层） |
| **681460** | 1000 | 18 | 87% pick / 12% wipe / 1% pour | **按族抽样** 30 ep/族 |
| **681485** | 1000 | 53 | 70% pick / 其余 5 族 | **按族抽样** 30 ep/族 |

---

## 4. 标注 SOP（量产派单）

1. **加载包** → 阅读顶部 **L0 `task_text`**（勿重标为时间轴）
2. **查映射表** → 确认该任务的 `task_family` 与 `schema_id`
3. **同族连续标** → 同一 `task_text` 或同族连续 20–50 ep，统一判据
4. **标 L2** → 仅使用本族 `subtask_labels`；**禁止**对倒水任务用 pick_place 八相位
5. **标 outcome** → episode 级 success / fail / partial（无 cycle 面板）
6. **质检** → 同族抽检 10%；边界争议以本族 phase spec 为准

### 优先级

| 阶段 | 包 / 内容 |
|------|-----------|
| P0 | `681496` → `681458` → `681520`（验证 `pick_place_v1`） |
| P1 | `681460` pick_place + wipe_clean 抽样 |
| P2 | `681485` 五族各 30 ep 代表任务 |

---

## 5. 分族 L2 相位速查

### 5.1 `pick_place_v1`（8 相 · 含 L1 自动推导）

```
idle → reach → pre_grasp → contact → lift → transport → place → release
```

- L1：`approach_skill` / `grasp_skill` / `place_skill`（自动，只读）
- **无** `cycle_fields` / `skill_cycles`
- 边界细则：[`unifranka-pick-place-phase-boundary-spec.md`](./unifranka-pick-place-phase-boundary-spec.md)

### 5.2 `pour_transfer_v1`（7 相）

```
idle → reach → grasp → lift_tilt → pour → return_level → release
```

| 相位 | 进入 | 退出 |
|------|------|------|
| lift_tilt | 容器离开支撑面并开始倾斜 | 液体/颗粒可见流出 |
| pour | 流出开始 | 流出停止 |
| return_level | 停止流出 | 容器大致回正 |

### 5.3 `tool_use_v1`（7 相）

```
idle → reach → grasp_tool → contact_work → actuate → retract → release
```

| 相位 | 说明 |
|------|------|
| actuate | 切/戳/搅的主操作段；连续操作不拆多段 |
| contact_work | 工具尖端首次碰到目标 |

### 5.4 `wipe_clean_v1`（7 相）

```
idle → reach → grasp_tool → contact_surface → wipe → retract → release
```

| 相位 | 说明 |
|------|------|
| wipe | 工具与表面保持接触并移动（擦/扫） |
| **禁止** 使用 lift / transport |

### 5.5 `articulate_drag_v1`（6 相）

```
idle → reach → engage → drag → disengage → idle_end
```

| 相位 | 说明 |
|------|------|
| drag | 物体在面上滑动；无夹爪 lift |
| **禁止** 使用 pre_grasp / release 语义 |

### 5.6 `articulation_v1`（6 相）

```
idle → reach → grasp → reorient → hold_adjust → release
```

| 相位 | 说明 |
|------|------|
| reorient | 翻转、对折、叠放等姿态变化主段 |
| 用于：叠杯、折毛巾 |

---

## 6. 训练侧对齐（MoE 思路）

| 头 | 输入 | 标签 |
|----|------|------|
| VLA / 语言策略 | 图像 + `task_text` + action | 端到端 action 或 success |
| BC 相位（同族） | 图像 + `subtask_index` | 仅 **同 schema_id** 的 episode |
| **禁止** | 跨族混训 `subtask_index` | pour 的 index 4 ≠ pick 的 index 4 |

导出契约沿用 v1.0：`cycles.parquet` 对 UniFranka 可为空；`episodes_meta.outcome` 保留。

---

## 7. 与 431132 差异

| 项 | 431132 | unifranka |
|----|--------|-----------|
| task_family | 单一 `box_transport` | 6 族 |
| L1.5 | 必须 | **禁用** |
| L2 schema | 全局八相位 + 搬箱边界 | **按族** |
| 包语义 | 产线任务族 | 转换批次（混任务） |
| 全量 L2 | 780 ep | 仅纯 pick 包 + 混杂包抽样 |

---

## 8. 任务映射表（摘要）

完整 80 条见 JSON。按族统计：

| task_family | 任务条数 | 总 ep |
|-------------|----------|-------|
| pick_place | 55 | 4570 |
| wipe_clean | 5 | 178 |
| pour_transfer | 8 | 78 |
| tool_use | 5 | 63 |
| articulate_drag | 5 | 66 |
| articulation | 2 | 45 |

**681458 三条任务（全 pick_place）：**

| Episodes | task_text |
|----------|-----------|
| 374 | Throw the crumpled paper into the trash can. |
| 357 | Place the plastic fork on the plate. |
| 269 | Remove the plastic fork from the plate and place it on the table. |

---

## 9. 待实现（工具链）

> **规范定稿：** [Annotation Job 规范](./annotation-job-spec.md)（`annotation_job_spec@1.0`）  
> P0 收尾后按该文档开发 D3–D5；**D1–D2 已落地**（`task_family.py` + `annotation.jobs.json`）。P0 三包标注不阻塞。

- [x] Annotation Job 体系（`annotation.jobs.json` + `generate_annotation_jobs.py`）
- [x] `resolve_schema_for_episode` — D1 核心路由（待 D3 接入 API）
- [ ] UI：Job 内 episode 列表 + Job 选择器入口（D4）
- [ ] 导出：`episodes_meta` 每 episode 写入 `task_family` + `schema_ref`（D5）

当前阶段：标注员 **手动查映射表** 选择对应相位定义；纯 pick 三包 manifest 已指向 `pick_place_v1`。
