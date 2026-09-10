# EGO Training Export Contract (P0 Commercial)

> **Version:** `ego_export_contract@1.0`  
> **Status:** Frozen — customer training code MUST only read files listed in §2.

## 1. Purpose

Define the minimal deliverable for commercial EGO human-demo datasets. Aligns with operation-arm `export_contract@1.0` structure; EGO-specific fields only where required (`pose_ready`, `video_codec`).

## 2. Training whitelist (Parquet + videos)

| Path | Role |
|------|------|
| `data/**/*.parquet` | LeRobot frame table |
| `videos/**` | Per-camera MP4 |
| `sensor_raw/imu/**/*.parquet` | 200 Hz raw IMU (unit layout) |
| `meta/info.json` | LeRobot features (`codebase_version: v3.0`) |
| `meta/episodes/**` | Episode metadata |
| `meta/episodes_meta.parquet` | Episode `outcome`, `annotation_status`, `schema_ref` |
| `meta/tasks.parquet` | Task lookup |
| `meta/export_manifest.json` | Contract version, episode list, enums |

## 3. Training blacklist

| Path | Reason |
|------|--------|
| `meta/lerobot_annotations.json` | Annotation SoT; QC/debug only |
| `state/**`, `derived/**`, `raw/**` | Internal pipeline artifacts |

## 4. Frozen delivery fields (`meta/export_manifest.json`)

| Field | Type | Values |
|-------|------|--------|
| `export_contract_version` | string | `1.0` |
| `pose_ready` | bool | `true` required for export |
| `video_codec` | string | `jpeg` \| `h264` |
| `pipeline_version` | string | derive/convert version |
| `episode_index` | int64 | LeRobot episode index |
| `source_session_id` | string | `sess_*` |

### `outcome` (episodes_meta)

`success` | `fail` | `partial`

### `annotation_status` (episodes_meta)

`none` | `partial` | `complete`

## 5. Delivery gates (hard)

1. `session.READY` (preview_ready)
2. `finalize.done` (pose_ready)
3. QC review `approved` (`EGO_EXPORT_REQUIRE_QC=1` default)
4. Not `rejected` / `suspicious`
5. `annotation_status=complete`, `outcome` set, L2 subtasks with leading/trailing `idle`
6. Export scope: **order whitelist only** (`--order-manifest`); not full-station copy

Order manifest (`config/ego-order-manifest.example.json`):

```json
{
  "order_id": "ORD-2026-0001",
  "customer_id": "CUST-PILOT-01",
  "session_ids": ["sess_YYYYMMDD_HHMMSS"]
}
```

Output: `data-storage/ego-delivery/orders/{order_id}/episodes/{sess_*}/` + `acceptance_report.json`.

## 6. Changelog

| Version | Date | Change |
|---------|------|--------|
| 1.1 | 2026-09-04 | Order-scoped per-episode export; annotation gate; acceptance_report |
| 1.0 | 2026-09-04 | Initial P0 commercial freeze |
