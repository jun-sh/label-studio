# EGO-001 Phase 0 设计冻结文档

**状态：** FROZEN（唯一开发基准，禁止口头变更）  
**版本：** Phase0-v1.0  
**基线镜像：** `data-lab-lerobot-studio:v0.0.11.2`  
**目标正式版：** `v0.0.13`  
**开发分支：** `feat/ego-001-foxglove-refactor`  
**适用范围：** 仅 `ego-001` 商用主链路  
**冻结日期：** 2026-08-18  

---

## 0. 文档目的与约束

本文档是 EGO-001 采集→上传→派生链路 Foxglove 范式重构的**唯一权威设计基准**。Phase 1–7 所有实现必须严格遵循本文档，不得：

- 修改对外契约（CLI 签名、API、目录交付形态、运维脚本入口）
- 引入兼容双写、软链接、临时 flag、defer 补丁
- 保留 `ego-lan-214`、`H264`、`segment_mp4`、`high_freq/` 路径

**已拍板决策摘要：**

| # | 决策 | 方案 |
|---|------|------|
| 1 | IMU 路径 | 硬切至 `sensor_raw/imu/`，废弃 `data/.../high_freq/` |
| 2 | Manifest | `manifest_schema_version: 2`，读兼容旧 bool，写仅 `status` |
| 3 | 帧映射 | `meta/episodes/episode_000.json` → `frame_segments` |
| 4 | 代码结构 | 新建 `ingest/`、`derive/`，`stream-ingest.mjs` 仅路由 |
| 5 | 部署 | 镜像唯一源，禁用 bind-mount；最终 `v0.0.13` |

**验收优先级（冻结）：**

1. 常规全链路（采集→上传→derive→READY→Viewer）
2. 重置重传（reset 保留 raw、`--force` 状态一致）
3. 多 Session 合并帧索引连续
4. IMU 双存储合规

---

## 1. 系统边界与不变契约

### 1.1 130 采集输入（不可改动）

| 项 | 固定值 |
|----|--------|
| 环境变量 | `OAK_HW_JPEG=1`、`OAK_H264=0`、`SEGMENT_FRAME_BIN=1`、`SEGMENT_H264=0` |
| 段产物 | `manifest.json`、`rows.jsonl`、`imu_raw.jsonl`、`frames/*.bin` |
| 上传协议 | `tar.zst` |
| 上传 URL | `http://10.10.10.34:8080/lerobot/api/collection/stations/ego-001/upload` |
| CLI | `upload_segments.py --ensure-session --force --segment-root` |
| 上传模式 | 仅手动，永久禁用自动 upload loop |
| systemd | `ecs-record-oak-stream.service` 启停行为不变 |

### 1.2 34 平台输出（不可改动 + 合规扩展）

```text
data-storage/stream/ego-001/
├─ meta/info.json
├─ meta/episodes/episode_000.json          # 含 frame_segments 全局映射
├─ data/chunk-000/file-000.{jsonl,parquet} # 30Hz 主表（一行一帧）
├─ videos/<camera_key>/chunk-000/file-000.mp4
├─ raw/segments/sess_*/                    # 原始 tar.zst（唯一可信源，永久不删）
├─ sensor_raw/imu/chunk-000/file-000.parquet  # 【新】200Hz 原始 IMU
└─ pipeline/corpus/                        # 后处理成品（可重建）
```

**明确废弃（Phase 7 删除）：**

```text
data/chunk-000/high_freq/imu_200hz.parquet   # v0.0.11 旧路径，硬切废弃
meta/info.json → vendor_meta.high_freq       # 不再写入
```

### 1.3 Session 对外状态（不可改动）

对外可见 Session 状态枚举保持不变：

```text
UPLOADING → DONE_UPLOAD → DERIVING → READY | FAILED
```

持久化 marker 文件（`state/sessions/<sessionId>/`）保持现有命名：

- `session.DONE_UPLOAD`
- `session.DERIVING`
- `session.READY`
- `session.FAILED`

marker payload 保留字段：`phase`、`mp4Ok`、失败 `message`/`reason`。

### 1.4 运维脚本（入口签名不变）

| 脚本 | Phase 6 内部行为变更 |
|------|---------------------|
| `ego-deliver` | 无签名变更 |
| `ego-run-pipeline` | 无签名变更；后处理读 `sensor_raw/imu` |
| `ego-derive` | 无签名变更 |
| `ego-reset-34-only.sh` | **仅删派生产物，保留 `raw/segments/`** |
| `ego-001-reset-for-rerun.sh` | 保留核心 raw 归档 |

---

## 2. 三层实体模型

```text
Segment  ──N:1──▶  Session  ──N:1──▶  Episode
(最小数据单元)      (单次采集)         (业务样本，可多 Session 合并)
```

