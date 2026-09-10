# EGO 采集 → 上传 → 派生：最简核心方案

> **受众：** 后端评审  
> **范围：** 生产主路径 `ego-001`（130 采集 / 34 平台），不含 214 历史通道、浏览器 bulk import、QC 等旁路。  
> **版本基线：** v0.1.3 async（upload / derive 解耦）

---

## 1. 设计目标

| 原则 | 说明 |
|------|------|
| **一次录制 = 一个 episode** | 操作员在采集端开始/停止一次，对应平台侧一个 `sess_*` session |
| **段（segment）为传输单元** | 长录制按 ~75s 切段，每段打包为 `seg_*.tar.zst` 独立上传 |
| **上传与派生解耦** | `stream-ingest` 只负责落盘 + 写 marker；`derive-worker` 异步消费 |
| **磁盘 marker 即状态机** | 无中心 DB 调度 derive；`state/sessions/{sess}/session.*` 文件驱动全流程 |
| **两层可见性** | derive 完成 → Collection 预览；convert 完成 → egodome 训练集 |

---

## 2. 拓扑

```text
┌──────────────────── 130 采集站 ────────────────────┐
│  OAK 4-cam 录制 (30fps + 200Hz IMU)               │
│  → segments/sessions/sess_*/seg_*/                │
│  → pack seg_*.tar.zst                             │
│  → ego-upload ego-001                             │
└──────────────────────┬────────────────────────────┘
                       │ HTTP POST (tar.zst)
                       ▼
┌──────────────────── 34 平台 ───────────────────────┐
│  nginx :8080                                       │
│    └─ stream-ingest (:7862)   上传 API             │
│    └─ derive-worker           异步派生             │
│    └─ lerobot                 Collection 预览      │
│  host: ego-process-watcher → ego-process          │
│    └─ convert + samples.zip → Viewer /data/egodome │
└────────────────────────────────────────────────────┘
```

| 节点 | IP / 入口 | 角色 |
|------|-----------|------|
| 采集站 130 | `10.10.10.130` | 边缘录制、打包、上传 |
| 平台 34 | `10.10.10.34:8080` | ingest、derive、后处理、浏览 |

---

## 3. 核心实体

```text
station (ego-001)
  └── session (sess_20250827_143022)     ← 一次录制 = 一个 episode
        └── segment (seg_0003)           ← ~1800 帧 / ~75s
              ├── manifest.json
              ├── rows.jsonl             ← 帧级 pose / hands / task
              ├── imu_raw.jsonl
              └── frames/{frame:08d}.bin ← 4 路相机 JPEG 打包
```

**上传包：** 每个 CLOSED segment 目录 → `seg_*.tar.zst`（zstd tar）。

**相机 key（LeRobot v3）：**

- `observation.images.camera_front_left`
- `observation.images.camera_front_right`
- `observation.images.camera_rear_left`
- `observation.images.camera_rear_right`

---

## 4. 端到端流程（最简主路径）

```text
[采集]  130 开始/停止录制
          ↓
[打包]  segment 关闭 → seg_*.tar.zst
          ↓
[上传]  ego-upload ego-001
          POST /lerobot/api/collection/stations/ego-001/upload
          → raw/segments/{sess}/seg_*.tar.zst
          → 全部段到位后写 session.DONE_UPLOAD
          → POST process-notify（默认）
          ↓
[派生]  derive-worker 轮询 DONE_UPLOAD
          EXTRACT → TABLE → MUX_ENCODE → MUX_MERGE → session.READY
          → LeRobot v3 stream dataset（parquet + MP4 + meta）
          ↓  /collection?station=ego-001 可见
[后处理] ego-process-watcher (~30s) → ego-process ego-001
          convert（手部 pipeline）→ corpus → samples/egodome.zip
          ↓  /data/egodome 可见
```

**操作员日常只需两步：**

