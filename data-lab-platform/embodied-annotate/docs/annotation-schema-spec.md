# Embodied Annotate — 标注 Schema 规范（v2 定稿 · 讨论稿）

> **状态：** 规范定稿，**尚未在代码中实现**  
> **适用范围：** `collection.manifest.json`、leaf 数据集 `annotation.schema.json`、标注存储 `lerobot_annotations.json`  
> **目标：** 标签集与 Episode 扩展字段按数据集配置，不写死在 `app.js`

---

## 1. 设计原则

| 原则 | 说明 |
|------|------|
| **Schema 驱动** | UI 标签、校验规则、Episode 表单均由 schema 渲染 |
| **集合默认 + 包可覆盖** | `collection.manifest.json` 定义默认；`packages[].annotation_schema` 可覆盖 |
| **向后兼容** | 无 schema 时使用内置 `default`；annotations `version: 1` 继续可读 |
| **任务无关字段不进全局 UI** | `box_cycle` 仅在声明了该 field 的数据集显示 |
| **存储与 UI 分离** | schema 描述「允许什么」；`lerobot_annotations.json` 存「标了什么」 |

---

## 2. 文件位置与优先级

解析顺序（从高到低）：

```
1. packages[].annotation_schema     （子包覆盖）
2. collection.annotation_schema     （集合默认）
3. <dataset_root>/annotation.schema.json   （leaf 数据集）
4. 内置 builtin://default_manipulation   （代码 fallback）
```

**Load 数据集成功后**，前端将解析结果缓存为 `state.annotationSchema`，后端校验可选使用同一套定义。

---

## 3. `annotation_schema` 顶层结构

```json
{
  "schema_id": "box_transport_v1",
  "schema_version": 1,
  "subtask_labels": [ /* SubtaskLabel[] */ ],
  "episode_fields": [ /* EpisodeField[] */ ],
  "validation": { /* ValidationRules */ },
  "timeline": { /* TimelineUX */ }
}
```

### 3.1 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `schema_id` | string | 是 | 稳定标识，写入 annotations `schema_ref` |
| `schema_version` | int | 是 | schema 自身版本，便于迁移 |
| `subtask_labels` | array | 是 | 时间轴分段标签枚举，至少 1 项 |
| `episode_fields` | array | 否 | Episode 级扩展字段；缺省则仅 subtask |
| `validation` | object | 否 | 分段校验参数 |
| `timeline` | object | 否 | 时间轴交互/视觉参数（Phase B） |

---

## 4. `SubtaskLabel` 定义

```json
{
  "id": "reach",
  "order": 1,
  "color": "#34D399",
  "label_zh": "接近",
  "label_en": "Reach",
  "hint_zh": "朝目标箱子移动，尚未接触",
  "hint_en": "Moving toward target without contact"
}
```

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| `id` | string | 是 | `[a-z][a-z0-9_]*`，保存进 `subtasks[].label` |
| `order` | int | 是 | 调色板排序 |
| `color` | string | 是 | `#RRGGBB` |
| `label_zh` / `label_en` | string | 否 | UI 展示名；缺省用 `id` |
| `hint_zh` / `hint_en` | string | 否 | 选中时提示 |

**规则：**

- `id` 在单 schema 内唯一  
- 保存时 `label` 必须是 `subtask_labels[].id` 之一（非标准可警告，见兼容策略）  
- 不同数据集可有完全不同的 `id` 集合  

---

## 5. `EpisodeField` 定义

### 5.1 通用字段

```json
{
  "id": "box_cycle",
  "type": "int",
  "required": true,
  "label_zh": "成功搬箱数",
  "label_en": "Box cycles",
  "hint_zh": "放到传送带并松手的次数",
  "min": 0,
  "max": 99,
  "default": null
}
```

### 5.2 类型枚举

| `type` | UI 控件 | 存储类型 | 额外属性 |
|--------|---------|----------|----------|
| `int` | number input | integer | `min`, `max`, `default` |
| `float` | number input | number | `min`, `max`, `step` |
| `text` | textarea / input | string | `max_length` |
| `enum` | radio / select | string | `values: string[]` |
| `bool` | checkbox | boolean | `default` |