| 实体 | 130 侧 | 34 侧 | 可信源 |
|------|--------|-------|--------|
| Segment | 本地目录 + manifest | `raw/segments/<session>/<seg>.tar.zst` + segment state | 130 tar.zst → 34 raw 归档 |
| Session | `sessions/<sessionId>/` | `state/sessions/<sessionId>/` markers | marker 文件 |
| Episode | 无（仅 session_id） | `meta/episodes/episode_000.json` | frame_segments 映射表 |

**派生 vs 原始隔离原则：**

- **可信源（不可删）：** `raw/segments/*.tar.zst`
- **可重建派生：** `data/`、`videos/`、`sensor_raw/`、`staging/`、`live/derive/`
- **后处理派生：** `pipeline/corpus/`

---

## 3. 状态机规范

### 3.1 130 Segment 状态机（manifest.json）

#### 3.1.1 状态枚举

```python
SegmentStatus = Literal[
    "RECORDING",      # 采集中
    "CLOSED",         # 段关闭，可上传
    "UPLOADING",      # 上传进行中
    "UPLOADED",       # 34 确认接收
    "UPLOAD_FAILED",  # 上传失败，可重试
    "CORRUPT",        # 完整性自检失败（崩溃残留/截断/缺文件）
]
```

> `CORRUPT` 为新增终态，不可上传、不可 GC，需人工介入或 `--force` 覆盖前修复。

#### 3.1.2 合法状态转移

| 从 | 到 | 触发条件 | 持久化位置 |
|----|-----|----------|-----------|
| — | `RECORDING` | `open_segment()` 创建段 | 130 `manifest.json` |
| `RECORDING` | `CLOSED` | 达到帧数/时间上限或 session 关闭 | 130 `manifest.json` |
| `RECORDING` | `CORRUPT` | 崩溃残留自检失败 | 130 `manifest.json` |
| `CLOSED` | `UPLOADING` | `upload_segments` 开始上传该段 | 130 `manifest.json` |
| `UPLOADING` | `UPLOADED` | HTTP 200 + 34 ACK | 130 `manifest.json` |
| `UPLOADING` | `UPLOAD_FAILED` | 网络/HTTP 错误/超时 | 130 `manifest.json` |
| `UPLOAD_FAILED` | `UPLOADING` | 手动重试 / `--force` | 130 `manifest.json` |
| `CLOSED` | `UPLOADING` | `--force` 重传（忽略 uploaded 标记） | 130 `manifest.json` |
| `UPLOADED` | — | GC 删除本地目录（唯一可 GC 状态） | 130 文件系统 |

**禁止转移：**

- `RECORDING` → `UPLOADING`（未关闭不可传）
- 任何状态 → GC（除 `UPLOADED`）
- `CORRUPT` → `UPLOADED`（须先修复或标记为 `CLOSED` 并 force）

#### 3.1.3 manifest.json Schema v2

```json
{
  "manifest_schema_version": 2,
  "segment_id": "seg_20260818_001",
  "session_id": "sess_abc123",
  "status": "CLOSED",
  "start_frame_index": 0,
  "end_frame_index": 299,
  "frame_count": 300,
  "created_at": "2026-08-18T12:00:00.000Z",
  "closed_at": "2026-08-18T12:00:15.000Z",
  "upload": {
    "attempts": 0,
    "last_attempt_at": null,
    "last_error": null,
    "remote_ack_at": null
  },
  "integrity": {
    "checked_at": "2026-08-18T12:00:15.001Z",
    "ok": true,
    "issues": []
  }
}
```

#### 3.1.4 旧 manifest 读取兼容映射

| 旧字段 | 映射规则 |
|--------|----------|
| `closed=false` | → `RECORDING` |
| `closed=true, uploaded=false` | → `CLOSED` |
| `closed=true, uploaded=true` | → `UPLOADED` |
| 无 `manifest_schema_version` | 视为 v1，按上表映射后内存升级为 v2 |

**写入规则：** 新段关闭后只写 `status`，**不再写入** `closed`/`uploaded` bool。

#### 3.1.5 GC 铁律（130）

```text
仅 status == UPLOADED 的 Segment 可进入 GC 队列。
删除 SEGMENT_AUTO_PURGE_PENDING、SEGMENT_MAX_PENDING 对未上传段的淘汰逻辑。
```

---

### 3.2 34 Segment 状态机

#### 3.2.1 状态枚举

```javascript
const SegmentIngestStatus = {
  RECEIVED: "RECEIVED",           // tar.zst 落盘 raw/
  INGESTING: "INGESTING",         // 解压/校验/写 staging
  INGEST_FAILED: "INGEST_FAILED", // 校验或解析失败
  DERIVE_PENDING: "DERIVE_PENDING", // ingest 完成，等待 derive
};
```

