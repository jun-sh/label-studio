# Training Export Contract (E0)

> **Version:** `export_contract@1.0`  
> **Status:** Frozen — training code MUST only read files listed in §2.

## 1. Purpose

Separate **annotation storage** (`meta/lerobot_annotations.json` on the source dataset) from **training consumption** (Parquet-only export bundle). Aligns with π₀.₅ / GR00T-N1.5 / Tesla-Figure production pipelines.

## 2. Training whitelist (Parquet only)

| File | Role | Join key |
|------|------|----------|
| `data/**/*.parquet` | LeRobot frame tensors + `subtask_index`, `skill_index` | `episode_index` |
| `meta/cycles.parquet` | Per-box `success`, `fail_reason`, time bounds | `episode_index`, `cycle_id` |
| `meta/episodes_meta.parquet` | Episode `outcome`, `box_cycle`, annotation status | `episode_index` |
| `meta/subtasks.parquet` | L2 phase label → `subtask_index` lookup | `subtask` (string id) |
| `meta/export_manifest.json` | Contract version, enums, row counts | — |
| `meta/info.json` | LeRobot dataset features (updated with index columns) | — |
| `meta/episodes/**` | Native LeRobot episode metadata (unchanged) | `episode_index` |
| `meta/tasks.parquet` | Task text lookup (unchanged) | `task_index` |
| `videos/**` | Video files (symlink or copy) | — |

## 3. Training blacklist (never read in training / RL loops)

| File | Reason |
|------|--------|
| `meta/lerobot_annotations.json` | Annotation SoT; debug/QC only on source dataset |
| `meta/skills.parquet` | `subgoal` text is JSON-debug only (Figure Helix-02 parity) |

## 4. Foreign keys

- **`episode_index`** (int64): primary join key across frames, `cycles.parquet`, `episodes_meta.parquet`.
- **`cycle_id`** (int64): per-episode box index within `cycles.parquet`.

Predicate pushdown example (PyArrow):

```python
import pyarrow.dataset as ds

cycles = ds.dataset("meta/cycles.parquet")
slip = cycles.to_table(filter=ds.field("fail_reason") == "slip_grasp")
episode_ids = slip.column("episode_index").to_pylist()
```

## 5. Frozen enums

### `outcome` (episodes_meta)

`success` | `fail` | `partial`

### `fail_reason` (cycles)

`none` | `slip_grasp` | `drop_mid_transport` | `place_offset` | `incomplete` | `other`

### `annotation_status` (episodes_meta)

`none` | `partial` | `complete`

## 6. Schema snapshots

### `meta/cycles.parquet`

| Column | Type | Notes |
|--------|------|-------|
| `episode_index` | int64 | FK |
| `cycle_id` | int64 | 0-based per episode |
| `start` | float64 | seconds |
| `end` | float64 | seconds |
| `complete` | bool | L2 structure complete |
| `success` | bool/null | on-conveyor success |
| `fail_reason` | string | enum §5 |
| `success_source` | string | `auto` \| `manual` |

### `meta/episodes_meta.parquet`

| Column | Type | Notes |
|--------|------|-------|
| `episode_index` | int64 | PK |
| `outcome` | string/null | enum §5 |
| `box_cycle` | int64/null | successful box count |
| `task_index` | int64/null | LeRobot task index |
| `annotated` | bool | true if partial or complete |
| `annotation_status` | string | enum §5 |
| `schema_ref` | string | e.g. `box_transport_v1@1` |

## 7. Annotation vs export

| Stage | Writes | Reads |
|-------|--------|-------|
| Embodied UI save | Source `meta/lerobot_annotations.json` | Same |
| Export to local | Training bundle (§2 only) | Source JSON + in-memory annotations |
| Training / RL | §2 whitelist | Never §3 |

## 8. Changelog

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-07-16 | Initial freeze: `cycles.parquet`, `episodes_meta.parquet`, drop JSON/skills from export |