```bash
# 130
ego-upload ego-001

# 34 自动触发；失败时手动兜底
ego-process ego-001
```

---

## 5. 采集端（130）

### 5.1 段目录结构

| 文件 | 内容 |
|------|------|
| `manifest.json` | session_id、segment_id、帧数、元数据 |
| `rows.jsonl` | 每帧时间戳、位姿、手部、任务标签 |
| `imu_raw.jsonl` | 原始 IMU |
| `frames/*.bin` | 多相机 JPEG 帧包（`frame_bin_codec`） |

### 5.2 关键路径

| 项 | 路径 |
|----|------|
| 段缓存 | `/home/server/cache/ego-001/segments` |
| 导出就绪 | `/home/server/export/ego-001/ready/` |

### 5.3 边缘服务（systemd）

| 服务 | 作用 |
|------|------|
| `ecs-record-oak-stream` | OAK 采集 |
| `ecs-upload-segments-loop` | 可选自动上传循环 |
| `ecs-station-heartbeat` | 15s 心跳上报 34 |

### 5.4 上传客户端

- 入口：`ego-upload` → `scripts/ego-upload-station.sh` → `ego-stream-client/cli/ego_upload.py`
- 扫描所有 `sess_*` 下 **CLOSED 且未上传** 的段（幂等）
- 协议：`UPLOAD_PROTOCOL=tarzst`（默认）
- 鉴权：`X-Station-Token` header
- 成功后可选删除本地段（`EGO_SEGMENT_DELETE_AFTER_UPLOAD=1`）
- 默认 POST `process-notify` 触发 34 后处理

---

## 6. 平台上传 API（34）

**服务：** `stream-ingest` 容器，`ingest-server.mjs`，内部 `:7862`，nginx 反代 `/lerobot/`。

### 6.1 核心接口

| 方法 | 路径 | 用途 |
|------|------|------|
| `POST` | `/lerobot/api/collection/stations/:id/upload` | 段上传（tar.zst） |
| `POST` | `/lerobot/api/collection/stations/:id/process-notify` | 排队 host 侧 `ego-process` |
| `GET` | `/lerobot/api/collection/stations/:id/derive-status` | 派生队列与进度 |
| `POST` | `/lerobot/api/collection/stations/:id/derive-start` | 手动触发派生 |
| `GET` | `/lerobot/api/collection/stations/:id/segments` | 段状态列表 |

### 6.2 上传请求头（tar.zst 模式）

| Header | 说明 |
|--------|------|
| `X-Station-Token` | 站点鉴权 |
| `X-Session-Id` | `sess_*` |
| `X-Segment-Id` | `seg_*` |
| `X-Segment-Seq` | 段序号 |
| `X-Upload-Protocol` | `tarzst` |
| `X-Content-Sha256` | 内容校验（可选） |

### 6.3 ingest 落盘逻辑（摘要）

1. 校验 token + 段元数据
2. 写入 `raw/segments/{sess_id}/seg_*.tar.zst`
3. 更新段状态 → `DERIVE_PENDING`
4. 当 session 内**全部段**均为 `DERIVE_PENDING` 时 → 写 `session.DONE_UPLOAD`
5. （可选）写 `state/process-notify.pending.json` 或通过 upload 客户端 POST notify

**实现：** `lerobot-studio/stream-ingest.mjs`、`lerobot-studio/ingest-server.mjs`

---

## 7. 派生（derive-worker）

### 7.1 触发

- `DERIVE_STANDALONE=1`（生产默认）：ingest **不**内联 derive
- `ego-derive-watch.mjs` 每 ~10s 扫描 `listSessionsPendingDerive()`
- 发现 `session.DONE_UPLOAD` 且尚无 `READY` → `kickDeriveSession()`

### 7.2 阶段

```text
EXTRACT → TABLE → MUX_ENCODE → MUX_MERGE → MUX_VALIDATE → READY
```