#### 3.2.2 持久化位置

```text
state/segments/<sessionId>/<segmentId>.json
```

```json
{
  "segment_id": "seg_20260818_001",
  "session_id": "sess_abc123",
  "status": "DERIVE_PENDING",
  "received_at": "2026-08-18T12:01:00.000Z",
  "raw_archive": "raw/segments/sess_abc123/seg_20260818_001.tar.zst",
  "sha256": "abc...",
  "frame_count": 300,
  "integrity": { "ok": true, "checks": ["manifest", "rows.jsonl", "imu_raw.jsonl"] },
  "error": null
}
```

#### 3.2.3 合法状态转移

| 从 | 到 | 触发条件 |
|----|-----|----------|
| — | `RECEIVED` | HTTP upload 完成，tar.zst 原子落盘 `raw/segments/` |
| `RECEIVED` | `INGESTING` | derive-worker 开始处理该段 |
| `INGESTING` | `DERIVE_PENDING` | tar 校验通过 + 解压 staging 完成 |
| `INGESTING` | `INGEST_FAILED` | 缺文件/损坏/解析错误 |
| `INGEST_FAILED` | `INGESTING` | `derive-retry` API 或 `ego-derive` 重试 |
| `DERIVE_PENDING` | — | derive 消费后由 session 级状态接管 |

#### 3.2.4 tar.zst 入库强校验（INGESTING 前置）

必须包含且非空：

| 成员 | 要求 |
|------|------|
| `manifest.json` | 可解析，含 `session_id`、`frame_count` |
| `rows.jsonl` | 行数 == `manifest.frame_count` |
| `imu_raw.jsonl` | 存在且 ≥1 行（ego-001 生产强制） |
| `frames/*.bin` | 数量 == `frame_count` |

失败 → `INGEST_FAILED`，`error.code` 见 §8 错误分类。

---

### 3.3 Session 状态机（34，对外契约）

#### 3.3.1 对外状态与内部 marker 映射

| 对外状态 | 判定条件（磁盘） |
|----------|-----------------|
| `UPLOADING` | 存在未上传完的 segment（130 侧）或 34 正在接收 |
| `DONE_UPLOAD` | `session.DONE_UPLOAD` marker 存在，且无 `DERIVING`/`READY`/`FAILED` |
| `DERIVING` | `session.DERIVING` marker 存在 |
| `READY` | `session.READY` marker 存在 |
| `FAILED` | `session.FAILED` marker 存在 |

#### 3.3.2 合法状态转移

| 从 | 到 | 触发条件 | 写入方 |
|----|-----|----------|--------|
| — | `UPLOADING` | 首个 segment 开始上传 | stream-ingest |
| `UPLOADING` | `DONE_UPLOAD` | **该 session 全部 segment 均为 `DERIVE_PENDING`** | stream-ingest |
| `DONE_UPLOAD` | `DERIVING` | `ego-derive run` 或 derive-worker 调度 | derive-worker |
| `DERIVING` | `READY` | §7 READY 门禁全部通过 | derive-worker |
| `DERIVING` | `FAILED` | 门禁任一项失败或 derive 异常 | derive-worker |
| `FAILED` | `DERIVING` | 手动 `ego-derive run` 重试 | derive-worker |
| `READY` | `DERIVING` | `ego-reset-34-only` 后重跑 derive | derive-worker |

#### 3.3.3 Session marker payload 规范

**`session.READY`：**

```json
{
  "sessionId": "sess_abc123",
  "marker": "session.READY",
  "at": "2026-08-18T12:30:00.000Z",
  "phase": "READY",
  "mp4Ok": true,
  "markers": 3,
  "total": 900,
  "parquetRows": 900,
  "ready_gate": {
    "version": 1,
    "checks_passed": 6,
    "checks_total": 6
  }
}
```

**`session.FAILED`：**

```json
{
  "sessionId": "sess_abc123",
  "marker": "session.FAILED",
  "at": "2026-08-18T12:30:00.000Z",
  "phase": "FAILED",
  "mp4Ok": false,
  "reason": {
    "code": "MUX_FRAME_MISMATCH",
    "message": "camera_front_left mp4 frames 895 < expected 900",
    "category": "mux"
  }
}
```

#### 3.3.4 130 ↔ 34 状态双向同步

| 事件 | 130 manifest | 34 segment state | 34 session marker |
|------|-------------|------------------|-------------------|
| 上传开始 | `UPLOADING` | — | — |
| 上传成功 ACK | `UPLOADED` | `RECEIVED` → `DERIVE_PENDING` | 末段完成 → `DONE_UPLOAD` |
| 上传失败 | `UPLOAD_FAILED` | — | — |
| `--force` 重传成功 | `UPLOADED`（刷新 `remote_ack_at`） | 覆盖 raw + 重置 segment state | 清除 `READY`/`FAILED`，回 `DONE_UPLOAD` |
| derive 成功 | — | — | `READY` |
| derive 失败 | — | — | `FAILED` |

