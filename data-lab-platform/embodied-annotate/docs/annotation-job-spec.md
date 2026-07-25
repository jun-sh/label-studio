# Annotation Job 规范（量产标注平台 · v1.0）

> **版本：** `annotation_job_spec@1.0` · 2026-07-20  
> **状态：** 待评审 → P0 收尾后开发落地  
> **对齐：** Tesla Optimus / Figure 01 量产标注平台 — **Corpus 可混，Annotation Job 必须纯**  
> **配套：** [UniFranka 标注规划](./unifranka-annotation-plan.md) · [任务族映射](./unifranka-task-family-map.json) · [导出契约](./export-contract.md) · [Schema 规范](./annotation-schema-spec.md)

---

## 1. 背景与问题陈述

### 1.1 现状

| 层 | 当前行为 | 问题 |
|----|----------|------|
| **存储** | LeRobot 按转换批次成包（如 `681460` 含 18 条语言任务、3 个 `task_family`） | 开源 corpus **常态**，非数据错误 |
| **平台** | `resolve_annotation_schema(dataset_root)` — **一包一套 L2 相位** | 混杂包打开后 wipe/pour episode 仍显示 pick 八相位 |
| **标注员** | 需手查 `task-family-map.json` 判断族与相位 | 违背量产 SOP，易错标、难质检 |

### 1.2 定稿原则（评审冻结）

1. **物理包不动** — 不拆分、不重写 LeRobot 目录；`videos/`、`data/`、`meta/` 保持原样。  
2. **存储层允许多任务混包** — 与 DROID / OXE 等开源 corpus 一致。  
3. **标注工单禁止混 L2 语义** — 一个 Job 绑定唯一 `schema_id`；标注员看不到跨族 episode。  
4. **Schema 跟 Job / Episode 走，不跟 Package 走** — 废弃「一包一 schema」作为标注入口语义。  
5. **L0 只读** — `task_text` 来自 LeRobot `tasks.parquet`，不在时间轴重标。  
6. **向后兼容** — 单族数据集（如 `431132` / UniFranka P0 三包）自动生成隐式 Job，P0 标注流程不中断。

---

## 2. 概念模型

```
┌──────────────────────────────────────────────────────────────────┐
│ Corpus（只读 · LeRobot 物理布局）                                 │
│   Collection ──► Package(681460) ──► Episode ──► Frames          │
│   L0: task_text / task_index 存于 meta/tasks + meta/episodes       │
└──────────────────────────────────────────────────────────────────┘
                              │
                              │ 逻辑切片（零拷贝）
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│ Annotation Job（标注员唯一入口）                                   │
│   job_id: unifranka-681460-wipe_clean-sample30                   │
│   filter: package=681460 ∧ task_family=wipe_clean ∧ ep⊆sample   │
│   schema: wipe_clean_v1@1（固定，UI 不可切换）                    │
│   episode_queue: [562, 569, …]（仅 Job 内 episode）              │
└──────────────────────────────────────────────────────────────────┘
                              │
                              │ 保存 / 导出
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│ Training View（训练消费）                                         │
│   filter: task_family + schema_ref + outcome + annotated         │
│   导出: episodes_meta.parquet 每行带 task_family / schema_ref    │
└──────────────────────────────────────────────────────────────────┘
```

### 2.1 术语

| 术语 | 定义 |
|------|------|
| **Collection** | 逻辑数据集集合，如 `unifranka`、`limx_box_transport`；含 `collection.manifest.json` |
| **Package** | Collection 下物理 LeRobot 根目录，如 `681460/` |
| **Episode** | Package 内 `episode_index` 标识的一条轨迹 |
| **task_family** | 操作语义族，决定 L2 相位集合；由 `task_text` 查映射表得到 |
| **Annotation Job** | 标注派单单元：`collection + package + 过滤条件 + 唯一 schema` |
| **schema_ref** | `{schema_id}@{schema_version}`，如 `wipe_clean_v1@1` |

---

## 3. Annotation Job 定义

### 3.1 Job 清单文件

路径（Collection 级）：

```
datasets/<collection_id>/annotation.jobs.json
```

与 `task-family-map.json` 并列；**不写入 LeRobot package 目录**。