### 5.3 `outcome` 的处理（过渡期）

**Phase A（推荐）：** `outcome` 仍用现有三个 radio（`success` / `partial` / `fail`），**不**放入 `episode_fields`，避免一次性大改。

**Phase B（可选）：** 迁入 schema：

```json
{
  "id": "outcome",
  "type": "enum",
  "values": ["success", "partial", "fail"],
  "required": true,
  "label_zh": "本集结果"
}
```

存储可双写：`episodes[n].outcome` + `episodes[n].fields.outcome`，读取优先顶层 `outcome`。

### 5.4 按任务声明字段（示例）

| 数据集 | `episode_fields` |
|--------|------------------|
| `limx_box_transport/431132` | `box_cycle` (int), `notes` (text) |
| `pusht` | 无，或仅 `notes` |
| 未来叠毛巾 | `fold_count` (int) |

---

## 6. `ValidationRules`

```json
{
  "segments_must_not_overlap": true,
  "gap_warn_frames": 10,
  "require_full_coverage": false,
  "min_segment_frames": 1
}
```

| 字段 | 默认 | 说明 |
|------|------|------|
| `segments_must_not_overlap` | `true` | 重叠 → 保存错误 |
| `gap_warn_frames` | `10` | 相邻段空隙超过此值 → 警告 |
| `require_full_coverage` | `false` | 若 true，未覆盖区间警告 |
| `min_segment_frames` | `1` | 最短段长度 |

---

## 7. `TimelineUX`（Phase B 交互）

```json
{
  "snap_adjacent_frames": 1,
  "resize_handle_px": 6,
  "enable_move": true,
  "enable_resize": true,
  "create_on_empty_track_only": true
}
```

| 字段 | 默认 | 说明 |
|------|------|------|
| `snap_adjacent_frames` | `1` | 拖边时吸附邻段边界 ±N 帧 |
| `resize_handle_px` | `6` | 左右缘命中宽度 |
| `enable_move` | `true` | 允许整体平移色块 |
| `enable_resize` | `true` | 允许拖边 |
| `create_on_empty_track_only` | `true` | 仅轨道空白处拖拽新建，避免误触 |

---

## 8. 标注存储：`lerobot_annotations.json` v2

```json
{
  "version": 2,
  "schema_ref": "box_transport_v1@1",
  "episodes": {
    "0": {
      "subtasks": [
        { "start": 0.0, "end": 4.05, "label": "idle" },
        { "start": 4.05, "end": 12.5, "label": "reach" }
      ],
      "outcome": "success",
      "fields": {
        "box_cycle": 2,
        "notes": ""
      },
      "high_levels": []
    }
  }
}
```

| 字段 | 说明 |
|------|------|
| `version` | `1` 旧格式；`2` 含 `fields` |
| `schema_ref` | `{schema_id}@{schema_version}` |
| `subtasks[].start/end` | 秒（与现有一致） |
| `outcome` | 过渡期保留顶层 |
| `fields` | 由 `episode_fields` 定义的键值 |

**读取兼容：**

- `version` 缺失 → 当 v1  
- `fields` 缺失 → 空对象  
- 未知 `label` → 显示为非标准段（现有逻辑）

---

## 9. API 契约（实现时）

### 9.1 Catalog 扩展

`GET /api/datasets/collections/{id}` 响应增加：

```json
{
  "annotation_schema": { ... },
  "packages": [
    {
      "id": "431132",
      "annotation_schema": null
    }
  ]
}
```

`null` 表示继承集合默认。

### 9.2 Load 响应扩展

`POST /api/dataset/load` 在 summary 中增加：

```json
{
  "annotation_schema": { ... },
  "schema_ref": "box_transport_v1@1"
}
```

### 9.3 Annotations 读写

`GET/POST /api/episodes/{id}/annotations`：

```json
{
  "episode_index": 0,
  "subtasks": [],
  "outcome": "partial",
  "fields": { "box_cycle": 1, "notes": "第二箱未搬完" }
}
```

POST 校验：

- `fields` 中每个 key 须在 `episode_fields` 声明内  
- `required: true` 的字段不能为空  
- `type` / `min` / `max` / `enum` 校验  

---