---

## 4. IMU 双存储规范

### 4.1 硬件与源数据

| 参数 | 值 |
|------|-----|
| 相机帧率 | 30 Hz |
| IMU 采样率 | 200 Hz（标称） |
| 唯一可信源 | tar.zst 内 `imu_raw.jsonl` |
| 源格式（段内） | 分行：`{ts_ns, sensor: "accel"|"gyro", x, y, z}` |
| 磁力计 | 硬件无数据；派生时 `mag=[0,0,0]` |

### 4.2 原始高频存储（无损）

**路径：**

```text
sensor_raw/imu/chunk-000/file-000.parquet
```

**Parquet Schema：**

| 列名 | 类型 | 说明 |
|------|------|------|
| `episode_index` | int32 | 固定 0（单 episode 模式 `STREAM_SESSION_SINGLE_EPISODE=1`） |
| `segment_id` | string | 来源段 ID |
| `imu_timestamp` | float64 | 秒（`ts_ns / 1e9`） |
| `accel` | list<float32>[3] | m/s² 或设备单位，不修改原值 |
| `gyro` | list<float32>[3] | rad/s 或设备单位 |
| `mag` | list<float32>[3] | 无硬件时 `[0.0, 0.0, 0.0]` |

**写入规则：**

1. 读取 `imu_raw.jsonl`，按 `ts_ns` 合并同行 accel+gyro（最近时间戳配对，容差 ≤ 0.5ms）
2. 无法配对的单传感器行：缺失轴填 `NaN`（门禁区分 NaN vs 0）
3. 不丢弃、不插值、不降采样
4. 多 segment 追加写入同一 parquet（原子 tmp → rename）

**`meta/info.json` 扩展（替代 vendor_meta.high_freq）：**

```json
{
  "sensor_raw": {
    "imu": {
      "path": "sensor_raw/imu/chunk-000/file-000.parquet",
      "rate_hz": 200,
      "schema_version": 1,
      "sensors": ["accel", "gyro", "mag"],
      "record_count": 60000,
      "source": "imu_raw.jsonl"
    }
  }
}
```

### 4.3 主表对齐存储（30 Hz 训练/回放）

**路径：** `data/chunk-000/file-000.parquet`（及伴生 jsonl）

**新增 features（`meta/info.json`）：**

```json
{
  "observation.imu_accel": {
    "dtype": "float32",
    "shape": [3],
    "names": ["x", "y", "z"]
  },
  "observation.imu_gyro": {
    "dtype": "float32",
    "shape": [3],
    "names": ["x", "y", "z"]
  },
  "observation.imu_timestamp": {
    "dtype": "float64",
    "shape": [1],
    "names": null
  }
}
```

**对齐算法（最近邻）：**

```text
对每个相机帧行（timestamp_ns = T_cam）：
  1. 在 sensor_raw IMU 时间序列中找 argmin |imu_timestamp - T_cam/1e9|
  2. 填入 observation.imu_accel, observation.imu_gyro, observation.imu_timestamp
  3. 记录对齐误差 align_delta_ms = |matched_ts - T_cam/1e9| * 1000
  4. align_delta_ms > 5.0 → 写 WARN 日志，不阻断
  5. 主表字段禁止 null（无匹配时填 NaN，门禁 NaN 视为失败）
```

### 4.4 imu_raw.jsonl → 向量行转换逻辑

```python
# 伪代码 — derive/imu/ingest-raw.py
def jsonl_to_vector_rows(imu_lines: list[dict], segment_id: str, episode_index: int):
    by_ts: dict[int, dict] = defaultdict(lambda: {"accel": None, "gyro": None})
    for rec in imu_lines:
        ts = int(rec["ts_ns"])
        vec = [float(rec["x"]), float(rec["y"]), float(rec["z"])]
        by_ts[ts][rec["sensor"]] = vec
    rows = []
    for ts_ns, parts in sorted(by_ts.items()):
        accel = parts["accel"] or [float("nan")] * 3
        gyro = parts["gyro"] or [float("nan")] * 3
        rows.append({
            "episode_index": episode_index,
            "segment_id": segment_id,
            "imu_timestamp": ts_ns / 1e9,
            "accel": accel,
            "gyro": gyro,
            "mag": [0.0, 0.0, 0.0],
        })
    return rows
```

### 4.5 ego-platform 跨仓库对齐（Phase 0 同步）