### 3.2 顶层结构

```json
{
  "jobs_version": "1.0",
  "collection_id": "unifranka",
  "schema_registry_dir": "data-lab-platform/embodied-annotate/docs/schemas",
  "task_family_map": "task-family-map.json",
  "jobs": [ /* AnnotationJob[] */ ]
}
```

### 3.3 `AnnotationJob` 字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `job_id` | string | 是 | 全局唯一；建议 `{collection}-{package}-{family}[-{scope}]` |
| `display_name` | string | 是 | UI 展示，如 `681460 · wipe_clean · 抽样 30` |
| `collection_id` | string | 是 | |
| `package_id` | string | 是 | 物理包 ID |
| `task_family` | string | 是 | 本 Job 唯一族；决定 schema |
| `schema_id` | string | 是 | 如 `wipe_clean_v1`；可由 `schema_by_family` 推导后写入 |
| `scope` | enum | 是 | `full` \| `sample` \| `explicit` |
| `filters` | object | 否 | 见 §3.4 |
| `episode_indices` | int[] | 条件 | `scope=explicit` 时必填；`sample` 可预生成或运行时抽样 |
| `sample` | object | 条件 | `scope=sample` 时必填 |
| `status` | enum | 否 | `open` \| `paused` \| `closed`；默认 `open` |
| `priority` | int | 否 | 派单排序；越小越优先 |
| `phase_spec_doc` | string | 否 | 边界规范 markdown 相对路径 |
| `notes` | string | 否 | 运营备注 |

### 3.4 `filters` 对象

```json
{
  "task_text": null,
  "task_index": null,
  "task_texts": [],
  "exclude_episode_indices": []
}
```

| 字段 | 说明 |
|------|------|
| `task_text` | 精确匹配单条 L0 语言任务（可选，用于单任务 Job） |
| `task_index` | LeRobot task_index 过滤（可选） |
| `task_texts` | 多条 task_text 白名单（OR） |
| `exclude_episode_indices` | 从 Job 队列排除 |

**Job 内 episode 集合** = package 全量 episode  
→ 按 `task_family` 过滤（经 `resolve_task_family(episode)`）  
→ 再应用 `filters`  
→ 再应用 `scope`（`full` / `sample` / `explicit`）

### 3.5 `sample` 对象（`scope=sample`）

```json
{
  "method": "random",
  "count": 30,
  "seed": 20260720,
  "stratify_by": "task_text"
}
```

| `method` | 说明 |
|----------|------|
| `random` | 固定 seed 可复现 |
| `first_n` | 按 `episode_index` 升序取前 N |
| `stratified` | 按 `stratify_by` 字段分层，每层 ceil(N/k) |

抽样结果**写入** `episode_indices` 并持久化到 `annotation.jobs.json`（评审后冻结，避免重跑变样）。

### 3.6 Job 示例

**P0 纯 pick 包 — 全量隐式 Job（可手写或自动生成）：**

```json
{
  "job_id": "unifranka-681496-pick_place-full",
  "display_name": "681496 · pick_place · 全量 L2",
  "collection_id": "unifranka",
  "package_id": "681496",
  "task_family": "pick_place",
  "schema_id": "pick_place_v1",
  "scope": "full",
  "status": "open",
  "priority": 10,
  "phase_spec_doc": "docs/unifranka-pick-place-phase-boundary-spec.md"
}
```

**P1 混杂包 — wipe 抽样 Job：**

```json
{
  "job_id": "unifranka-681460-wipe_clean-sample30",
  "display_name": "681460 · wipe_clean · 抽样 30",
  "collection_id": "unifranka",
  "package_id": "681460",
  "task_family": "wipe_clean",
  "schema_id": "wipe_clean_v1",
  "scope": "sample",
  "sample": {
    "method": "stratified",
    "count": 30,
    "seed": 20260720,
    "stratify_by": "task_text"
  },
  "episode_indices": [],
  "status": "open",
  "priority": 40,
  "phase_spec_doc": "docs/schemas/wipe_clean_v1.annotation_schema.json"
}
```

**单族 legacy 数据集（431132）— 兼容 Job：**