## 10. 完整示例：`limx_box_transport` manifest v2

```json
{
  "schema_version": 2,
  "collection_id": "limx_box_transport",
  "title": "limx_box_transport",
  "description": "搬箱至传送带 · LimX Oli · LeRobot v3.0",
  "robot_type": "LimX_Oli",
  "task_family": "box_transport",
  "annotation_schema": {
    "schema_id": "box_transport_v1",
    "schema_version": 1,
    "subtask_labels": [
      { "id": "idle", "order": 0, "color": "#9CA3AF", "label_zh": "空闲", "hint_zh": "等待，未参与搬箱" },
      { "id": "reach", "order": 1, "color": "#34D399", "label_zh": "接近", "hint_zh": "朝目标箱移动，尚未接触" },
      { "id": "pre_grasp", "order": 2, "color": "#6EE7B7", "label_zh": "预抓取", "hint_zh": "对准、张开、准备抓" },
      { "id": "contact", "order": 3, "color": "#FB923C", "label_zh": "接触", "hint_zh": "碰到箱子至抓稳前" },
      { "id": "lift", "order": 4, "color": "#60A5FA", "label_zh": "抬起", "hint_zh": "箱子离开支撑面" },
      { "id": "transport", "order": 5, "color": "#3B82F6", "label_zh": "搬运", "hint_zh": "持箱移向传送带" },
      { "id": "place", "order": 6, "color": "#A78BFA", "label_zh": "放置", "hint_zh": "朝传送带下降，未松手" },
      { "id": "release", "order": 7, "color": "#F472B6", "label_zh": "释放", "hint_zh": "松手，箱脱离" }
    ],
    "episode_fields": [
      {
        "id": "box_cycle",
        "type": "int",
        "required": true,
        "label_zh": "成功搬箱数",
        "hint_zh": "本集成功放上传送带并松手的次数",
        "min": 0,
        "max": 20
      },
      {
        "id": "notes",
        "type": "text",
        "required": false,
        "label_zh": "备注",
        "max_length": 500
      }
    ],
    "validation": {
      "segments_must_not_overlap": true,
      "gap_warn_frames": 10
    },
    "timeline": {
      "snap_adjacent_frames": 1,
      "enable_move": true,
      "enable_resize": true
    }
  },
  "packages": [
    {
      "id": "431132",
      "display_name": "头摄 + 全身位姿",
      "local_path": "431132",
      "episodes": 780,
      "annotation_status": "raw"
    }
  ]
}
```

---

## 11. 示例：`pusht` fallback schema

置于 `datasets/pusht/annotation.schema.json` 或内置默认：

```json
{
  "schema_id": "pusht_v1",
  "schema_version": 1,
  "subtask_labels": [
    { "id": "idle", "order": 0, "color": "#9CA3AF", "label_zh": "空闲", "hint_zh": "未推动滑块" },
    { "id": "reach", "order": 1, "color": "#34D399", "label_zh": "接近", "hint_zh": "移向滑块/目标" },
    { "id": "push", "order": 2, "color": "#3B82F6", "label_zh": "推动", "hint_zh": "接触并推动滑块" },
    { "id": "reposition", "order": 3, "color": "#A78BFA", "label_zh": "调整", "hint_zh": "微调位置" }
  ],
  "episode_fields": [],
  "validation": {
    "gap_warn_frames": 10
  }
}
```

**无 `box_cycle`** → UI 不显示箱数字段。

---

## 12. 时间轴交互规范（Phase B 实现清单）

### 12.1 指针模式状态机

```
idle
  ├─ mousedown on track empty     → create_drag
  ├─ mousedown on bar center      → move_drag
  ├─ mousedown on bar left edge   → resize_start_drag
  └─ mousedown on bar right edge  → resize_end_drag

create_drag / move_drag / resize_*_drag
  └─ mouseup → commit or cancel
```

### 12.2 吸附算法

```
候选边界 B ∈ { 0, maxFrame, 邻段.end+1, 邻段.start-1 }
若 |pointerFrame - B| ≤ snap_adjacent_frames → 使用 B
```

### 12.3 与 Regions 列表关系