| 旧（v0.0.12） | 新（v0.0.13） |
|--------------|--------------|
| `merge_high_freq_from_stream` | 重命名为 `merge_sensor_raw_imu_from_stream` |
| 读 `data/chunk-000/high_freq/imu_200hz.parquet` | 读 `sensor_raw/imu/chunk-000/file-000.parquet` |
| schema `ts_ns, sensor, x, y, z` | schema 向量行（§4.2） |
| VIO 读 high_freq | VIO 读 sensor_raw |

**不做：** 软链接、双路径 fallback、vendor_meta 兼容层。

---

## 5. Episode 全局帧映射

### 5.1 存储位置

```text
meta/episodes/episode_000.json
```

### 5.2 完整 Schema

```json
{
  "episode_index": 0,
  "length": 900,
  "frame_index_min": 0,
  "frame_index_max": 899,
  "sessions": [
    {
      "session_id": "sess_abc123",
      "uploaded_at": "2026-08-18T12:05:00.000Z",
      "segment_count": 3
    },
    {
      "session_id": "sess_def456",
      "uploaded_at": "2026-08-18T14:00:00.000Z",
      "segment_count": 2
    }
  ],
  "frame_segments": [
    {
      "session_id": "sess_abc123",
      "segment_id": "seg_001",
      "local_frame_min": 0,
      "local_frame_max": 299,
      "global_start": 0,
      "global_end": 299,
      "frame_count": 300
    },
    {
      "session_id": "sess_abc123",
      "segment_id": "seg_002",
      "local_frame_min": 0,
      "local_frame_max": 299,
      "global_start": 300,
      "global_end": 599,
      "frame_count": 300
    },
    {
      "session_id": "sess_def456",
      "segment_id": "seg_001",
      "local_frame_min": 0,
      "local_frame_max": 299,
      "global_start": 600,
      "global_end": 899,
      "frame_count": 300
    }
  ],
  "mapping_version": 1,
  "updated_at": "2026-08-18T14:30:00.000Z"
}
```

### 5.3 映射构建规则

```text
1. 按 sessions[].uploaded_at 升序排列 session
2. 每个 session 内按 segment_id 字典序（或 manifest.created_at）排列
3. global_start = 上一段 global_end + 1（首段为 0）
4. 写入 rows.jsonl / parquet 时：global_index = global_start + local_frame_index
5. 彻底废弃 frameBase 累加（stream-ingest.mjs 中所有 frameBase 逻辑删除）
```

### 5.4 消费方（统一只读此文件）

| 模块 | 用途 |
|------|------|
| `derive/frame-map.mjs` | 构建/更新映射 |
| `derive/parquet-writer.mjs` | 写主表全局 frame_index |
| `derive/mux-exec.mjs` | MP4 帧序与全局索引对齐 |
| `derive/ready-gate.mjs` | 连续性校验 |
| `ego-run-pipeline` | 后处理帧范围 |

### 5.5 连续性不变量

```text
∀ i ∈ [0, length-1]: 存在唯一 frame_segments 条目使得 global_start ≤ i ≤ global_end
frame_segments 按 global_start 严格递增、无重叠、无空洞
length == frame_index_max - frame_index_min + 1
```

---

## 6. Derive 执行顺序（冻结）

```text
1. 读取 raw/segments/*.tar.zst（只读）
2. 更新 segment state → INGESTING
3. tar 完整性校验
4. 解压 frames → staging/（不 purge）
5. 解析 imu_raw.jsonl → 追加 sensor_raw/imu parquet
6. 读取 frame_segments 映射 → 写主表 parquet/jsonl + IMU 最近邻对齐
7. staging ffmpeg mux → 四路 MP4
8. 执行 §7 READY 门禁
9. 通过 → session.READY；失败 → session.FAILED
10. 终态（READY/FAILED）且派生产物完整 → GC staging/
```

**固定环境变量（不变）：**

```text
DERIVE_STANDALONE=1
DERIVE_VIDEO_EXPORT_BACKEND=auto
DERIVE_ASYNC=0
STREAM_SESSION_SINGLE_EPISODE=1
```

---

## 7. READY 门禁可执行校验清单

门禁实现文件：`derive/ready-gate.mjs`  
全部通过才可写 `session.READY`；任一项失败 → `session.FAILED` + 结构化 reason。

### 7.1 检查项