```json
{
  "job_id": "limx_box_transport-431132-box_transport-full",
  "display_name": "431132 · box_transport · 全量",
  "collection_id": "limx_box_transport",
  "package_id": "431132",
  "task_family": "box_transport",
  "schema_id": "box_transport_v1",
  "scope": "full",
  "status": "open",
  "priority": 1
}
```

### 3.7 Job 自动生成规则（工具脚本，非运行时必需）

对尚无 `annotation.jobs.json` 的 Collection：

| Package `annotation_scope` | 生成策略 |
|----------------------------|----------|
| `full_l2` + 单 `dominant_task_family` | 每包 1 个 `scope=full` Job |
| `sample_by_family` | 每包 × 每 `task_family`（count>0）1 个 `scope=sample` Job |
| leaf 单包单族（431132） | 1 个 `scope=full` Job |

脚本建议路径：`backend/tools/generate_annotation_jobs.py`（P0' 交付物）。

---

## 4. `resolve_schema(episode)` — 核心解析

### 4.1 新增模块

文件：`backend/task_family.py`（建议名）

```python
def resolve_task_family(
    *,
    collection_id: str,
    package_id: str,
    episode_index: int,
    task_text: str | None,
    task_index: int | None,
    family_map: dict,
) -> tuple[str, str]:
    """Returns (task_family, schema_ref). Raises if unmapped."""

def load_schema_by_id(schema_id: str, registry_dir: Path) -> dict:
    """Load docs/schemas/{schema_id}.annotation_schema.json"""

def resolve_schema_for_episode(
    *,
    collection_id: str,
    package_id: str,
    episode_index: int,
    task_text: str | None,
    task_index: int | None,
    active_job: AnnotationJob | None,
) -> dict:
    """Returns full annotation_schema dict for this episode."""
```

### 4.2 解析顺序（冻结）

```
1. active_job.schema_id          （Job 上下文强制 — 标注主路径）
2. task_text → task-family-map   （episode 级族解析）
3. schema_by_family[family]     → load from schema registry
4. 单族 legacy fallback:
     package annotation.schema.json  （仅当 package 100% 单族且未启用 Job 系统）
5. 内置 default_manipulation_v1   （仅开发/未映射；标注模式 DISABLED）
```

### 4.3 硬规则

| 规则 | 行为 |
|------|------|
| **J-01** | 有 `active_job` 时，episode 的 `task_family` **必须**等于 `job.task_family`，否则 **403 + 不展示** |
| **J-02** | `resolve_task_family` 失败（task_text 未入映射表）→ episode **不可标 L2**；UI 显示「未映射任务」 |
| **J-03** | 保存 `subtasks` 时校验 label ∈ **该 episode 的 schema**，非 Job 默认 schema |
| **J-04** | 同一 episode 历史上若已有不同 `schema_ref` 的标注，切换 Job 打开时 **警告**但不自动删标 |
| **J-05** | UniFranka：`l1_5_cycles_enabled=false` 的 schema 不展示 cycle 面板（沿用 manifest `annotation_policy`） |

### 4.4 与旧 `resolve_annotation_schema(dataset_root)` 关系

| 场景 | 行为 |
|------|------|
| 新 Job 流程 | **主路径**；`DataManager.active_job` 持有当前 Job |
| 无 Job 加载（旧深链） | 单族包：自动选中唯一 `full` Job；混杂包：**拒绝加载**并提示选择 Job |
| `dataset_catalog` 展示 | Package 行显示 `job_count` / `dominant_task_family`，**不再**将 package 级 schema 作为标注依据 |

---

## 5. API 与 URL 约定

### 5.1 URL（前端路由）

基础路径：`/projects/{project}/embodied` 或 `/lerobot-annotate/`

| 参数 | 必填 | 示例 | 说明 |
|------|------|------|------|
| `collection` | 是 | `unifranka` | |
| `package` | 是 | `681460` | 物理包 |
| `job` | **是**（新） | `unifranka-681460-wipe_clean-sample30` | Job ID |
| `episode` | 否 | `562` | 当前 episode |

**示例深链：**

```
?collection=unifranka&package=681496&job=unifranka-681496-pick_place-full&episode=0
```

**旧链兼容（过渡期）：**

```
?collection=unifranka&package=681496
```