- 时间轴拖动 **实时更新** `subtasks` 内存  
- `mouseup` 后刷新 Regions 数字框  
- Regions 改数字 **同步** 时间轴色块  
- 单一数据源：`ann.subtasks`

### 12.4 视觉（轻量 LS 对齐）

- 色块左右 `ew-resize` 光标 + 2px 竖线手柄  
- 选中态提高不透明度（已有 `--ls-region-opacity-selected`）  
- 条内默认不显示长 `id`，hover 显示 `label_zh + 帧范围`  
- 标签 chips 显示 `label_zh`

---

## 13. 实现阶段与文件触点（供开发）

### Phase A — Schema 平台化（3～4 人天）

| 文件 | 改动 |
|------|------|
| `collection.manifest.json` | 增加 `annotation_schema` |
| `dataset_catalog.py` | 解析并透传 schema；包级覆盖合并 |
| `app.py` | `EpisodeAnnotations` + `fields`；读写 v2 JSON |
| `app.js` | 删除硬编码 `SUBTASK_LABELS`；改读 `state.annotationSchema` |
| `index.html` | 动态 episode fields 容器 |
| `i18n.js` | 优先 schema 的 `label_zh` |

### Phase B — 时间轴交互（3～4 人天）

| 文件 | 改动 |
|------|------|
| `app.js` | 状态机、resize/move、snap |
| `styles.css` | 手柄、光标、tooltip |
| `annotation-schema-spec.md` | `timeline` 节与实现核对 |

### Phase C — 抛光（1 人天）

- 进度：episode 列表已标/未标  
- 保存时 `box_cycle` vs `outcome` 软校验  
- 培训文档更新

---

## 14. 迁移与风险

| 风险 | 缓解 |
|------|------|
| 旧 annotations 无 `fields` | 读取默认空；导出不要求 box_cycle |
| 旧 label 不在新 schema | 标为非标准；质检批量迁移 |
| pusht 与搬箱共用旧 8 类 | 为 pusht 单独 schema 后重新标或映射表 |
| schema 与代码版本漂移 | `schema_ref` + manifest `schema_version` |

---

## 15. JSON Schema（机器校验用 · 附录）

实现 CI 时可用下列 Draft 校验 `annotation_schema` 对象：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://data-lab.local/schemas/annotation_schema.json",
  "type": "object",
  "required": ["schema_id", "schema_version", "subtask_labels"],
  "properties": {
    "schema_id": { "type": "string", "pattern": "^[a-z][a-z0-9_]*$" },
    "schema_version": { "type": "integer", "minimum": 1 },
    "subtask_labels": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "required": ["id", "order", "color"],
        "properties": {
          "id": { "type": "string", "pattern": "^[a-z][a-z0-9_]*$" },
          "order": { "type": "integer" },
          "color": { "type": "string", "pattern": "^#[0-9A-Fa-f]{6}$" },
          "label_zh": { "type": "string" },
          "label_en": { "type": "string" },
          "hint_zh": { "type": "string" },
          "hint_en": { "type": "string" }
        }
      }
    },
    "episode_fields": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "type"],
        "properties": {
          "id": { "type": "string", "pattern": "^[a-z][a-z0-9_]*$" },
          "type": { "enum": ["int", "float", "text", "enum", "bool"] },
          "required": { "type": "boolean" },
          "label_zh": { "type": "string" },
          "values": { "type": "array", "items": { "type": "string" } },
          "min": { "type": "number" },
          "max": { "type": "number" }
        }
      }
    }
  }
}
```

---

## 16. 与 431132 培训 SOP 的对应

| SOP 要求 | Schema 落点 |
|----------|-------------|
| 8 类 subtask | `subtask_labels` |
| outcome | 顶层 `outcome`（Phase A） |
| box_cycle | `episode_fields[box_cycle]` |
| notes / DATA_ISSUE | `episode_fields[notes]` |
| 间隙 >10 帧警告 | `validation.gap_warn_frames` |
| 拖边/吸附 | `timeline.*`（Phase B） |

培训文档路径：`docs/431132-annotation-sop-training.md`（Phase A 完成后改「旁路表」一节为工具内填写）。

---

*文档版本：2026-07-12 · 维护：数据平台 / embodied-annotate*