| # | 检查 ID | 描述 | 实现函数 | 失败 code |
|---|---------|------|----------|-----------|
| G1 | `MAIN_TABLE_CONTINUOUS` | 主表 frame_index 从 min 到 max 连续无重复无空洞 | `checkMainTableContinuity()` | `PARQUET_INDEX_GAP` |
| G2 | `MP4_FRAME_COVERAGE` | 四路 MP4 总帧数均 ≥ 主表行数 | `checkMp4FrameCoverage()` | `MUX_FRAME_MISMATCH` |
| G3 | `MUX_VALIDATED` | `live/derive/mux_validated.json` 存在且 `ok=true` | `checkMuxValidated()` | `MUX_VALIDATION_FAILED` |
| G4 | `LEROBOT_SCHEMA` | info.json features 合法，parquet 可被 LeRobot 加载 | `checkLeRobotSchema()` | `SCHEMA_INVALID` |
| G5 | `IMU_RAW_PARQUET` | `sensor_raw/imu/.../file-000.parquet` 存在、非空、行数>0 | `checkImuRawParquet()` | `IMU_RAW_MISSING` |
| G6 | `IMU_MAIN_ALIGNED` | 主表 `observation.imu_*` 无 null；NaN 比例 < 1% | `checkImuMainAlignment()` | `IMU_ALIGN_NULL` |

### 7.2 G1 详细算法

```javascript
function checkMainTableContinuity(root) {
  const map = readFrameMap(root); // meta/episodes/episode_000.json
  const rows = readMainTableFrameIndices(root);
  const expected = map.length;
  const unique = new Set(rows);
  if (unique.size !== rows.length) return fail("PARQUET_INDEX_GAP", "duplicate frame_index");
  if (rows.length !== expected) return fail("PARQUET_INDEX_GAP", `row count ${rows.length} != length ${expected}`);
  for (let i = map.frame_index_min; i <= map.frame_index_max; i++) {
    if (!unique.has(i)) return fail("PARQUET_INDEX_GAP", `missing index ${i}`);
  }
  return pass();
}
```

### 7.3 G2 详细算法

```javascript
const VIDEO_KEYS = [
  "observation.images.camera_front_left",
  "observation.images.camera_front_right",
  "observation.images.camera_rear_left",
  "observation.images.camera_rear_right",
];
for (const key of VIDEO_KEYS) {
  const mp4Frames = probeMp4TotalFrames(root, key);
  if (mp4Frames < mainTableRows) {
    return fail("MUX_FRAME_MISMATCH", `${key}: ${mp4Frames} < ${mainTableRows}`);
  }
}
```

### 7.4 G5 mag / NaN 区分

| 情况 | 判定 | 门禁 |
|------|------|------|
| `mag = [0,0,0]` | 合法零值（无磁力计） | 通过 |
| `accel/gyro` 为 NaN | 数据缺失 | `IMU_RAW_MISSING` 或 `IMU_ALIGN_NULL` |
| 主表 `observation.imu_*` 为 null | 对齐失败 | `IMU_ALIGN_NULL` |
| `align_delta_ms > 5` | 精度告警 | WARN 日志，**不阻断** |

### 7.5 mux_validated.json 格式

```json
{
  "ok": true,
  "checked_at": "2026-08-18T12:29:55.000Z",
  "session_id": "sess_abc123",
  "expected_frames": 900,
  "frames": {
    "observation.images.camera_front_left": 900,
    "observation.images.camera_front_right": 900,
    "observation.images.camera_rear_left": 900,
    "observation.images.camera_rear_right": 900
  },
  "source": "frame_map"
}
```

---

## 8. 错误分类（结构化 reason.code）

| category | code | 含义 | 建议处置 |
|----------|------|------|----------|
| `capture` | `SEGMENT_CORRUPT` | 130 段损坏/截断 | 人工检查，重采或修复 |
| `upload` | `UPLOAD_NETWORK` | 网络错误 | 重试 upload |
| `upload` | `UPLOAD_HTTP_REJECT` | 34 拒绝 | 检查 token/格式 |
| `ingest` | `TAR_MISSING_MEMBER` | tar 缺 manifest/rows/imu | 重传该段 |
| `ingest` | `TAR_FRAME_COUNT_MISMATCH` | frames 数量不符 | 标记 INGEST_FAILED |
| `derive` | `PARQUET_INDEX_GAP` | 主表索引空洞/重复 | reset-34 + re-derive |
| `mux` | `MUX_FRAME_MISMATCH` | MP4 帧数不足 | 检查 staging/mux |
| `mux` | `MUX_VALIDATION_FAILED` | mux_validated 失败 | 检查 ffmpeg |
| `imu` | `IMU_RAW_MISSING` | sensor_raw 分片缺失 | 检查 imu_raw.jsonl |
| `imu` | `IMU_ALIGN_NULL` | 主表 IMU 字段 null/过多 NaN | 检查对齐逻辑 |
| `schema` | `SCHEMA_INVALID` | LeRobot schema 不合法 | 检查 features/info.json |

---

## 9. 生命周期 GC 规范

### 9.1 staging JPG

```text
禁止：ingest 完成立即 purge
允许：session 达到 READY 或 FAILED 终态，且 G1–G6 派生产物已落盘（或 FAILED 时保留 staging 供排查，24h 后 GC）
实现：derive/lifecycle-gc.mjs（替代 shouldDeferStagingPurge）
```