→ 后端若仅 1 个 open Job，**302 逻辑**自动补 `job=`；若多个 Job，返回 Job 选择器。

### 5.2 REST API

#### 列出 Jobs

```
GET /api/datasets/collections/{collection_id}/jobs
    ?package_id=681460
    &status=open
    &task_family=wipe_clean
```

响应：

```json
{
  "collection_id": "unifranka",
  "jobs": [
    {
      "job_id": "unifranka-681460-wipe_clean-sample30",
      "display_name": "681460 · wipe_clean · 抽样 30",
      "package_id": "681460",
      "task_family": "wipe_clean",
      "schema_id": "wipe_clean_v1",
      "schema_ref": "wipe_clean_v1@1",
      "scope": "sample",
      "episode_count": 30,
      "annotated_count": 0,
      "status": "open"
    }
  ]
}
```

#### 获取单个 Job（含 episode 队列）

```
GET /api/datasets/collections/{collection_id}/jobs/{job_id}
```

响应片段：

```json
{
  "job_id": "unifranka-681460-wipe_clean-sample30",
  "schema": { "schema_id": "wipe_clean_v1", "subtask_labels": [ "..."] },
  "schema_ref": "wipe_clean_v1@1",
  "episodes": [
    {
      "episode_index": 562,
      "task_text": "Clean the table with a broom.",
      "task_family": "wipe_clean",
      "schema_ref": "wipe_clean_v1@1",
      "annotation_status": "none",
      "duration": 12.4
    }
  ],
  "annotation_progress": { "complete": 0, "partial": 0, "total": 30 }
}
```

#### 加载数据集（修改现有）

```
POST /api/dataset/load
```

请求体扩展：

```json
{
  "source": "local",
  "local_path": "/data/datasets/unifranka/681460",
  "collection_id": "unifranka",
  "package_id": "681460",
  "job_id": "unifranka-681460-wipe_clean-sample30",
  "video_key": "observation.images.exterior_image_1"
}
```

#### 数据集摘要（修改现有）

```
GET /api/dataset/info
```

响应变更：

| 字段 | 变更 |
|------|------|
| `active_job` | **新增** — 当前 Job 元数据 |
| `annotation_schema` | 改为 **当前 episode** 的 schema（随 episode 切换变） |
| `episodes` | **仅** Job 队列内 episode；每项含 `task_family`, `schema_ref` |
| `schema_ref`（顶层） | 等同 `active_job.schema_ref` |

#### Episode 标注（修改现有）

```
GET  /api/episodes/{episode_index}/annotations
POST /api/episodes/{episode_index}/annotations
```

- GET 响应增加：`task_family`, `schema_ref`, `resolved_schema`（或与顶层 schema 合并）
- POST 校验使用 `resolve_schema_for_episode(...)` 结果
- 写入 `lerobot_annotations.json` 时每 episode 存 **`schema_ref`**（见 §6）

#### 解析预览（可选调试）

```
GET /api/episodes/{episode_index}/resolve?job_id=...
```

返回 `task_text`, `task_family`, `schema_ref`, `in_job: bool`。

### 5.3 废弃 / 降级

| 原行为 | 新行为 |
|--------|--------|
| 打开 package 即标全包 | **禁止**（混杂包）；须经 Job |
| Package 行 `annotation_schema` | 仅 catalog 预览；标注 UI 忽略 |
| 手工替换 `annotation.schema.json` 整包切 schema | **废弃**；改用 Job |

---

## 6. 存储格式变更

### 6.1 `lerobot_annotations.json`（每 episode）

在现有 `episodes["{idx}"]` 下**推荐**持久化：

```json
{
  "version": 3,
  "schema_ref": "pick_place_v1@1",
  "episodes": {
    "42": {
      "schema_ref": "wipe_clean_v1@1",
      "task_family": "wipe_clean",
      "job_id": "unifranka-681460-wipe_clean-sample30",
      "subtasks": [ "..." ],
      "outcome": "success"
    }
  }
}
```

| 字段 | 级别 | 说明 |
|------|------|------|
| 根 `schema_ref` | 可选 | 单族 legacy；多族时仅作默认提示 |
| `episodes[].schema_ref` | **推荐必填**（保存时写入） | 导出与 QC 依据 |
| `episodes[].task_family` | 推荐 | 冗余可加速导出 |
| `episodes[].job_id` | 可选 | 追溯派单来源 |