| 阶段 | 动作 |
|------|------|
| EXTRACT | 解压 tar.zst，解码 `frames/*.bin` |
| TABLE | 建帧映射，写 parquet / jsonl，IMU 对齐 |
| MUX_ENCODE | 按相机分 chunk 编码 MP4（并行池） |
| MUX_MERGE | ffconcat 一次性合并（M1，替代 pairwise） |
| READY | 写 `session.READY`，发布 stream viewer 索引 |

**进度文件：** `live/derive/progress/{sess_id}.json`（phase / done / total / updatedAt）

**实现：** `lerobot-studio/derive/pipeline-unit.mjs`、`derive-pipeline.mjs`

### 7.3 派生产出（stream 热层）

落盘于 `data-storage/stream/{station}/`：

```text
data/chunk-000/file-000.parquet      # LeRobot 主表
videos/observation.images.camera_*/  # 合并后 MP4
meta/                                # info.json, episodes, intrinsics
```

**验收：** `http://34:8080/collection?station=ego-001`

---

## 8. Session 状态机（磁盘 marker）

路径：`data-storage/stream/{station}/state/sessions/{sess_id}/`

```text
                    upload 全部段完成
                           │
                           ▼
                  session.DONE_UPLOAD  ◄── stream-ingest 写入
                           │
              derive-worker 认领
                           ▼
                  session.DERIVING     ◄── derive-worker 写入
                           │
              成功                    失败
                ▼                        ▼
         session.READY            session.FAILED
```

| Marker | 写入方 | 含义 |
|--------|--------|------|
| `session.DONE_UPLOAD` | stream-ingest | 上传完成，待派生 |
| `session.DERIVING` | derive-worker | 派生进行中 |
| `session.READY` | derive-worker | 派生完成，stream 可预览 |
| `session.FAILED` | derive-worker | 派生失败，可 retry |

**契约文件：** `lerobot-studio/session-markers.mjs`

---

## 9. 后处理（ego-process）

derive 与 convert **刻意分离**：derive 产出 stream-tier 快速预览；convert 跑更重的手部重建 pipeline。

### 9.1 触发链

```text
ego-upload 默认 POST process-notify
  → state/process-notify.pending.json
  → systemd timer: data-lab-ego-process-watcher (~30s)
  → ego-process ego-001
```

### 9.2 ego-process 步骤

| 步骤 | 动作 |
|------|------|
| 1. derive | async 模式：等待 derive-worker 至 `READY` |
| 2. convert | `ego-run-pipeline` → `ego_platform.cli.convert` |
| 3. sync | `ingest-bundled-datasets.sh` → Viewer |
| 4. gate | `ego-viewer-publish-gate`（可选） |

### 9.3 后处理产出

| 路径 | 内容 |
|------|------|
| `data-storage/pipeline/ego-001/{sess}/` | 单 session 工作目录与 dataset |
| `data-storage/corpus/egodome/` | 聚合训练语料 |
| `data-storage/samples/egodome.zip` | Viewer 打包样本 |

**验收：** `http://34:8080/data/egodome`

---

## 10. 存储分层

```text
data-storage/
├── stream/{station}/          # 热层：ingest + derive 活跃数据
│   ├── raw/segments/            # 上传原始 tar.zst
│   ├── derived/{sess}/          # 派生中间产物
│   ├── state/sessions/          # session marker
│   ├── data/ videos/ meta/      # LeRobot v3 stream dataset
│   └── archive/                 # 热盘 session 归档
├── pipeline/{station}/          # convert 工作区
├── corpus/{slug}/               # 聚合语料（egodome）
├── samples/{slug}.zip           # Viewer 样本包
└── ego-archive/{station}/       # 冷层：长期 tar.gz（/cold-archive 挂载）
```

| 层级 | 策略（默认） |
|------|-------------|
| 热层 `stream/` | 活跃 ingest/derive；400GB 配额、7 天保留 |
| 冷层 `ego-archive/` | `archive-to-cold.sh` 按龄迁移 |