### 9.2 130 本地 Segment

```text
仅 UPLOADED → 可 delete 本地段目录
UPLOAD_FAILED / CLOSED / CORRUPT → 永久保留（直到人工处理）
```

### 9.3 34 reset 行为

**`ego-reset-34-only.sh` 删除：**

```text
data/
videos/
sensor_raw/
staging/
live/
state/sessions/（marker）
meta/episodes/
meta/info.json（派生部分）
pipeline/
corpus/
samples/
```

**`ego-reset-34-only.sh` 保留：**

```text
raw/segments/          # 唯一可信源
```

---

## 10. 模块拆分目录蓝图

### 10.1 目标结构

```text
data-lab-platform/
├── ego-stream-client/                    # Phase 1–2
│   ├── segment_store.py                # 重写：状态机 + 完整性自检
│   ├── segment_upload.py               # 重写：manifest 状态流转
│   └── cli/upload_segments.py            # 签名不变
│
└── lerobot-studio/
    ├── stream-ingest.mjs                 # 瘦身：HTTP 路由 + 调度入口 only
    ├── ingest-server.mjs                 # 保留，调用 ingest/*
    ├── session-markers.mjs               # 保留，扩展 reason 字段
    │
    ├── ingest/                           # Phase 3 新建
    │   ├── receive-tar.mjs               # 接收 multipart → raw 原子落盘
    │   ├── segment-state.mjs             # 34 segment 状态机 CRUD
    │   ├── tar-validator.mjs             # tar 成员完整性校验
    │   ├── session-coordinator.mjs       # 全部 segment DERIVE_PENDING → DONE_UPLOAD
    │   └── index.mjs                     # 导出 ingest 公共 API
    │
    ├── derive/                           # Phase 4–5 新建
    │   ├── pipeline.mjs                  # derive 主编排（替代 derive-pipeline 核心）
    │   ├── frame-map.mjs                 # episode_000.json frame_segments 读写
    │   ├── parquet-writer.mjs            # 主表 30Hz 写入
    │   ├── imu/
    │   │   ├── ingest-raw.py             # sensor_raw parquet（替代 ingest-imu-high-freq.py）
    │   │   └── align-main.py             # 主表 IMU 最近邻对齐
    │   ├── mux-exec.mjs                  # 四路 MP4 mux（从现有迁移）
    │   ├── ready-gate.mjs                # §7 六门禁
    │   ├── lifecycle-gc.mjs              # 终态 staging GC
    │   └── index.mjs
    │
    ├── derive-pipeline.mjs               # @deprecated Phase 4 → 薄包装调用 derive/
    ├── derive-async.mjs                  # @deprecated → derive/pipeline.mjs
    └── scripts/
        ├── ingest-imu-high-freq.py       # @deprecated Phase 7 删除
        └── export-videos-from-parquet.py # 永久离线校验工具
```

### 10.2 stream-ingest.mjs 瘦身后的职责

```javascript
// 仅保留：
// 1. HTTP server 挂载（委托 ingest-server.mjs）
// 2. station root 路径解析
// 3. derive 触发入口（调用 derive/pipeline.mjs）
// 4. 对外 API 兼容层（derive-status, segments list）
//
// 删除/迁出：
// - shouldDeferStagingPurge
// - frameBase 逻辑
// - tar 解压/parquet 写入/mux 内联实现
// - high_freq IMU ingest 调用
// - MCAP 相关（MCAP_FAILED marker 保留读取兼容，不再写入）
```

### 10.3 模块依赖图

```text
ingest-server.mjs
  └─▶ ingest/receive-tar.mjs
        └─▶ ingest/tar-validator.mjs
        └─▶ ingest/segment-state.mjs
        └─▶ ingest/session-coordinator.mjs
              └─▶ session-markers.mjs (DONE_UPLOAD)

derive/pipeline.mjs
  ├─▶ derive/frame-map.mjs
  ├─▶ derive/imu/ingest-raw.py
  ├─▶ derive/imu/align-main.py
  ├─▶ derive/parquet-writer.mjs
  ├─▶ derive/mux-exec.mjs
  ├─▶ derive/ready-gate.mjs
  ├─▶ derive/lifecycle-gc.mjs
  └─▶ session-markers.mjs (DERIVING / READY / FAILED)
```

---

## 11. 可观测性规范

### 11.1 结构化日志字段（所有模块强制）

```json
{
  "ts": "2026-08-18T12:00:00.000Z",
  "level": "INFO",
  "component": "derive/ready-gate",
  "station_id": "ego-001",
  "session_id": "sess_abc123",
  "segment_id": "seg_001",
  "episode_index": 0,
  "event": "ready_gate_check",
  "check_id": "G2",
  "ok": false,
  "reason_code": "MUX_FRAME_MISMATCH"
}
```