**向后兼容：** 无 `episodes[].schema_ref` 的旧数据，导出时用 `resolve_schema_for_episode` 回填。

### 6.2 物理文件位置（不变）

```
datasets/unifranka/681460/meta/lerobot_annotations.json   ← 仍在 package 内
datasets/unifranka/annotation.jobs.json                   ← Collection 级
datasets/unifranka/task-family-map.json                   ← 已有
```

---

## 7. 导出契约扩展（`export_contract@1.1` 建议）

在 `episodes_meta.parquet` **新增列**（与 [export-contract.md](./export-contract.md) §6 对齐）：

| 列 | 类型 | 说明 |
|----|------|------|
| `task_family` | string/null | 如 `wipe_clean` |
| `task_text` | string/null | L0 冗余，便于训练筛选 |
| `schema_ref` | string | **每 episode 独立**（不再整包单一值） |
| `job_id` | string/null | 可选追溯 |

`meta/subtasks.parquet`：**按 schema_ref 分表或分文件**（v1.1 可选）；v1.0 可维持全局 lookup，但训练侧 **必须** 用 `schema_ref` 过滤后再 join `subtask_index`。

导出 Job 范围：

```
POST /api/export
{
  "job_id": "unifranka-681460-wipe_clean-sample30",
  "output_name": "unifranka_681460_wipe_sample30_annotated"
}
```

未指定 `job_id` 时：单族 full Job 默认；混杂包 **要求** 指定 Job 或 `task_family`。

---

## 8. 前端 UX 规范

### 8.1 导航流程（标注员）

```
选择 Collection → 选择 Job（非 Package）→ Episode 列表（已过滤）→ 标注
```

- **Package** 仅作为 Job 标题的一部分出现，**不作为一级入口**（混杂 collection）。
- 侧栏固定显示：
  - **Job：** `681460 · wipe_clean · 抽样 30`
  - **Schema：** `wipe_clean_v1`（7 相，灰字只读）
  - **L0：** 当前 episode `task_text`（只读）
  - **进度：** `12 / 30 complete`

### 8.2 Episode 列表

- 默认只列 Job 内 episode；支持按 `annotation_status` 筛选。
- **禁止**「跳到 Job 外 episode」的翻页；URL 篡改返回 403。
- 同 `task_text` 连续排列（`stratify` 排序），便于批量标。

### 8.3 时间轴面板

- `subtask_labels` 完全来自 **当前 episode 的 resolved schema**。
- 切换 episode 时若 schema 变化（仅 explicit 多族 Job 不允许；单 Job 内 schema 恒定），热更新标签栏。

### 8.4 Job 选择器（Collection 页）

| 列 | 说明 |
|----|------|
| Job 名称 | `display_name` |
| 族 | `task_family` |
| 范围 | `30 ep · sample` / `1000 ep · full` |
| 进度 | 环形图或 `annotated/total` |
| 状态 | open / paused / closed |

**681460 / 681485：** 仅列出已 `open` 的 Job；未生成 Job 的族 **不可点**。

---

## 9. 与 UniFranka 执行排期对齐

| 阶段 | 标注 | 平台 |
|------|------|------|
| **现在 · P0** | `681496 → 681458 → 681520` 继续 | **不开发**；隐式单 Job 兼容 |
| **P0 收尾** | 三包验收 | 开发 **Annotation Job v1**（本章 §4–§8） |
| **P1 标注** | 仅通过 Job 打开 `681460` pick / wipe 抽样 | Job 已上线 |
| **P2 标注** | `681485` 五族各 30 ep | 扩展 Job 清单 |

### 9.1 P0' 开发拆分（建议 3–5 人天）

| 序号 | 交付物 | 依赖 |
|------|--------|------|
| D1 | `task_family.py` + `resolve_schema_for_episode` + 单测 | `task-family-map.json` |
| D2 | `annotation.jobs.json` 格式 + `generate_annotation_jobs.py` | D1 |
| D3 | API：`/jobs`、load/info 改造、保存校验 J-01..J-03 | D1 |
| D4 | 前端：Job 选择器、过滤列表、URL `job=` | D3 |
| D5 | 导出 per-episode `task_family` / `schema_ref` | D1, export_builders |