---

## 11. 平台服务一览

| 组件 | 部署 | 职责 |
|------|------|------|
| `nginx` | Docker | 统一网关 `:8080` |
| `stream-ingest` | Docker | 上传 API、段提交、写 DONE_UPLOAD |
| `derive-worker` | Docker | 异步派生，写 READY |
| `stream-parquet-sync` | Docker | parquet 后台同步 |
| `lerobot` | Docker | Collection 预览网关 |
| `ego-process-watcher` | host systemd | 消费 process-notify，调 ego-process |

**Compose：** `docker-compose.platform.yml` + `docker-compose.v0.1.3-async.yml`

---

## 12. 验收与观测

### 12.1 两个页面对照

| 页面 | URL | 对应阶段 | 时延 |
|------|-----|----------|------|
| Collection | `/collection?station=ego-001` | derive READY 后 | 较快 |
| egodome | `/data/egodome` | convert + sync 后 | 较慢（~30s+） |

正常情况：**episode 数量一致**（一次录制 = 一条）。

### 12.2 常用诊断

```bash
# 派生状态
curl -s http://127.0.0.1:8080/lerobot/api/collection/stations/ego-001/derive-status | python3 -m json.tool

# CLI
ego-derive status --station ego-001

# derive worker 日志
docker logs data-lab-derive-worker-1 --since 1h

# process watcher
systemctl --user list-timers data-lab-ego-process-watcher.timer
```

---

## 13. 不在本方案范围内的能力

以下存在但**不属于最简主路径**，评审时可暂不展开：

- 浏览器 bulk import（`/import/upload`）
- LeRobot QC 终端（`/qc/`）
- 多 derive-worker 水平扩展（`ego-derive-scale.sh`）
- episode registry DB（`data-storage/registry/`）
- 冷归档恢复与 re-derive 运维脚本

---

## 14. 关键代码索引

| 模块 | 路径 |
|------|------|
| 采集录制 | `ego-stream-client/cli/record_oak_stream.py` |
| 段打包 | `ego-stream-client/segment_tar_zst.py` |
| 上传客户端 | `ego-stream-client/cli/ego_upload.py` |
| 上传 API | `lerobot-studio/ingest-server.mjs` |
| ingest 核心 | `lerobot-studio/stream-ingest.mjs` |
| Session marker | `lerobot-studio/session-markers.mjs` |
| 派生 watch | `lerobot-studio/ego-derive-watch.mjs` |
| 派生 pipeline | `lerobot-studio/derive/pipeline-unit.mjs` |
| 后处理入口 | `scripts/ego-process` |
| 站点注册 | `config/ego-pipeline-stations.yaml` |
| 操作手册 | `docs/ego-001-使用手册.md` |

---

## 15. 评审关注点（建议）

1. **幂等与重试：** 段上传、derive retry、`session.FAILED` 恢复策略是否满足后端 SLA 预期？
2. **marker 契约：** 磁盘文件状态机是否足够替代队列/DB？多 worker claim 竞态如何处理？
3. **两层延迟：** Collection vs egodome 的时间差是否为产品可接受？是否需要在 API 层统一 episode 状态？
4. **存储生命周期：** 热层 7 天 + 冷归档后，derive 产物与 raw tar.zst 的保留/清理策略。
5. **鉴权模型：** `X-Station-Token` 分发与轮换；是否需要升级为 mTLS 或短期签名 URL。
6. **可观测性：** `derive-status` + progress JSON 是否满足排障；是否需要统一 metrics / tracing。

---

*关联文档：[`ego-001-使用手册.md`](./ego-001-使用手册.md) · [`ego-derive-m1-r1.md`](./ego-derive-m1-r1.md) · [`ego-derive-p1-commercial-sla.md`](./ego-derive-p1-commercial-sla.md)*