### 11.2 IMU 对齐告警

```json
{
  "event": "imu_align_warn",
  "frame_index": 42,
  "align_delta_ms": 7.3,
  "threshold_ms": 5.0
}
```

---

## 12. Phase 1–7 交付物与验收映射

| Phase | 交付物 | 验收标准 |
|-------|--------|----------|
| **1** | `segment_store.py` v2 manifest + GC 铁律 | SIGKILL 断段可识别；未上传段不被删 |
| **2** | `upload_segments.py` 状态流转 | `--force` 双向同步；UPLOAD_FAILED 可重试 |
| **3** | `ingest/*` 模块 | raw 优先落盘；tar 校验；DONE_UPLOAD 门禁 |
| **4** | `derive/*` + IMU 双存储 + frame_map | sensor_raw 无损；主表 IMU 对齐；废弃 frameBase |
| **5** | `ready-gate.mjs` + `lifecycle-gc.mjs` | 六门禁；staging 终态 GC |
| **6** | reset 脚本 | reset-34 保留 raw；force 重传全链路恢复 |
| **7** | 技术债清零 + `v0.0.13` 镜像 | §13 删除清单归零；8 项验收全通过 |

---

## 13. Phase 7 强制删除清单

| 类别 | 删除/废弃项 |
|------|------------|
| 路径 | `data/chunk-000/high_freq/` |
| 配置 | `ego-lan-214` 及所有 lan-214–223 station 条目 |
| 脚本 | `scripts/legacy/ego-lan-214-*` |
| 文档 | `docs/ego-lan-214-*` |
| 代码 | `shouldDeferStagingPurge`、所有 `frameBase` |
| 代码 | H264 / `segment_mp4` / `SEGMENT_H264` 分支 |
| 代码 | `SEGMENT_AUTO_PURGE_PENDING`、未上传段淘汰 |
| 代码 | `DERIVE_MCAP_EXPORT`、MCAP 写入路径 |
| 代码 | bind-mount 源码覆盖 compose override |
| 脚本 | `ingest-imu-high-freq.py`（由 `derive/imu/ingest-raw.py` 替代） |
| 角色降级 | `export-videos-from-parquet.py` → 仅离线校验 |

---

## 14. 镜像与部署

| 项 | 规范 |
|----|------|
| 开发分支 | `feat/ego-001-foxglove-refactor` |
| RC 标签 | `data-lab-lerobot-studio:v0.0.13-rc.{phase}` |
| 正式标签 | `data-lab-lerobot-studio:v0.0.13` |
| 生产部署 | `deploy-stream-ingest-v0.0.13.sh` |
| 禁止 | compose bind-mount 覆盖 `/app` 源码 |

---

## 15. 验收测试矩阵（最终）

| # | 场景 | 命令/操作 | 期望 |
|---|------|-----------|------|
| T1 | 常规全链路 | 130 采集 → upload → ego-derive → Viewer | session.READY；四路可播 |
| T2 | 重置重传 | ego-reset-34-only → upload --force → derive | 从 raw 完整恢复；状态一致 |
| T3 | 多 Session 合并 | 两 session 上传 derive | frame_segments 连续无冲突 |
| T4 | IMU 双存储 | 检查 parquet | sensor_raw 200Hz 完整；主表 IMU 无 null |
| T5 | 崩溃容错 | SIGKILL 中断采集 | CORRUPT 标记；不雪崩 |
| T6 | 异常拦截 | 故意缺帧 | session.FAILED + 明确 reason |
| T7 | 上层零感知 | ego-deliver / Viewer / 训练加载 | 无修改可用 |
| T8 | 技术债清零 | grep ego-lan-214 / high_freq / frameBase | 零命中（legacy 目录除外） |

---

## 附录 A：130 段完整性自检规则

| 检查 | 失败标记 |
|------|----------|
| `manifest.json` 存在且可解析 | `CORRUPT` |
| `rows.jsonl` 行数 == `frame_count` | `CORRUPT` |
| `frames/` 下 `.bin` 数量 == `frame_count` | `CORRUPT` |
| `imu_raw.jsonl` 存在（ego-001 生产） | `CORRUPT`（warning 模式可配置为 CLOSED+warn） |
| tmpfs 残留未 finalize | `CORRUPT`（`integrity.issues: ["orphan_active"]`） |

---

## 附录 B：文档变更流程

1. 任何设计变更必须 PR 修改本文档并标注版本号递增（`Phase0-v1.1`）
2. 禁止未更新本文档的口头变更
3. Phase 1 开工前本文档状态为 **FROZEN**

---

*本文档由 Phase 0 设计冻结产出，后续 Phase 1–7 实现的唯一基准。*