### 9.2 不在本次范围

- 物理拆包、重写 LeRobot meta  
- 导出进度条 SSE  
- `cycles.parquet` `start_frame`/`end_frame`（431132 v1.1）  
- 训练仓内 `EmbodiedDataConfig` 实装（E2 后续）  
- pour/wipe 等全量 phase boundary 长文（标前按需补）

---

## 10. 验收标准

### 10.1 功能

- [ ] 标注员从 Collection 页**只选 Job**即可开工，无需打开映射表  
- [ ] `681460` wipe Job 内所有 episode 显示 **7 相 wipe_clean**，无 `lift`/`transport`  
- [ ] 同一 package 的 pick Job 与 wipe Job **互不可见**对方 episode  
- [ ] 保存后 `lerobot_annotations.json` 含 per-episode `schema_ref`  
- [ ] 导出 `episodes_meta.parquet` 每行 `task_family` + `schema_ref` 正确  
- [ ] 旧链 `?collection=unifranka&package=681496` 自动进入唯一 full Job，P0 无感  
- [ ] 混杂包无 `job=` 时返回 Job 选择器，**不**进入标注时间轴  

### 10.2 负向 / 安全

- [ ] 向 wipe Job POST pick 相位 label → **400**  
- [ ] Job 外 `episode_index` 的视频/标注 API → **403**  
- [ ] 未映射 `task_text` 的 episode → 列表标红，不可保存 L2  

### 10.3 测试

- [ ] `test_task_family.py` — 映射表全覆盖 80 tasks  
- [ ] `test_annotation_jobs.py` — Job 过滤、抽样可复现  
- [ ] `test_resolve_schema_episode.py` — J-01..J-03  
- [ ] `test_export_parquet.py` — per-episode `schema_ref` / `task_family`  

---

## 11. `annotation-schema-spec.md` 修订要点（评审后同步）

| 原 §2 优先级 | 新优先级 |
|--------------|----------|
| packages[].annotation_schema | **deprecated** 作标注依据；仅 legacy catalog |
| collection.annotation_schema | 同上 |
| leaf annotation.schema.json | 单族 legacy fallback |
| **新增** Job.schema_id → schema registry | **标注主路径** |
| **新增** episode → task_family-map | resolve 输入 |

---

## 12. 参考：大厂对照

| 实践 | Tesla / Figure 类平台 | 本规范 |
|------|----------------------|--------|
| Raw corpus | 按 session/shard 存储，可多任务 | LeRobot package 不动 |
| Work order | 单 skill template / phase spec | Annotation Job + 单 schema |
| Annotator UX | 不暴露 shard 内其他任务类型 | Job 过滤 episode 列表 |
| Training | 按 task_id / skill_id 筛 | `task_family` + `schema_ref` + L0 |
| QC | 工单内 10% 抽检 | Job 级 `annotation_progress` |

---

## 13. 文档变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0 | 2026-07-20 | 初稿：Job 模型、resolve_schema(episode)、API/URL、导出扩展、P0' 排期 |
| 1.0.1 | 2026-07-20 | **D1 落地：** `task_family.py`、`annotation_job.py`、`tests/test_task_family.py`（16 tests） |
| 1.0.2 | 2026-07-20 | **D2 落地：** `annotation_jobs_builder.py`、`tools/generate_annotation_jobs.py`、UniFranka `annotation.jobs.json`（12 jobs） |
| 1.0.3 | 2026-07-20 | **D3 落地：** Job API、`load`/`info`/保存接入 `resolve_schema_for_episode`、J-01..J-03 |

---

**评审确认项（请勾选）：**

- [ ] Job 清单放 `annotation.jobs.json`（Collection 级）  
- [ ] 混杂包禁止无 Job 加载  
- [ ] `scope=sample` 抽样结果持久化到 `episode_indices`  
- [ ] 导出 `export_contract@1.1` 增加 `task_family` / per-episode `schema_ref`  
- [ ] P0 继续，P0' 开发紧随三包验收  
