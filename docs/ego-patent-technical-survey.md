# EGO 第一视角采集系统 — 软件技术调研与专利素材汇编

> **文档用途**：供后续撰写 EGO 采集设备**软硬件一体专利交底书**之软件侧技术底稿。  
> **编写原则**：以技术事实描述为主，按「问题—方案—效果」组织，便于专利代理人转化为权利要求与说明书实施例。  
> **范围**：本仓库 `data-lab` 内与 EGO 采集站（以 `ego-lan-214` 为参考实现）相关的边缘采集代码、云端 ingest/派生、LeRobot v3 流式数据集、采集质检 UI、后处理与标注扩展。  
> **硬件侧**：本文档侧重软件；相机模组（OAK-FFC-4P）、IMU、边缘计算主机、网络拓扑等由硬件专利文档补充，文末给出软硬件接口边界。

---

## 目录

1. [技术背景与要解决的技术问题](#1-技术背景与要解决的技术问题)
2. [系统总体架构](#2-系统总体架构)
3. [硬件集成与传感器数据通路](#3-硬件集成与传感器数据通路)
4. [边缘采集软件子系统（214 边端）](#4-边缘采集软件子系统214-边端)
5. [云端数据平台子系统（34 平台）](#5-云端数据平台子系统34-平台)
6. [数据格式、存储分层与协议规范](#6-数据格式存储分层与协议规范)
7. [端到端业务流程](#7-端到端业务流程)
8. [采集质检与用户界面子系统](#8-采集质检与用户界面子系统)
9. [后处理、手部姿态与标注扩展](#9-后处理手部姿态与标注扩展)
10. [可靠性、安全与运维机制（P0–P3）](#10-可靠性安全与运维机制p0p3)
11. [专利候选技术点汇总](#11-专利候选技术点汇总)
12. [软硬件接口边界（供与硬件专利合并）](#12-软硬件接口边界供与硬件专利合并)
13. [附录：代码路径、API 与配置索引](#13-附录代码路径api-与配置索引)

---

## 1. 技术背景与要解决的技术问题

### 1.1 应用场景

EGO（Egocentric，第一视角）采集系统用于在实验室工作台等场景下，通过佩戴或固定于操作者视野的多路相机同步采集 RGB 图像，并结合 IMU、位姿占位、手部特征等低维观测，形成可供机器人学习（Imitation Learning / VLA）使用的 LeRobot v3 格式数据集。

典型部署为：

- **边缘采集设备**（示例 IP `10.10.10.214`，站点 ID `ego-lan-214`）：负责实时采帧、本地段式缓存、打包上传、本机/待机预览。
- **数据平台**（示例 IP `10.10.10.34`，Data Lab）：负责签收上传、格式转换、视频 mux、流式数据集发布、远程质检 UI、后处理与语料归档。

### 1.2 现有技术痛点（本系统针对解决的问题）

| 痛点 | 技术后果 | 本系统应对思路 |
|------|----------|----------------|
| 四路高分辨率相机同步写盘 IO 压力大 | 采帧掉帧、USB 带宽争抢 | tmpfs 热写 + DLB1 单帧四路打包 + 段关断后原子搬迁 |
| 采集与上传同进程耦合 | 网络抖动阻塞采帧主线程 | **采集写段**与**段上传** systemd 服务解耦 |
| 边缘中间格式与训练格式不一致 | 手工转换易错、episode 语义混乱 | 云端 ingest 统一转 LeRobot v3，并做全局帧索引重映射 |
| 远程实时预览与采集争抢带宽 | 采集中远程预览导致掉帧 | **采集态门控**：采集中禁止远程 MJPEG，回放路径不受影响 |
| 上传与派生（parquet/mux）同 HTTP 请求 | 上传超时、无法秒级 ACK | **上传/派生进程隔离** + 磁盘 session 状态机 |
| 多 session 多段数据 episode 边界不清 | Viewer 无法区分不同采集批次 | Session 级单 Episode + 自动命名（站点·session 指纹·日期·帧数） |
| 边采边播读到未完成 chunk | 花屏、曲线缺失 | Chunk `writing→finished` 读写分离 + dense parquet 同步 |
| 大规模流式数据磁盘膨胀 | 热层爆满 | 214 段队列配额 + 34 热层配额 + 冷归档 |

---

## 2. 系统总体架构

### 2.1 逻辑分层

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Layer A：边缘采集设备（EGO 采集站硬件 + 边端软件）                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐ │
│  │ OAK-FFC-4P   │→ │ record_oak   │→ │ segment_store│→ │ segment_upload  │ │
│  │ 四路 RGB+IMU │  │ _stream      │  │ 段式持久化   │  │ tar.zst 上传    │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────────┘ │
│         │                  │                  │                  │           │
│         └──────────────────┴──────────────────┴──────────────────┘           │
│                                    │ HTTP(S) LAN                               │
│  ┌──────────────┐  ┌──────────────┐                                          │
│  │ preview :8765│  │ ego_web :8080│  移动 Web 启停采集 / session 轮换          │
│  └──────────────┘  └──────────────┘                                          │
└────────────────────────────────────┬───────────────────────────────────────────┘
                                     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  Layer B：数据平台（Data Lab，nginx + stream-ingest + lerobot + derive-worker）│
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐ │
│  │ ingest HTTP  │→ │ raw 落盘     │→ │ derive-worker│→ │ LeRobot v3 流式 │ │
│  │ 签收/幂等    │  │ DONE_UPLOAD  │  │ extract/mux  │  │ jsonl/parquet/mp4│ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────────┘ │
│         │                                                    │               │
│         └──────────────────── preview-proxy ────────────────┘               │
│  ┌──────────────┐  ┌──────────────┐                                          │
│  │ /collection  │  │ LeRobot      │  双层 iframe：实时/回放 + 主题同步        │
│  │ 采集质检 UI  │  │ Viewer 嵌入  │                                          │
│  └──────────────┘  └──────────────┘                                          │
└────────────────────────────────────┬───────────────────────────────────────────┘
                                     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  Layer C：后处理与标注（可选）                                               │
│  ego-run-pipeline → HaMeR/MANO 手部关键点 → corpus 部署 → embodied-annotate  │
└────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 存储分层（三级）

| 层级 | 位置 | 典型容量策略 | 内容 |
|------|------|--------------|------|
| L1 边缘段队列 | 214 `/home/server/cache/ego-lan-214/segments` | 默认 256GB | 待上传 closed segment（rows.jsonl + DLB1 frames） |
| L2 平台热层流式数据集 | 34 `data-storage/stream/ego-lan-214` | 默认 100GB / 7 天 | LeRobot v3 实时 aggregate + raw tar.zst 保留 |
| L3 冷归档 | 34 `data-storage/ego-archive` | 长期 | session 级 tar.gz 归档 |

### 2.3 核心代码仓库路径（软件交付物）

| 子系统 | 根路径 |
|--------|--------|
| 边缘采集客户端 | `data-lab-platform/ego-stream-client/` |
| 边缘本地 Web 控制 | `data-lab-platform/ego-local-web/` |
| 云端 ingest / 派生 / Viewer | `data-lab-platform/lerobot-studio/` |
| 采集质检 Shell UI | `label_studio/templates/datalab/collection_viz.html` + `data-lab-platform/client/` |
| 后处理 CLI | `data-lab-platform/scripts/ego-run-pipeline` |
| EGO 标注 API | `data-lab-platform/embodied-annotate/backend/` |
| 运维文档 | `docs/ego-lan-214-*.md` |

---

## 3. 硬件集成与传感器数据通路

> **说明**：本节描述软件如何绑定硬件；具体光学布局、结构件、供电由硬件专利补充。

### 3.1 相机与命名映射

硬件采用 **OAK-FFC-4P-New** 四 socket 相机模组，软件将物理 socket 映射为 LeRobot v3 视频特征键（前后布局语义）：

| OAK Socket | LeRobot 特征键 | 语义 |
|------------|----------------|------|
| CAM_A（主时钟） | `observation.images.camera_front_left` | 第一视角主相机，30Hz 网格时钟 |
| CAM_B | `observation.images.camera_front_right` | 前右 |
| CAM_C | `observation.images.camera_rear_left` | 后左 |
| CAM_D | `observation.images.camera_rear_right` | 后右 |

实现文件：`data-lab-platform/ego-stream-client/camera_map.py`

**技术要点**：

- 主相机 CAM_A 驱动 30Hz 采帧网格（生产标准 `egoverse_30hz`，见 `docs/ego-lan-214-capture-standard.md`）。
- 历史数据集兼容别名（`camera_head_left` 等）通过 `LEGACY_TO_LEROBOT_VIDEO` 只读映射，新写入统一使用 `ALL_LEROBOT_VIDEO_KEYS` 顺序。

### 3.2 IMU 与低维观测

每帧 `rows.jsonl` 行包含：

| 字段 | 维度 | 来源 |
|------|------|------|
| `observation.state` | 6 | IMU（生产标准 200Hz 采样，对齐到 30Hz 网格） |
| `observation.pose` | 7 | 位姿占位（xyz + quaternion，采集端可写单位四元数缺省） |
| `observation.hands` | 63 | 手部特征占位（后处理可填充 MANO） |
| `action` | 1+ | 动作占位 |
| `timestamp_ns` | 标量 | 单调时钟 |
| `camera_ts_offset_ns` | 可选 dict | 各相机相对主相机网格 tick 的偏移（纳秒） |

维度常量：`ego-stream-client/ego_spec.py`（`OBS_STATE_DIM`、`OBS_POSE_DIM`、`OBS_HANDS_DIM`）。

### 3.3 相机内参（EEPROM → 会话元数据）

- 开机从 DepthAI EEPROM 读取内参，schema `depthai_eeprom_v1`。
- `session_start` 时随 JSON 控制面上传至平台 `meta/camera_intrinsics.json`。
- **严格模式**（`intrinsics_strict_required()`）：内参无效则拒绝开录，避免无标定数据污染语料。

相关文件：

- `ego-stream-client/camera_intrinsics.py`
- `ego-stream-client/config/OAK-FFC-4P-ego.json`（生产标定）
- `ego-stream-client/intrinsics_store.py`

### 3.4 采集/production 时序标准（EgoVerse 30/200）

| 参数 | 生产值 |
|------|--------|
| RGB 帧率 | 30 Hz |
| IMU 采样 | 200 Hz |
| 分辨率 | 1280×800 |
| 段长 | 300 帧 / 段（约 10s）或 45 秒先到先关 |
| 平台 mux FPS | `STREAM_MUX_FPS=30` |

配置：`ego-stream-client/config/egoverse_30hz_production.env`、systemd drop-in `z-production-egoverse.conf`。

### 3.5 本机预览服务

- 采集中：`record_oak_stream.py` 内嵌 `preview_server`，`:8765` 提供 MJPEG/JPG（本机 WiFi 可见）。
- 待机：`preview_standby.py` + `ecs-preview-standby.service` 低帧率 OAK 预检，不写盘。
- 与采集栈 **systemd 互斥**（`ecs-oak-capture-stack` ↔ `ecs-oak-standby-stack`）。

---

## 4. 边缘采集软件子系统（214 边端）

### 4.1 进程与服务编排

| systemd 单元 | 职责 |
|--------------|------|
| `ecs-record-oak-stream.service` | 主采集：OAK 采帧 → `SegmentCaptureWriter` |
| `ecs-upload-segments-loop.service` | 轮询 closed 段 → tar.zst → HTTP 上传 |
| `ecs-station-heartbeat.service` | 向平台发送 `captureState` 心跳 |
| `ecs-preview-standby.service` | 待机低帧率预览 |
| `ecs-oak-capture-stack.target` | 采集栈聚合（启停入口） |
| `ecs-oak-standby-stack.target` | 待机栈聚合 |
| `ecs-ego-web.service` | 移动 Web UI（`ego_web.py`） |

### 4.2 Scheme A：段式存储 + tmpfs 热写

**问题**：四路 JPEG 若每帧多个文件，inode 与 fsync 开销大；NVMe 持续小写放大。

**方案**（`segment_store.py` — `SegmentCaptureWriter`）：

1. **Open 段**写在 tmpfs：`/dev/shm/ego-capture-active/sessions/{sessionId}/segments/seg_XXXXXX/`。
2. 每帧写入：
   - `rows.jsonl` 一行（可批量缓冲 `SEGMENT_ROWS_BUFFER_LINES`）；
   - `frames/{frame_index:08d}.bin` 一个 DLB1 包（可选 `SEGMENT_FRAME_BIN=1`）。
3. **关段条件**：`SEGMENT_MAX_FRAMES`（默认 300）或 `SEGMENT_MAX_SECONDS`（默认 45s）。
4. 关段时：`fsync` + `rename` 原子搬迁至持久根 `EGO_SEGMENT_ROOT`。
5. **背压**：pending 段超 `SEGMENT_MAX_PENDING` 时采集线程 sleep（`SEGMENT_BACKPRESSURE_PENDING`），优先保证不丢当前采帧逻辑完整性。

**效果**：热写延迟低；关段后段目录作为**上传队列元素**；上传成功可 `EGO_SEGMENT_DELETE_AFTER_UPLOAD=1` 删除，边缘仅保留待传队列而非全量仓库。

### 4.3 DLB1 四路 JPEG 单帧打包格式

**问题**：单帧四路相机若落盘为四个独立 JPEG 文件，随机写放大。

**方案**（`frame_bin_codec.py`，云端 `frame_bin_codec.mjs` 对称实现）：

```
文件头：<4sBB>  magic="DLB1", version=1, n_keys=4
每路：  <HI>    key_len(u16), jpeg_len(u32)
        key_utf8 + jpeg_bytes
键顺序：固定为 ALL_LEROBOT_VIDEO_KEYS（front_left, front_right, rear_left, rear_right）
```

**效果**：每帧一次顺序写；上传 tar.zst 后云端一次 IO 解包四路；与 LeRobot 视频键稳定对齐。

### 4.4 段归档与上传协议（tar.zst）

**打包**（`segment_tar_zst.py`）：段目录 → zstd 压缩 tar（默认 L1），输出 `.upload/seg_XXXXXX.tar.zst`，计算 SHA256。

**上传**（`segment_upload.py` — `SegmentUploader`）：

```
POST {platform}/lerobot/api/collection/stations/{stationId}/upload
Content-Type: application/zstd
X-Upload-Protocol: tarzst
X-Session-Id: sess_<uuid>
X-Segment-Id: seg_000001
X-Segment-Seq: <int>
X-Content-Sha256: <hex>
X-Station-Token: <token>
X-Session-Segment-Total: <optional>
Body: tar.zst 二进制流
```

特性：

- 并发上传 `EGO_UPLOAD_CONCURRENCY`（默认 2）；
- 失败重试 `EGO_UPLOAD_MAX_RETRIES`；
- 与采集进程**完全解耦**（`record_oak_stream.py` 注释明确：upload via upload_segments CLI）。

**遗留协议**：`UPLOAD_PROTOCOL=multipart` 逐帧 JPEG（P1 路径，带宽较 tar.zst 差，仅回退）。

### 4.5 会话控制与断点续传

**checkpoint.json**（段根目录）字段：

- `sessionId`：全局 `sess_{uuid4}`
- `nextFrameIndex` / `segmentSeq`：断点位置
- `task`：自然语言任务描述（可选，供平台 `session.json`）

**session_start 控制面**（JSON POST 同 upload URL）：

```json
{
  "action": "session_start",
  "sessionId": "sess_...",
  "task": "...",
  "videoShapes": { "observation.images.camera_front_left": [800, 1280], ... },
  "cameraIntrinsics": { ... }
}
```

**heartbeat**：

```json
{ "action": "heartbeat", "captureState": "idle|recording", "host": "10.10.10.214" }
```

### 4.6 边缘 Web 控制（ego_web.py）

移动浏览器访问 `http://{214}:8080/`：

- 启停 `ecs-oak-capture-stack.target`（自动 stop standby）；
- 轮换 `checkpoint.sessionId`（新 session）；
- 展示采集状态、存储余量、预览快照；
- 防抖 `MIN_ACTION_INTERVAL_S` 避免误触连点。

**与硬件专利衔接点**：Web UI 是「人—机—采集栈」的软控制面，硬件按钮/指示灯可映射为同一 systemd target。

### 4.7 边缘 CLI 工具链

| 命令 | 路径 | 功能 |
|------|------|------|
| `ego-export` | `ego-local-web/scripts/ego-export` | 本地段打包导出（U 盘离线交付） |
| `ego-upload` | `ego-stream-client/cli/ego_upload.py` | 手动/批量上传 |
| `ego-derive-status` | `ego-local-web/scripts/ego-derive-status` | 查询平台派生进度 |

---

## 5. 云端数据平台子系统（34 平台）

### 5.1 服务组件

| 容器/进程 | 端口 | 职责 |
|-----------|------|------|
| `nginx` | 8080 | 路由 `/lerobot/`、`/collection` |
| `stream-ingest`（`ingest-server.mjs`） | 7862 | 上传签收、JSON 控制、import API |
| `lerobot`（`server.mjs`） | 7860 | 数据集静态服务、catalog、preview 反代 |
| `derive-worker`（`ego-derive-watch.mjs`） | — | 轮询 `session.DONE_UPLOAD` 执行派生 |
| `preview-proxy`（`preview-proxy.mjs`） | 内嵌 | 214:8765 MJPEG 反代 + 采集态 403 |

Docker 编排：`data-lab-platform/docker-compose.platform.yml`  
Nginx 片段：`data-lab-platform/gateway/nginx/snippets/datalab-stream-ingest.conf`（上传超时 300s、body 512MB）。

### 5.2 上传签收与幂等（stream-ingest.mjs）

**段级幂等键**：

- `live/sessions/{sessionId}/segments/{segmentId}.done`
- raw 文件路径 + `X-Content-Sha256`
- 提前判重：`tarzst_early_duplicate` 已 committed 则秒级 ACK

**帧级幂等**（multipart 遗留路径）：

- `sessionId + frameIndex` 重复则丢弃

**文件锁**（`.locks/`）：

- `jsonl.lock`、`parquet.lock`、`mux.lock` 避免并发写损坏

### 5.3 上传/派生隔离架构（商用 Phase A）

**问题**：extract + ffmpeg mux + parquet 在 HTTP 请求内完成导致上传超时、无法水平扩展。

**方案**（`docs/ego-lan-214-standalone-derive-operator.md`）：

```
stream-ingest（HTTP 轻量）
  → 流式接收 tar.zst → 写 raw/segments/{session}/{seg}.tar.zst
  → 会话段齐 → 写 session.DONE_UPLOAD
  → 不调用 extract/mux/parquet

derive-worker / ego-derive run（独立进程）
  → 读 session.DONE_UPLOAD
  → extract DLB1 → staging JPEG → append jsonl
  → append-segment-parquet.py（官方 LeRobot API）
  → mux-exec ffmpeg → videos/*.mp4
  → sync-stream-parquet.py → dense data/episodes parquet
  → 写 session.READY | session.FAILED
```

**磁盘 Session 状态机**（`session-markers.mjs`）：

| 标记文件 | 写入方 | 含义 |
|----------|--------|------|
| `state/sessions/{id}/session.DONE_UPLOAD` | stream-ingest | 该 session 全部段 raw 已到齐 |
| `session.DERIVING` | derive-worker | 派生进行中 |
| `session.READY` | derive-worker | parquet + MP4 校验通过 |
| `session.FAILED` | derive-worker | 失败快照 |

全局锁：`state/deriver.lock`（同站单进程派生）。

**效果**：上传 HTTP 秒级 ACK；派生可阻塞数分钟且可重试；raw 保留支持 **re-derive** 无需重采。

### 5.4 Session 级单 Episode + 全局帧索引重映射

**问题**：Scheme A 每 300 帧一段，若每段对应 LeRobot 一个 episode，长 session 侧边栏碎片化；且段内 `frame_index` 从 0 重启，与全局流式 aggregate 冲突。

**方案**（`stream-ingest.mjs`）：

- 常量 `SESSION_EPISODE_SEGMENT_ID = "__session__"`：一次 capture session 在 Viewer 中合并为**一个 episode**（可配置 `STREAM_SESSION_SINGLE_EPISODE`）。
- `sessionGlobalFrameOffset(root, sessionId)`：查已有 episode 的 `dataset_from_index`，或 `nextGlobalDatasetFrameIndex()`。
- `remapSegmentFramesForGlobalDataset()` / `remapRowsJsonlForGlobalOffset()`：将段内局部索引映射到全局连续 `frame_index`。
- `sync-stream-parquet.py` 中 `episode_index_for_frame()`：帧到 episode 切片；重叠时优先高 `episode_index`（修复 legacy 尾段与新 segment 重叠）。

**效果**：多 session 在同一流式数据集中帧索引单调；Episode 列表按 session 一行，便于质检导航。

### 5.5 Dense Parquet 同步与 Viewer 兼容

**问题**：LeRobot v3 Viewer 按 `dataset_from_index`/`dataset_to_index` 对 **data parquet 行号**切片；稀疏 jsonl 行会导致曲线/特征为空。

**方案**（`sync-stream-parquet.py`）：

1. 读取 `data/chunk-000/file-000.jsonl` 稀疏行；
2. 生成 `total_frames` 行稠密 parquet，缺失帧对向量特征 **forward-fill**；
3. 同步写 `meta/episodes/chunk-000/file-000.parquet`，`tasks` 为 list[string]；
4. **LeRobot v3 加载器行为**：会用 `meta/tasks.jsonl` 按 `task_index` **覆盖** episode 的 `tasks[0]`，故 `tasks.jsonl` 必须为**每个 episode 一行**（`task_index == episode_index`）；
5. Episode 列表第三行展示规则：`{精简任务名} · {MM-DD HH:mm} · {帧数}f`（不展示来源标签；`session.task` 空时精简名为「未命名任务」）。

### 5.6 Chunk 发布与边采边播隔离

`live/chunks.json` 记录各 artifact 状态：`writing` → `finished`。

- `info.viewer.json` 暴露当前可安全播放帧数；
- 前端 HTTP 数据源（`stream-http-source.js`）仅消费 `finished` chunk；
- mux 前 JPEG 在 `_staging/{camera_key}/`，mux 完成后 purge。

### 5.7 视频 Mux 策略

默认 `DERIVE_MUX_MODE=full`：

- parquet 就绪后，必要时从 raw tar.zst **rehydrate staging**；
- 四路相机各**一次性**编码完整 MP4（`mux-exec.mjs`，后端 `fluent`/`legacy` 可切换）；
- 避免增量 mux + staging 清理导致的帧数漂移（已验证的生产故障模式）。

### 5.8 远程预览代理与采集态门控

**方案 B**（`docs/ego-lan-214-remote-preview-policy.md`）：

| 条件 | 远程预览 |
|------|----------|
| `captureState === idle` 且 `COLLECTION_REMOTE_PREVIEW=1` | 允许 |
| `captureState === recording` | **403** `preview_capture_active` |
| 离线 | 不可用 |

实现：

- 214：`capture_state.py` 采集栈 active → 强制 `recording`；
- 34：`preview-proxy.mjs` 检查 `getStationCaptureState()`；
- UI：`remotePreviewAllowed = remotePreview && online && captureState === idle`；
- Live 模式用 JPG 轮询（`overlay-collection-mode.js`），不占用 MJPEG 长连接。

**技术效果**：采集主路径 USB/CPU 优先级最高；远程「实时」仅为待机质检旁路；**回放**仍可浏览已派生历史数据（与采集状态解耦）。

### 5.9 自动任务/Episode 命名

`task-naming.mjs` 规则：

```
{stationShortName} · {sessionId前8位} · {MM-DD}
例：EGO-214 · 081a430e · 07-08
```

列表展示可追加 `· {N}f`。替换遗留占位符 `Perform egocentric manipulation tasks at the laboratory workbench`。

### 5.10 LeRobot 流式 HTTP 数据集补丁

`patches/patch-lerobot-stream.mjs` + `branding/stream-http-source.js`：

- 支持 `stream://ego-lan-214` URL scheme；
- `HttpLeRobotSource` 通过 `fetch(..., { cache: "no-store" })` 读 `/lerobot/api/stream/{station}/`；
- Collection embed 模式（`?datalab_collection=1`）下注入 overlay 层，空数据集仍保留 Episodes 侧栏容器。

---

## 6. 数据格式、存储分层与协议规范

### 6.1 边缘段目录（214）

```text
segments/
├── checkpoint.json
├── registry.json
└── sessions/sess_<uuid>/
    ├── meta/camera_intrinsics.json
    └── segments/seg_000001/
        ├── manifest.json
        ├── rows.jsonl
        ├── frames/00000000.bin   # DLB1
        └── .upload/seg_000001.tar.zst
```

### 6.2 平台 LeRobot v3 流式数据集（34）

```text
ego-lan-214/
├── live/                    # 运行时（非 LeRobot 标准）
├── meta/
│   ├── info.json            # v3.0 features
│   ├── tasks.jsonl          # 每 episode 一行（Viewer 显示源）
│   ├── camera_intrinsics.json
│   └── episodes/chunk-000/file-000.parquet
├── data/chunk-000/
│   ├── file-000.jsonl       # ingest 实时追加
│   └── file-000.parquet     # dense sync
├── videos/observation.images.camera_*/chunk-000/file-000.mp4
├── _staging/                # mux 前 JPEG
├── raw/segments/            # tar.zst 原始保留
└── state/sessions/          # DONE_UPLOAD / READY 标记
```

### 6.3 Episodes / Annotations Parquet 扩展（标注）

`embodied-annotate/backend/ego_parquet.py` 定义：

**Episodes 元数据列**：`station_id`, `embodiment`, `task_id`, `annotation_status`, `pipeline_version`, `extended_info`, `quality_valid_hand_ratio`, `quality_mean_jitter` 等。

**Annotations 列**：`episode_index`, `frame_index`, `subtask_index`, `subtask_name`。

**手部 pose 特征**：`observation.hand_pose_left`, `observation.hand_pose_right`。

---

## 7. 端到端业务流程

### 7.1 标准采集—上传—派生—质检

```
1. 操作者在 214 ego_web 或 systemd 启动 ecs-oak-capture-stack
2. record_oak_stream：Oak4pEgoRecorder 30Hz 网格采帧
3. SegmentCaptureWriter：tmpfs 热写 → 300f/45s 关段 → segments/seg_XXX
4. upload_segments_loop：pack tar.zst → POST /upload
5. stream-ingest：校验 token/sha → raw 落盘 → jsonl/staging（sync 或 async）
6. 段齐 → session.DONE_UPLOAD
7. derive-worker：extract → parquet append → full mux → sync-stream-parquet
8. session.READY → chunks finished → info.viewer.json 更新
9. 用户在 34 /collection?station=ego-lan-214 切换「回放」浏览 LeRobot Viewer
```

### 7.2 待机远程预览

```
1. 214 ecs-oak-standby-stack → preview_standby :8765
2. heartbeat → captureState=idle
3. 34 COLLECTION_REMOTE_PREVIEW=1 → UI「实时」可点
4. overlay-collection-mode：四路 JPG 轮询叠加
5. 开始采集 → captureState=recording → 实时灰化 + proxy 403
```

### 7.3 离线上传 / 应急导入

- U 盘 `ego-export` → 34 采集页「导入」单段 tar.zst（`collection_import.py` → ingest）；
- 或任意机器 `curl POST` + `X-Station-Token`；
- **禁止**直接拷贝 214 原始 `segments/` 到 34 而不经 ingest 内核（格式与索引不会自动建立）。

### 7.4 后处理管线

```bash
ego-run-pipeline ego-lan-214
  → data-storage/pipeline/ego-lan-214/sess_*/dataset/
  → HaMeR 手部 → mano_kp2d / mano_pose parquet
  → append corpus/ego_214_hand_pose
  → deploy-local-datasets.sh
```

Viewer 叠加：`overlay-hand-keypoints-lib.mjs`（骨架叠 front_left；front_right 深度预览 `overlay-depth-preview-lib.mjs`）。

---

## 8. 采集质检与用户界面子系统

### 8.1 双层页面结构

| 层级 | URL | 内容 |
|------|-----|------|
| Layer 1 | `/collection` | 采集站列表（`collection-page.js`），20s 轮询 online |
| Layer 2 | `/collection?station=ego-lan-214` | 站级 Shell（`collection_viz.html`） |

### 8.2 站级 Shell 功能

- **模式切换**：「实时」/「回放」（parent toolbar pill）；
- **主题切换**：浅色/深色，与 iframe LeRobot **双向 postMessage 同步**（`overlay-collection-theme.js`）；
- **派生进度**：5s 轮询 `GET .../derive-status`；
- **流就绪**：等待 `meta/info.json`（`STREAM_INFO_TIMEOUT_MS=15000`）。

### 8.3 Parent ↔ Iframe 消息协议

| 消息类型 | 方向 | 载荷 |
|----------|------|------|
| `datalab-lerobot-theme` | Parent → Iframe | `{ theme: "light"\|"dark" }` |
| `datalab-lerobot-theme-sync` | Iframe → Parent | `{ theme, source: "iframe-ui" }` |
| `datalab-collection-mode` | Parent → Iframe | `{ mode: "preview"\|"dataset", online, captureState, remotePreviewAllowed }` |
| `datalab-collection-pause-replay` | Parent → Iframe | Live 模式暂停回放传输 |
| `datalab-import-done` | Iframe → Parent | 导入完成刷新 |

### 8.4 Episode 列表分层展示（采集质检创新）

LeRobot 侧栏固定三行：`#序号`、`mm:ss` 时长、`tasks[0]` 文案。

本系统第三行规则（`sync-stream-parquet.py` + `tasks.jsonl` 同步）：

```
{精简任务名} · {MM-DD HH:mm} · {帧数}f
```

- 精简名：已知长句 →「工作台操作」；`session.task` 空 →「未命名任务」；
- Hover：`session.task` 原文（空则无 tooltip，不回退平台默认句）；
- 完整 task 不占列表空间，符合工业数据管理「列表区分、详情展开」范式。

### 8.5 Overlay 注入层（iframe 内）

| 脚本 | 功能 |
|------|------|
| `overlay-collection-mode.js` | Live 四路 JPG overlay；断开预览时移除残留 DOM |
| `overlay-collection-theme.js` | 程序化触发 LeRobot 原生 setTheme（避免 uPlot 色板不同步） |
| `overlay-import.js` | Episodes 工具栏「导入」按钮 + episode tooltip |
| `overlay-hand-keypoints.js` | MANO 2D 关键点 Canvas |
| `stream-live-poll.js` | 流状态轮询（不 reset 数据集，防闪烁） |
| `stream-http-source.js` | HTTP 流式数据集打开 |

---

## 9. 后处理、手部姿态与标注扩展

### 9.1 手部关键点叠加（Viewer）

- 数据：`mano_kp2d_uv.{camera}.json` 或 sample API `hand-kp2d.json`；
- 渲染：`overlay-hand-keypoints-lib.mjs`；
- 左右手分层 reproj 阈值（示例：left 96px / right 12px）；
- Episode 检测优先级：URL `?episode=` > session fingerprint > aria-current 打分。

### 9.2 Embodied Annotate（EGO API）

| API | 功能 |
|-----|------|
| `GET /api/ego/load` | 加载 datasetPath + episodeIndex |
| `GET /api/ego/hand_poses` | 读 dense parquet 手部 pose |
| `POST /api/ego/save` | 写 `annotations.parquet` |

与流式采集的关系：标注消费**后处理完成**的 corpus 或 pipeline 输出，不阻塞实时 ingest。

---

## 10. 可靠性、安全与运维机制（P0–P3）

文档：`lerobot-studio/Stream-Optimization-P0-P3.md`

| 阶段 | 机制 |
|------|------|
| **P0** | 15s 心跳 / 45s TTL 判活；上传异步队列（max 300 帧）；session+frameIndex 幂等；checkpoint 断点续传 |
| **P1** | multipart 裸流（较 Base64 -35% 带宽）；原子写 + `.locks/`；chunk writing/finished 分离 |
| **P2** | systemd `Restart=always`；断网 held_job 指数退避；`STREAM_QUOTA_GB` + 7 天归档 + 85%/70% 水位清理 |
| **P3** | `[datalab-stream] session= frame= event=` 结构化日志；`X-Station-Token` 鉴权 |

**站点 Token**（`collection-station-tokens.json`）：

```json
{ "ego-lan-214": "dl-upload-ego-lan-214-v1" }
```

仅配置 Token 的站点强制校验；未配置站点不开启（渐进部署）。

**磁盘治理**：

- 214：`EGO_SEGMENT_QUOTA_GB` 删最旧 pending 段；
- 34：stream 热层超限 → archive 冷盘；staging 紧急清理；
- 记录：`live/disk-housekeeping.json`。

---

## 11. 专利候选技术点汇总

> 以下按「技术问题 → 技术手段 → 技术效果」表述，便于拆分独立权利要求或组合权利要求。

### 11.1 边缘段式中间格式与 DLB1 单帧四路打包

- **问题**：多相机 ego 采集每帧多文件随机写导致 IO 放大与掉帧。
- **手段**：定义 DLB1 二进制容器，固定键序打包四路 JPEG；open 段在 tmpfs 热写，关段原子搬迁；段级 tar.zst 原子上传。
- **效果**：单帧单次顺序写；上传包与云端解包格式对称；采集线程与上传线程解耦。

### 11.2 采集—上传—派生三级流水线与 Session 磁盘状态机

- **问题**：上传 HTTP 内同步 ffmpeg/parquet 导致超时与不可扩展。
- **手段**：ingest 仅 raw 落盘 + `session.DONE_UPLOAD`；独立 derive-worker 读标记执行 extract/mux/parquet；`session.READY/FAILED` 闭环；raw 保留支持 re-derive。
- **效果**：上传秒级 ACK；派生可重试；上传与转码资源隔离。

### 11.3 Session 级单 Episode 与全局帧索引重映射

- **问题**：固定长度段（300 帧）与 LeRobot episode 语义不一致；段内局部 frame_index 破坏流式 aggregate。
- **手段**：`__session__` 虚拟 segment_id；`sessionGlobalFrameOffset` + remap；dense parquet forward-fill；episode 重叠帧优先高 index。
- **效果**：一次采集 session 对应 Viewer 一行 episode；全局 frame_index 连续；曲线/特征切片正确。

### 11.4 采集优先的远程预览门控（方案 B）

- **问题**：远程 MJPEG 与四路写盘争抢 USB/CPU。
- **手段**：systemd 采集栈/待机栈互斥；`captureState` 心跳；preview-proxy 采集中 403；UI `remotePreviewAllowed` 三元与；回放路径独立。
- **效果**：采集中远程实时预览 fail-closed；待机可远程质检；已派生数据仍可回放。

### 11.5 双层 iframe 采集质检 Shell 与跨帧主题/模式同步协议

- **问题**：LeRobot Viewer 嵌入采集站需统一 chrome，且主题不同步导致曲线色板错乱。
- **手段**：parent 提供模式/主题/derive 进度；iframe overlay 隐藏重复 toolbar；`datalab-lerobot-theme` 双向消息；主题切换优先触发 LeRobot 原生 setTheme。
- **效果**：单一操作面；深浅色与图表一致；Live 模式暂停回放减带宽。

### 11.6 LeRobot v3 流式 HTTP 数据集与 Chunk 发布契约

- **问题**：边采边播读到 writing chunk 花屏。
- **手段**：`chunks.json` artifact 状态机；`info.viewer.json` 可播帧数；HTTP source `cache: no-store`；finished 后才 viewer publish。
- **效果**：质检 UI 稳定；流式数据持续增长而不破坏正在播放的已完成部分。

### 11.7 多 Session Episode 列表分层展示与 tasks.jsonl 协同

- **问题**：LeRobot v3 用 tasks.jsonl 覆盖 episode.tasks[0]，导致多 episode 显示相同长文本。
- **手段**：每 episode 一行 tasks.jsonl（task_index=episode_index）；第三行 `{精简名}·时间·帧数`；hover 仅 session.task 原文。
- **效果**：列表可区分 episode；业务语义与区分维度分离。

### 11.8 内参严格模式与会话级标定上传

- **问题**：无标定数据污染下游 SLAM/手部重建。
- **手段**：EEPROM 读取 + `intrinsics_strict_required` 开录门禁；`session_start` 上传 `camera_intrinsics.json`。
- **效果**：每 session 标定可追溯；无效内参拒绝采集。

### 11.9 EgoVerse 30/200 时序网格与 IMU 对齐

- **问题**：RGB 与 IMU 频率差异大，训练用时间对齐困难。
- **手段**：30Hz RGB 网格 + 200Hz IMU；`camera_ts_offset_ns` 记录各相机相对主 tick 偏移；生产配置 CI 门禁禁止 20Hz 回退。
- **效果**：统一时序语义；可复现生产标准。

### 11.10 手部 MANO 叠加与深度预览功能分区

- **问题**：四路画面中骨架与深度信息叠加需求不同。
- **手段**：front_left Canvas 骨架；front_right 深度预览层；分层 reproj 阈值；episode 多源检测优先级。
- **效果**：同 Viewer 内多模态质检不互相遮挡。

---

## 12. 软硬件接口边界（供与硬件专利合并）

| 接口 | 软件侧 | 硬件侧（建议硬件专利覆盖） |
|------|--------|---------------------------|
| 相机数据 | DepthAI API、socket 映射、JPEG 编码 | OAK-FFC-4P 光学布局、安装角度、同步触发 |
| IMU | 200Hz 采样写入 observation.state | IMU 芯片选型、与相机刚性连接 |
| 内参 | EEPROM 读取、JSON schema | 产线标定流程、标定工装 |
| 预览 | :8765 MJPEG/JPG HTTP | 预览灯/状态指示（可选） |
| 采集控制 | systemd target、ego_web HTTP | 物理按钮、急停、状态 LED |
| 网络 | LAN HTTP 上传、Token | 网口/WiFi 模组、天线 |
| 存储 | tmpfs + NVMe 段队列 | SSD 容量、散热、写放大硬件策略 |
| 供电/热 | 软件背压与段关断 | 电源管理、热设计 |

**合并专利建议结构**：

1. **独立权利要求**：硬件装置（相机阵列 + 计算单元 + 结构/标定）。
2. **独立权利要求**：边缘段式采集方法（DLB1 + tmpfs + tar.zst）。
3. **独立权利要求**：云端 ingest/派生状态机方法。
4. **系统权利要求**：边端 + 云端 + 质检 UI 组合的 ego 数据采集系统。

---

## 13. 附录：代码路径、API 与配置索引

### 13.1 边缘核心源码

```
data-lab-platform/ego-stream-client/
├── oak_4p_capture.py          # OAK 四路采集
├── camera_map.py              # Socket → LeRobot 键映射
├── segment_store.py           # SegmentCaptureWriter、tmpfs 热写
├── frame_bin_codec.py         # DLB1 编解码
├── segment_tar_zst.py         # tar.zst 打包
├── segment_upload.py          # HTTP 上传客户端
├── record_oak_stream.py       # 采集主进程
├── capture_state.py           # idle/recording 状态
├── preview_standby.py         # 待机预览
├── camera_intrinsics.py       # 内参 EEPROM
├── tools/upload_segments_loop.py
├── cli/ego_upload.py
└── systemd/ecs-*.service

data-lab-platform/ego-local-web/
├── ego_web.py                 # 移动 Web 控制
└── scripts/ego-export, ego-derive-status
```

### 13.2 平台核心源码

```
data-lab-platform/lerobot-studio/
├── ingest-server.mjs
├── stream-ingest.mjs
├── derive-pipeline.mjs
├── derive-async.mjs
├── session-markers.mjs
├── mux-exec.mjs
├── ego-derive-run.mjs
├── ego-derive-watch.mjs
├── preview-proxy.mjs
├── server.mjs
├── import-handlers.mjs
├── task-naming.mjs
├── scripts/sync-stream-parquet.py
├── scripts/append-segment-parquet.py
├── scripts/extract-tar-zst.py
├── branding/overlay-*.js
├── branding/stream-http-source.js
├── config/collection-stations.json
└── config/collection-station-tokens.json
```

### 13.3 UI

```
label_studio/templates/datalab/collection_viz.html
label_studio/core/collection_import.py
data-lab-platform/client/collection-page.js
data-lab-platform/client/collection-viz-shell.css
```

### 13.4 主要 API

**采集控制面**（lerobot server）：

- `GET /lerobot/api/collection/stations`
- `GET /lerobot/api/collection/stations/{id}/ping`
- `GET /lerobot/api/collection/stations/{id}/preview/{cam}/{mjpeg|jpg}`
- `GET /lerobot/api/stream/{station}/status`
- `GET /lerobot/api/stream/{station}/**` — LeRobot 数据集文件

**Ingest 数据面**（stream-ingest）：

- `POST /lerobot/api/collection/stations/{id}/upload` — tar.zst / JSON / multipart
- `GET /lerobot/api/collection/stations/{id}/derive-status`
- `POST /lerobot/api/collection/stations/{id}/import/upload`

### 13.5 参考运维文档

| 文档 | 主题 |
|------|------|
| `docs/ego-lan-214-segment-storage-and-upload.md` | 段存储与 LeRobot 关系 |
| `docs/ego-lan-214-remote-preview-policy.md` | 远程预览方案 B |
| `docs/ego-lan-214-standalone-derive-operator.md` | 上传/派生隔离 |
| `docs/ego-lan-214-capture-standard.md` | EgoVerse 30/200 标准 |
| `docs/ego-lan-214-upload-channel-policy.md` | Agent 上传通道 |
| `docs/ego-edge-offline-upload-and-deployment.md` | 离线上传部署 |
| `docs/ego-dataset-management.md` | Viewer vs 训练数据 |
| `docs/ego-lan-214-pipeline-runbook.md` | 全流程 runbook |
| `lerobot-studio/Stream-Optimization-P0-P3.md` | P0–P3 优化 |

---

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-07-22 | 初版：基于 data-lab 仓库代码与 docs 汇编，供 EGO 软硬件专利交底使用 |

---

*本文档描述的是项目实施技术事实，不构成已授权专利或法律意见。正式申请前需由专利代理人结合硬件交底书进行现有技术检索与权利要求撰写。*
