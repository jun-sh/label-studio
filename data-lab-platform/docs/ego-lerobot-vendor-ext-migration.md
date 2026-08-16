# Ego LeRobot v3 商用扩展迁移（MCAP 剥离 + high_freq IMU）

## 决策摘要

| 项 | 决策 |
|----|------|
| MCAP / ROS2 侧车 | **彻底删除**（不再维护导出、viewer、derive hook） |
| 200Hz IMU 采集 | **保留**（130 `imu_raw.jsonl` 仍为 tar.zst 成员） |
| 200Hz IMU 交付 | **迁移至** LeRobot v3 `data/.../high_freq/imu_200hz.parquet` + `vendor_meta` |
| 30Hz 基础 IMU | **保留** `observation.state[6]`（gx/gy/gz/ax/ay/az）于主 Parquet |

---

## Phase A — MCAP 删除清单（已完成）

### 删除目录/文件

- `data-lab-platform/mcap-viewer/`（Lichtblick + file browser）
- `lerobot-studio/scripts/export_segment_to_mcap.py`
- `lerobot-studio/scripts/validate_mcap_export.py`
- `lerobot-studio/scripts/package_delivery_sample.py`
- `lerobot-studio/scripts/test_export_segment_to_mcap.py`
- `lerobot-studio/scripts/ego_mcap/`（含 extrinsics JSON）

### 代码剥离

- `stream-ingest.mjs`：`mcapExportEnabled`、`spawnExportSegmentMcapSync`、derive 段内 MCAP hook
- `session-markers.mjs`：`MCAP_FAILED`、`markSessionMcapFailed`
- `Dockerfile` / `requirements-derive.txt`：`mcap`、`mcap-ros2-support`
- `docker-compose.platform.yml`、`.env`：`DERIVE_MCAP_*`
- `download-derive-python-wheels.sh`：MCAP wheel 段

### 明确保留（非 MCAP）

- `ego-stream-client`：`imu_raw.jsonl` 采集、`validate_imu_raw_jsonl.py`
- `segment_tar_zst.py`：tar 成员 `imu_raw.jsonl`（v0.0.8 契约，Phase B 前不变）

---

## Phase B — high_freq IMU（已实现）

### 目标目录布局

```text
dataset/
├── meta/info.json              # 标准 + vendor_meta
├── meta/vendor_annotations/    # 大容量分层标注（Phase D）
├── data/chunk-000/file-000.parquet     # 30Hz 主时序
├── data/chunk-000/high_freq/
│   └── imu_200hz.parquet       # 完整 gyro/accel @ ~200Hz
└── videos/observation.images.*/chunk-000/file-000.mp4
```

### `meta/info.json` — `vendor_meta` 示例

```json
{
  "codebase_version": "v3.0",
  "fps": 30,
  "features": { "...": "..." },
  "vendor_meta": {
    "schema": "datalab-ego-v1",
    "device_id": "OAK-4P-ego-001",
    "station_id": "ego-001",
    "capture_batch_id": "sess_adeec0b9d2b5470596ff643793c55499",
    "calibration_version": "calib-v1",
    "imu_nominal_hz": 200,
    "high_freq": {
      "imu_200hz": {
        "path": "data/chunk-000/high_freq/imu_200hz.parquet",
        "rate_hz": 200,
        "sensors": ["gyro", "accel"],
        "source": "imu_raw.jsonl"
      }
    },
    "trace_chain": []
  }
}
```

### `imu_200hz.parquet` 列 schema（草案）

| 列 | 类型 | 说明 |
|----|------|------|
| `ts_ns` | int64 | 单调时间戳（与 `imu_raw.jsonl` 一致） |
| `sensor` | string | `gyro` \| `accel` |
| `x`, `y`, `z` | float32 | 原始读数 |
| `episode_index` | int64 | 可选，便于 episode 切片 |
| `segment_id` | string | 可选，溯源段 ID |

行数预期：约 `jsonl_rows × (200/30) × 2`（gyro+accel 分行存储时）。

### 转换挂载点（推荐）

在 **derive READY 前** 或 **segment_mp4 ingest 批处理末**：

1. 从已解压段目录或 `raw/segments/*.tar.zst` 读取 `imu_raw.jsonl`
2. 写入 `data/chunk-000/high_freq/imu_200hz.parquet`（原子 tmp → rename）
3. 合并更新 `meta/info.json` 的 `vendor_meta.high_freq`
4. 校验：`validate_imu_raw_jsonl` 规则 + 行数 ≥ 段时长 × 180Hz（容忍）

脚本：`lerobot-studio/scripts/ingest-imu-high-freq.py`（derive segment_mp4 路径在 parquet sync 后自动调用；亦可单独回填）。

挂载点：`derive-pipeline.mjs` → `spawnImuHighFreqIngestSync`；`sync-stream-parquet.py` / `stream-ingest.mjs` 的 parquet 行数统计已排除 `high_freq/`。

### 30Hz 主时序（不变）

- `observation.state[6]`：帧网格对齐的 IMU 摘要（训练默认可用）
- 与 `high_freq` 关系：基础兼容层 vs 商用增值层，不互相覆盖

---

## Phase C — 历史回填（已实现）

对已有 `raw/segments/<sess>/*.tar.zst`（READY 会话 derive 不会重跑 IMU ingest，需手动一次）：

```bash
python3 lerobot-studio/scripts/ingest-imu-high-freq.py \
  data-storage/stream/ego-001 \
  --session sess_adeec0b9d2b5470596ff643793c55499
```

---

## Phase D — vendor_annotations（已实现）

- `meta/vendor_annotations/subtask_segments.parquet` — 由 `meta/annotations.parquet` 聚合连续子任务段
- `meta/vendor_annotations/contact_pixel_per_frame.parquet` — 骨架表（待 embodied-annotate 写入接触点）
- `meta/vendor_annotations/manifest.json` — 制品清单
- derive segment_mp4 路径在 IMU ingest 后自动调用 `sync-vendor-annotations.py`
- `vendor_meta.vendor_annotations` 写入 `meta/info.json`

脚本：`lerobot-studio/scripts/sync-vendor-annotations.py`

历史回填：

```bash
python3 lerobot-studio/scripts/sync-vendor-annotations.py data-storage/stream/ego-001
```

---

## 验收

```bash
# 平台回归（无 MCAP + high_freq）
RC_STATION=ego-001 data-lab-platform/scripts/ci-ego-platform.sh

# RC-5 单独跑
RC_STATION=ego-001 data-lab-platform/scripts/rc-high-freq-imu.sh
```
