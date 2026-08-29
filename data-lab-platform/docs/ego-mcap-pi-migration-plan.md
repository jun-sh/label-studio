# EGO 全链路 MCAP 对齐规划（Physical Intelligence 模式）

> **状态：** 规划稿 v0.4 · P0–P3 已落地并现场签收；**P4 代码就绪**（待现场 E2E 验收 + 7 天试点）  
> **执行：** 确认后在 **`feat/ego-mcap-pi` 测试支路** 实施；**主线 `deploy-release` / ego-001 零变更**  
> **目标：** 采集端 MCAP → 服务器/工作站转 LeRobot v3 → 训练/交付  
> **基线版本：** v0.1.3 async（`tar.zst` + DLB1 + unit derive）  
> **v0.2 变更：** 纳入 130 H.264 POC 结论；拆 **Track 1（MCAP 容器）** / **Track 2（VPU H.264 + derive remux）**；补充 v0.0.12→v0.0.13 H.264 关停根因与避坑清单

---

## 1. 目标与边界

### 1.1 要对齐什么

对齐 **Physical Intelligence（π0 / openpi）数据哲学**，而非逐行复制其代码：

```text
[采集端]  录制成标准 MCAP（多 topic、时间戳对齐）
    ↓  HTTP 分段上传
[服务器]  MCAP 入库 → 转 LeRobot v3（parquet + MP4 + meta）
    ↓
[后处理]  ego-platform convert → corpus / samples / Viewer
```

参考：openpi 的 `convert_mcap_data_to_lerobot.py` —— **采集格式与训练格式解耦，转换在算力充足侧完成**。

### 1.2 明确不对齐什么

| 项 | 说明 |
|----|------|
| 边缘 MP4 / H.264 软编 | 130 为 GPD MicroPC 2 低功耗设备；**禁止**在端侧做 4 路 x264 |
| 废弃现有 LeRobot v3 交付物 | `data/`、`videos/`、`sensor_raw/imu/`（unit layout；非 v0.0.11 `high_freq/`）、`vendor_meta` 等继续保留 |
| 一次性切生产 | 必须支持 **tar.zst 与 MCAP 双协议并行** 过渡期 |
| 重写 ego-platform convert | convert 仍消费 derive 后的 LeRobot 视图 |

### 1.3 130 低功耗约束（硬约束）

生产配置已锁定（v0.1.3）：

- `OAK_HW_JPEG=1`、`OAK_H264=0`、`SEGMENT_H264=0`
- 活跃段写 tmpfs（`/dev/shm/ego-capture-active`）

**v0.2 修订：边缘 MCAP 分两条轨，不再写死单一 JPEG 形态。**

| 轨道 | 边缘 MCAP 内容 | 34 derive | derive 加速 |
|------|----------------|-----------|-------------|
| **Track 1**（主轨，先上） | `foxglove.CompressedImage`（HW JPEG）+ IMU + json topic | JPEG → libx264 MP4（与现网相同） | 否 |
| **Track 2**（性能轨，后上） | `foxglove.CompressedVideo`（OAK VPU H.264）+ IMU + json topic | **copy/remux** MP4，跳过 `MUX_ENCODE` x264 | 是 |

Track 1 满足 PI「MCAP 容器 + 服务端转 LeRobot」与 Foxglove 可观测性；Track 2 才兑现 derive 加速。两条轨共用 upload / ingest / session 状态机，仅在 writer topic 与 derive 视频分支不同。

**禁止项不变：** 130 端 **4 路 libx264 软编**（GPD CPU 扛不住）；VPU H.264 属于 Track 2 允许范围。

---

## 2. 现状 vs 目标

### 2.1 数据形态对比

| 层级 | 现状（v0.1.3） | 目标（PI 对齐） |
|------|---------------|----------------|
| 边缘单段内容 | `manifest.json` + `rows.jsonl` + `imu_raw.jsonl` + `frames/*.bin`（DLB1 JPEG） | **单个 `.mcap`**（多 topic） |
| 边缘上传包 | `sess_*__seg_*.tar.zst` | `sess_*__seg_*.mcap` 或 `sess_*__seg_*.mcap.zst` |
| 原始归档 | `raw/segments/{sess}/{seg}.tar.zst` | `raw/segments/{sess}/{seg}.mcap[.zst]` |
| 派生输入 | 解压 tar → DLB1 → staging JPEG → libx264 MP4 | Track1：读 MCAP JPEG → x264；Track2：读 MCAP H.264 → **remux** |
| 训练视图 | LeRobot v3 stream + corpus | **不变** |
| 可观测性 | 自研 progress JSON | 可用 Foxglove 直接打开 MCAP（附加收益） |

### 2.2 可复用 vs 需改造

#### 可直接复用（约 60% 编排与基础设施）

- Session 状态机：`session.DONE_UPLOAD` → `DERIVING` → `READY` / `FAILED`
- 上传编排：`ego-upload`、`segment_upload.py` 的重试/并发/背压/notify
- HTTP 鉴权：`X-Station-Token`、station registry、topology
- 存储根路径：`data-storage/stream/{station}/` 分层（raw / derived / state / live）
- derive-worker：watch、kick、claim、heartbeat、多 worker 扩展
- Publisher + ready-gate（G1–G6）框架
- `ego-process` → `ego-run-pipeline` → `ego-platform convert` 全链
- process-notify watcher、Collection / egodome 验收 URL
- Docker compose、deploy 脚本模式

#### 必须改造（核心）

| 模块 | 改造内容 |
|------|---------|
| **130 采集** | `segment_store` / `record_oak_stream`：DLB1 落盘 → MCAP writer |
| **130 打包** | `segment_tar_zst.py` → MCAP 段关闭/校验（可选 zstd 外壳） |
| **130 上传** | 新 `UPLOAD_PROTOCOL=mcap`；Content-Type / 文件名解析 |
| **34 ingest** | `receive-tar.mjs` 旁路新增 `receive-mcap.mjs`；校验 topic/schema |
| **34 derive** | `unit.mjs`：`materializeUnitFrames` 从 MCAP 读帧，替代 DLB1 |
| **测试/RC** | 新 fixture、双协议 preflight、回归对比脚本 |
| **文档/运维** | 使用手册、provision、verify 脚本 |

#### 历史包袱

- v0.0.11 **已删除** MCAP 导出与 mcap-viewer（见 `RELEASE-v0.0.11.md`）
- v0.0.13 Foxglove 重构 **主动删除** `SEGMENT_H264` / `segment_mp4` 分支（见 §14.1）；生产锁定 JPEG DLB1
- v0.0.12 H.264 调参（`OAK_H264_SEQUENTIAL`、`OAK_CAM_QUEUE_MAX=32` 等）需在 Track 2 **有选择地合回**，不能仅靠 env 开关
- `scripts/legacy/ego-130-verify-h264.sh` 等仅作参考；130 POC 报告见 `/tmp/ego-h264-poc/report.json`（2026-08-27）

---

## 3. 目标 MCAP Schema（草案）

> 评审前需与后端确认 topic 命名与 Foxglove schema 版本。

### 3.1 文件切分

与现 segment 策略一致：

- **一切片 ≈ 一个 MCAP 文件**（~75s / ~1800 帧）
- 命名：`{session_id}__{segment_id}.mcap`（对齐现有 tar 命名）
- 可选：上传前 `zstd` 压缩为 `.mcap.zst`（level 1，与 tar.zst 同级开销）

### 3.2 Topic 规划（ego-standard 四相机）

| Topic | Schema | 频率 | 内容 |
|-------|--------|------|------|
| `/ego/session_meta` | `json` 或 `foxglove.RawImage` 旁路 | 段首 1 条 | session_id, segment_id, station_id, topology |
| `/ego/camera/front_left` | `foxglove.CompressedImage` | 30 Hz | OAK HW JPEG |
| `/ego/camera/front_right` | 同上 | 30 Hz | |
| `/ego/camera/rear_left` | 同上 | 30 Hz | |
| `/ego/camera/rear_right` | 同上 | 30 Hz | |
| `/ego/imu/raw` | 自定义或 `sensor_msgs/Imu` 等价 | 200 Hz | 与现 `imu_raw.jsonl` 对齐 |
| `/ego/observation/state` | `json` | 30 Hz | 与现 `rows.jsonl` 中 `observation.state` 对齐 |
| `/ego/observation/pose` | `json` | 30 Hz | 与现 `observation.pose` 对齐 |
| `/ego/task` | `json` | 30 Hz | task 字符串 |

**LeRobot video key 映射**（写入 MCAP metadata 或 sidecar）：

```text
/ego/camera/front_left  → observation.images.camera_front_left
...
```

### 3.3 与现 jsonl 的关系

过渡期 **MCAP 为唯一真源**；不再在段内写 `rows.jsonl` / `imu_raw.jsonl` / `frames/*.bin`。

derive 负责从 MCAP 生成：

- LeRobot 主表 parquet 行
- `sensor_raw/imu/chunk-000/file-000.parquet`（unit layout；unit 目录内别名为 `imu.parquet`；**非** v0.0.11 遗留 `high_freq/imu_200hz.parquet`）
- 各相机 MP4

---

## 4. 目标架构图

```text
┌──────────── 130 采集端（低功耗） ────────────┐
│ OAK 4-cam HW JPEG                             │
│ segment_store → McapSegmentWriter               │
│ 关闭段 → sess_*__seg_*.mcap[.zst]             │
│ ego-upload (UPLOAD_PROTOCOL=mcap)               │
└────────────────────┬──────────────────────────┘
                     │ POST .../upload
                     ▼
┌──────────── 34 平台 ──────────────────────────┐
│ stream-ingest                                   │
│   raw/segments/{sess}/{seg}.mcap                │
│   segment → DERIVE_PENDING（终态，含 sourceFormat）│
│   session.DONE_UPLOAD → DERIVING → READY        │
│                                                 │
│ derive-worker (layout=unit)                     │
│   McapReader → frames / imu / rows              │
│   libx264 MP4（Track1 JPEG→x264，与现网相同）   │
│   parquet + sensor_raw IMU + ready-gate G1–G3   │
│   session.DERIVING → session.READY              │
│   derived/<session_id>/unit.json（unit layout） │
│                                                 │
│ ego-process-watcher → ego-process               │
│   ego-platform convert → corpus / samples       │
└─────────────────────────────────────────────────┘
```

**与 PI 对齐点：** 边缘只产 MCAP；LeRobot 只在 34 生成。  
**与现网对齐点：** MP4 编码仍在 derive-worker（130 不算力换带宽）。

---

## 5. 分阶段实施计划

建议在测试分支 **`feat/ego-mcap-pi`**（名称可评审后定）按 **双轨** 推进：Track 1（§5.1）先完成 MCAP 容器迁移；Track 2（§5.2）在 POC Gate 通过后叠加 H.264 + remux。每 Phase 可独立验收、可回滚。

### 5.0 双轨总览

```text
Track 1（MCAP 容器，与 H.264 解耦）     P0 → P1 → P2 → P3 → P4
Track 2（Foxglove 性能，依赖 POC Gate）  P2b → P3b → P4b →（P5 与 Track1 合并清理）
```

**POC Gate（Track 2 开工前）：** 130 隔离环境 2 段连续录、4 路 ffprobe 帧数 = rows、段关闭不阻塞 `persist_q`、derive remux 基准测试通过。

### Phase 0 — 设计冻结与脚手架（1–2 周）

**交付物（文档 + 测试夹具，无生产行为变更）：**

- [ ] 本文档评审通过 + MCAP schema 定稿
- [ ] 录制 1 个 golden MCAP fixture（手工或 spike 脚本，不在主路径）
- [ ] 定义 `UPLOAD_PROTOCOL=mcap` 的 HTTP 契约（headers、错误码、与 tarzst 并列）
- [ ] 明确双协议共存期策略（按 station 或按 env 开关）
- [ ] 更新 `ego-core-pipeline.md` 增加「目标态」章节

**验收：** 评审签字；fixture 可被 Foxglove Studio 打开且 topic 完整。

---

### Phase 1 — 边缘 MCAP 写入（130 only）

**范围：** 仅 130 采集栈；上传仍可走旧 tar.zst 或新协议（feature flag）。

| 任务 | 说明 |
|------|------|
| 新增 `mcap_segment_writer.py` | 封装 `mcap` Python SDK；段打开/写消息/关闭 |
| 改造 `segment_store` | `SEGMENT_MCAP=1` 时走 MCAP writer，替代 DLB1 persist |
| 改造段关闭逻辑 | 完整性：消息计数、时间跨度、四路相机非空 |
| 新增 `segment_mcap.py` | 可选 zstd 压缩、sha256（对齐 `segment_tar_zst` 接口） |
| 环境变量 | `SEGMENT_MCAP=1`、`UPLOAD_PROTOCOL=mcap`（默认关） |
| systemd drop-in | `z-mcap-pilot.conf`（仅测试机 130） |
| 单元测试 | `test_mcap_segment_writer.py`、段完整性 |

**不改：** 34 ingest/derive、生产 `z-production-egoverse.conf`。

**验收：**

- 130 录 1 session（3+ 段）→ 本地 MCAP 可被 Foxglove 回放
- CPU/功耗对比现网 JPEG 路线：帧率不掉、无明显温升（对比 `ego-130-verify-production`）
- 段大小与 tar.zst 同量级（±30% 可接受）

---

### Phase 2 — 上传与入库（130 → 34 ingest）

**范围：** 双协议 upload + ingest 分支；derive **暂不**切 MCAP。

| 任务 | 说明 |
|------|------|
| `segment_upload.py` | 支持 `.mcap` / `.mcap.zst` body |
| `ingest/receive-mcap.mjs` | 落盘 `raw/segments/{sess}/{seg}.mcap` |
| `ingest/mcap-validator.mjs` | topic 白名单、帧数、时间单调性 |
| `stream-ingest.mjs` | `X-Upload-Protocol: mcap` 路由 |
| segment-state | `DERIVE_PENDING` 时标注 `sourceFormat: mcap` |
| 兼容 | tar.zst 路径零回归 |

**验收：**

- 130 `ego-upload` 上传 MCAP → 34 `raw/segments/` 可见
- `session.DONE_UPLOAD` 正常
- tar.zst 回归：`ingest/ingest.test.mjs` 全绿
- `derive-status` API 返回 `sourceFormat`

---

### Phase 3 — 服务端 MCAP → LeRobot derive

**范围：** derive-worker 读 MCAP 产出与现网 **bit-for-bit 等价** 的 LeRobot 视图（允许 MP4 编码参数微小差异）。

| 任务 | 说明 |
|------|------|
| `derive/mcap-reader.mjs` 或 Python | 解 MCAP → 帧/IMU/元数据 |
| `derive/unit.mjs` | `sourceFormat===mcap` 分支，替代 DLB1 materialize |
| `derive/imu/ingest-raw.py` | 增加 MCAP 输入适配 |
| ready-gate | G1–G6 对 MCAP 来源调整阈值说明 |
| progress | phase 命名保持 EXTRACT→TABLE→MUX_*→READY |
| 删除路径 | **不**删 DLB1 路径，保留至 Phase 5 |

**验收（P3 scope：仅 derive 链路，不含 ego-process / Collection / egodome）：**

- **Session 状态机（权威）：** `session.DONE_UPLOAD` → `session.DERIVING` → `session.READY`；全程**不得**出现 `session.FAILED`
- **Segment 状态机（勿误判）：** ingest 完成后 segment 终态为 `DERIVE_PENDING`（`sourceFormat: mcap`）；**不存在** segment 级 `DERIVING` / `READY`——segment 保持 `DERIVE_PENDING` 属正常现象
- **产出路径（unit layout）：** `data-storage/stream/ego-mcap-pilot/derived/<session_id>/`
  - `unit.json`（`derive.source_format: "mcap"`）
  - `data.parquet`、4× `videos/observation.images.camera_*.mp4`
  - IMU：`sensor_raw/imu/chunk-000/file-000.parquet`（derive 中间产物）；unit 顶层别名 `imu.parquet`
  - ❌ **非** `derived/sessions/{session}/segments/{seg_id}/`（旧 per-segment 布局）
  - ❌ **非** `high_freq/imu_200hz.parquet`（v0.0.11 遗留路径）
- golden + 现场 `*.mcap.zst` 样本均可 derive 至 `session.READY`；ready-gate G1–G3 通过
- `rc-ego-001-production.sh` fail=0（tar.zst 主线零回归）
- Collection 预览、ego-process、egodome convert → **P4 范围**，P3 不验收

---

### Phase 4 — 后处理与生产切换

**范围：** ego-process、convert、viewer、运维脚本、生产切流。

| 任务 | 说明 |
|------|------|
| `ego-process` / watcher | 无逻辑变更；确认 MCAP 会话 discover 正常 |
| `ego-run-pipeline` | convert 输入不变 |
| `ego-130-provision.sh` | 新 profile：`mcap-production` |
| `ego-130-verify-production.sh` | 断言 `SEGMENT_MCAP=1` |
| `deploy-stream-ingest-v0.1.4-mcap.sh` | 新 compose overlay（可选） |
| 文档 | `ego-001-使用手册.md` 增加 MCAP 操作说明 |
| 生产切换 | ego-001 单站试点 → 全站 |

**验收：**

- `/data/egodome` episode 数与 Collection 一致
- `ego-deliver ego-001` 端到端
- 7 天试点无 `session.FAILED` 异常堆积

---

### Phase 5 — 清理与优化（可选，试点稳定后）

- 移除 DLB1：`frame_bin_codec.py`、`frame_bin_codec.mjs`
- 移除 tar.zst 生产路径（保留 import 兼容 1 个版本）
- Track 1 + Track 2 均稳定后，统一下线 JPEG→x264 derive 路径（仅保留 remux）
- 归档 v0.0.11 前 MCAP 相关 deprecated 文档

---

### 5.2 Track 2 — VPU H.264 + derive remux（Foxglove 性能轨）

> **前置：** §14 POC 已证明 VPU 可行，但 **未 production-ready**；本节在 POC Gate 通过后实施。

| Phase | 任务 | Gate |
|-------|------|------|
| **P2b** | 合回 v0.0.12 调参（§14.3）；段关闭 **异步 + copy-only**；禁止 rollover 走 libx264 trim | 2 段连续录，3/4 路以上 parity（目标 4/4） |
| **P3b** | MCAP 写 `foxglove.CompressedVideo`（4 路 H.264 topic）；schema 版本化 | Foxglove Studio 可回放 4 路 |
| **P4b** | derive `videoCodec=h264` 分支：**remux/copy** → LeRobot MP4；跳过 `MUX_ENCODE` | 同 session derive 耗时较 Track1 ↓50%+（需基准） |
| **P5** | 与 Track1 P5 合并：下线 tar.zst + DLB1 + JPEG derive | RC 双协议 + H.264 路径全绿 |

**Track 2 不上线条件（任一命中则 hold）：**

- 段关闭时 `persist_q` 持续攀升（POC 曾达 1229）
- 任一路 ffprobe 帧数 ≠ rows（POC rear_right 1029/1235）
- rollover 触发 libx264 重编码（POC seg_000001 front_right trim 失败）
- 2h 连续录温升/掉帧劣于 JPEG 基线

---

## 6. 测试分支策略

### 6.1 分支模型

```text
main (生产 tar.zst，不动直到 Phase 4 评审)
  └── feat/ego-mcap-pi          # 长期集成分支
        ├── feat/mcap-p0-schema
        ├── feat/mcap-p1-edge-writer
        ├── feat/mcap-p2-ingest
        └── feat/mcap-p3-derive
```

- 每 Phase 从 `feat/ego-mcap-pi` 拉短分支，PR 回 `feat/ego-mcap-pi`
- **禁止** Phase 1–3 合入 `main`
- Phase 4 完成后：`feat/ego-mcap-pi` → `main`（大 PR + RC 窗口）

### 6.2 环境隔离

| 环境 | 130 | 34 | 协议 |
|------|-----|-----|------|
| 生产 | 现网 | 现网 | tar.zst |
| MCAP 试点 | 130 或 ego-lab-01 | 34 测试 overlay | mcap |
| CI | docker compose local | unit + integration tests | 双协议 |

建议：试点站先用 **`ego-lab-01`**，稳定后再切 **`ego-001`**。

### 6.3 回归矩阵

| 用例 | tar.zst | mcap |
|------|---------|------|
| 单 session 3 段上传 | ✓ 必过 | ✓ 必过 |
| 断点重传 / 幂等 | ✓ | ✓ |
| derive READY | ✓ | ✓ |
| ego-process → egodome | ✓ | ✓ |
| sensor_raw IMU parquet | ✓ | ✓ |
| 多 worker derive | ✓ | ✓ |
| 低功耗 30min 连续录 | ✓ | ✓ 不比 tar 更差 |

---

## 7. 关键接口契约（实施前冻结）

### 7.1 上传 HTTP

在现有 `POST /lerobot/api/collection/stations/:id/upload` 上扩展：

| Header | tar.zst（现） | mcap（新） |
|--------|--------------|-----------|
| `X-Upload-Protocol` | `tarzst` | `mcap` |
| `Content-Type` | `application/zstd` | `application/mcap` 或 `application/zstd`（mcap.zst） |
| `X-Content-Sha256` | 压缩后 hash | 同 |
| 落盘路径 | `{seg}.tar.zst` | `{seg}.mcap` |

### 7.2 Segment 状态扩展

`state/segments/{sess}/{seg}.json` 增加：

```json
{
  "sourceFormat": "tarzst | mcap",
  "mcapTopics": ["..."],
  "frameCount": 1800
}
```

### 7.3 Session marker

`session.DONE_UPLOAD` / `READY` **语义不变**；meta 可增加 `uploadProtocol: mcap`。

---

## 8. 依赖与工具链

| 依赖 | 用途 | 备注 |
|------|------|------|
| `mcap` Python SDK | 130 写入 | 与 Foxglove 生态一致 |
| `@mcap/core` 或 Python reader | 34 derive | 选型 Phase 0 定 |
| 现有 `ffmpeg` / `encode-pool` | JPEG→H264 | 复用 |
| Foxglove Studio | 开发调试 | 非运行时依赖 |

**Docker 镜像：** `data-lab-lerobot-studio` 需重新加入 MCAP 读库（v0.0.11 曾移除）；版本建议 **v0.1.4-mcap**。

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| MCAP 段体积大于 tar.zst | 上传变慢 | 可选 `.mcap.zst`；保持 75s 切段 |
| 130 写 MCAP CPU 开销 | 掉帧 | 异步 writer；tmpfs；对标现 persist 线程模型 |
| derive 双路径维护成本 |  bug 面翻倍 | Phase 5 前保留 DLB1；共享 ready-gate / publisher |
| v0.0.11 删 MCAP 后重引入 | 历史债务 | 新代码独立模块，不恢复旧 mcap-viewer |
| MCAP schema 演进 | 旧段不可读 | `schema_version` in session_meta topic |
| 试点影响生产 | 数据污染 | 试点用 `ego-lab-01` 或独立 `data-storage/stream/ego-mcap-pilot/` |

---

## 10. 待评审决策项

| # | 问题 | 建议 | 备选 |
|---|------|------|------|
| D1 | 上传压缩：裸 `.mcap` 还是 `.mcap.zst`？ | **`.mcap.zst`**（与 tar.zst 一致） | 裸 mcap 简化调试 |
| D2 | derive 读 MCAP：Node 还是 Python？ | **Python**（与 `ingest-imu-high-freq.py` 一致） | Node `@mcap/core` |
| D3 | 试点站点 | **ego-lab-01** 先于 ego-001 | 直接 ego-001 |
| D4 | 双协议共存多久 | **≥2 个 release** | 快速硬切 |
| D5 | Foxglove topic 命名空间 | `/ego/camera/*` | 直接用 LeRobot key 作 topic |
| D6 | 是否恢复 mcap-viewer 服务 | **否**（用 Foxglove 桌面） | 容器内嵌轻量 viewer |
| D7 | Track1 JPEG-MCAP 与 Track2 H264 是否一起上 | **先 Track1**（降风险） | 等待 Track2 POC Gate |
| D8 | H.264 MCAP topic schema | **`foxglove.CompressedVideo`** + metadata 映射 LeRobot key | Annex-B raw + sidecar |
| D9 | derive 双视频路径共存多久 | **≥2 release**；`sourceFormat` + `videoCodec` 标注 | 快速硬切 remux |
| D10 | 段关闭策略 | **独立 persist 线程 + tmpfs active**；mux 仅 copy | 同步阻塞（现 v0.0.12 行为） |
| D11 | Track2 试点站 | **ego-lab-01** 先于 ego-001 | 直接 ego-001 |

---

## 11. 里程碑时间线（估算）

| Phase | 工期 | 累计 |
|-------|------|------|
| P0 设计冻结 | 1–2 周 | 2 周 |
| P1 边缘写入 | 2–3 周 | 5 周 |
| P2 上传入库 | 1–2 周 | 7 周 |
| P3 derive 转换 | 2–3 周 | 10 周 |
| P4 生产切换 | 1–2 周 | 12 周 |
| P5 清理优化 | 1 周 | 13 周 |

*以上为单人主导 + 评审周期的保守估计；P1/P3 可并行预备（schema/fixture）。*

---

## 12. 成功标准（Go-Live）

1. **ego-001** 日常 SOP 仍为：`ego-upload` → 自动 `ego-process`；操作员无感切换协议
2. Collection 与 egodome episode 数一致
3. 130 连续录制 2h：无掉帧、无过热降频（对比现网基线）
4. `tar.zst` 与 `mcap` 双协议 RC 全绿
5. 后端评审项 D1–D6 均有书面结论
6. `ego-core-pipeline.md` 更新为 MCAP 目标态

---

## 13. 相关文档与代码索引

| 类型 | 路径 |
|------|------|
| 现状核心方案 | `docs/ego-core-pipeline.md` |
| MCAP 剥离记录 | `RELEASE-v0.0.11.md` |
| H.264 100% parity 签收 | `RELEASE-v0.0.12.md`、`docs/_deprecated/STABLE-v0.0.12.md` |
| H.264 关停决策 | `docs/_deprecated/ego-001-phase0-design-freeze.md` §0、§13 |
| v0.0.13 删除 H264 记录 | `docs/_deprecated/ego-001-v0.0.13-local-release-notes.md` |
| 130 H.264 POC 报告 | `10.10.10.130:/tmp/ego-h264-poc/report.json` |
| 130 生产 env | `ego-stream-client/systemd/.../z-production-egoverse.conf` |
| 段打包（现） | `ego-stream-client/segment_tar_zst.py` |
| Ingest（现） | `lerobot-studio/ingest/receive-tar.mjs` |
| Derive unit（现） | `lerobot-studio/derive/unit.mjs` |
| PI 参考 | [Physical-Intelligence/openpi](https://github.com/Physical-Intelligence/openpi) `convert_mcap_data_to_lerobot.py` |

---

## 14. v0.2 修订专题

### 14.1 为什么 v0.0.12 的 H.264 后来被关掉？

**结论：不是因为 v0.0.12 H.264 失败，而是 v0.0.13 Foxglove 重构主动「单路径化」。**

| 阶段 | 事实 |
|------|------|
| **v0.0.8–v0.0.11** | 边缘 H.264 实验期；帧对齐差（~87–96%）、GOP 被破坏（最近邻子采样）、USB 队列过小 |
| **v0.0.12** | 修复后 **100% parity**（1417 rows = 1417×4 MP4 @ 1280×800）；生产签收见 `STABLE-v0.0.12` |
| **v0.0.13** | Phase0 冻结 **130 契约 = JPEG DLB1 only**；Phase7 **强制删除** `H264` / `segment_mp4` / `SEGMENT_H264` / MCAP 写入（技术债清零清单 §13） |
| **v0.1.x** | 新 ingest/derive 模块围绕 `frames/*.bin` + tar.zst 重建；`record_oak_stream` 硬编码 `OAK_H264=0` |

**关停的三类动机（文档可追溯）：**

1. **工程简化：** 双编解码路径（H.264 MP4 + JPEG bin）使 ingest/derive 状态机、门禁、测试矩阵翻倍；重构目标是一刀切到 DLB1。
2. **derive 收益有限：** 即使边缘出 H.264 MP4，v0.0.12 时代 34 仍走 JPEG staging → libx264，**derive 瓶颈未消除**。
3. **运维契约统一：** Phase0 将 `OAK_H264=0` 写入不可变 130 输入契约，provision/verify 脚本强制检查。

因此：**v0.0.12 的 H.264 是「验证成功后被战略放弃」**，不是「验证失败被回退」。

### 14.2 130 隔离 POC（2026-08-27）复现了什么？

在 `10.10.10.130:/tmp/ego-h264-poc` 用 v0.0.12 快照叠加现网依赖，**未改工作区代码**：

| 项 | 结果 |
|----|------|
| VPU 4 路 H.264 @ ~27fps | ✅ 可行，`dropped=0` |
| GPD 软编码 CPU | ✅ 非瓶颈（编码在 VPU） |
| USB | ✅ 无错误 |
| `segment_frame_parity_ok`（copy mux） | ✅ seg_000002，1235 帧 |
| 4 路 ffprobe = rows | ⚠️ rear_right **1029/1235** |
| 段关闭阻塞 | ❌ `persist_q` 最高 **1229** |
| seg_000001 rollover | ❌ front_right trim 走 **libx264** 失败 |
| 功耗 | ⚠️ 无 in-run 采样 |

**含义：** VPU 硬件前提成立；**v0.0.12 代码直接叠到 v0.1.3 栈上仍不能生产启用**，段关闭与 rollover 需专项改造。

### 14.3 历史坑 vs 新规划避坑矩阵

| 历史问题 | 根因（版本） | v0.2 规避策略 |
|----------|-------------|---------------|
| GOP 破坏、帧对齐 ~87% | v0.0.11 最近邻子采样 | Track2 强制 `OAK_H264_SEQUENTIAL=1` + FIFO 消费 |
| USB 丢包 / 队列溢出 | v0.0.11 `maxSize=8` | `OAK_CAM_QUEUE_MAX=32`；MCAP writer 异步 flush |
| 1920×1200 污染 concat | v0.0.11 | 分辨率探针 + fail-fast（v0.0.12 已有，Track2 保留） |
| 段尾 ffmpeg 阻塞采集 | v0.0.12 同步 mux；POC 复现 | **P2b：段关闭异步化**；禁止采集线程 await mux |
| rollover libx264 trim | POC seg_000001 | **copy-only mux**；删除 trim 重编码分支 |
| rear_right 帧洞 | POC；`decode_slice_header` | parity 门禁 **4 路 ffprobe**；未达标不上线 |
| derive 仍慢 | v0.0.12 34 仍 x264 | Track2 **P4b remux-only**；Track1 不承诺加速 |
| 删 H264 无替代 | v0.0.13 技术债清零 | Track2 在 MCAP 上 **重新引入**，不恢复 tar.zst+MP4 双格式 |
| MCAP 再删再建 | v0.0.11 删、v0.0.13 删 | 新模块 `mcap_segment_writer.py` 独立实现，不恢复旧 viewer |
| 不能 env 一键开启 | v0.1.3 `SEGMENT_FRAME_BIN` 硬要求 | Track2 需代码分支 + provision profile，非 drop-in |

### 14.4 v0.2 决策摘要（评审前必读）

```text
可以开工：  Track 1（MCAP + JPEG）—— PI 容器对齐，风险可控
暂缓上线：  Track 2（MCAP + VPU H.264）—— 硬件 OK，软件 Gate 未过
不能假设：  「VPU 可行」= 障碍全清；「换 MCAP」= derive 变快
推荐顺序：  P0 设计冻结 → Track1 P1–P4 → Track2 P2b–P4b（POC Gate 后）
```

---

## 15. 测试支路执行规划（v0.3 草案 · **待确认后实施**）

> **状态：** 待评审签字 · **确认前不修改任何业务代码、不部署生产**  
> **对齐目标：** Physical Intelligence —— **采集端 MCAP，训练前在 34 转 LeRobot**；Foxglove 可观测 +（Track 2）derive 加速  
> **隔离原则：** 主线 `deploy-release` / `ego-001` / `tar.zst` **零行为变更**

### 15.1 北极星（不变）

```text
[130 测试支路]  OAK → McapSegmentWriter → sess_*__seg_*.mcap[.zst]
       ↓  ego-upload（station=ego-mcap-pilot，UPLOAD_PROTOCOL=mcap）
[34 测试 overlay]  ingest → raw/.../seg_*.mcap → derive → LeRobot v3
       ↓  ego-process（仅 pilot station）
[交付]  Collection 预览 + egodome convert（与现网相同下游）

主线并行（不受影响）：
[130 ego-001]  tar.zst + DLB1 + OAK_H264=0  →  [34 v0.1.3]  现网 derive
```

**PI 对齐点：** 边缘只产 **标准 MCAP**（多 topic）；LeRobot v3 **只在 34 生成**。  
**Foxglove 对齐点：** MCAP 可用 Studio 回放；状态机 / raw-derived 隔离沿用 v0.0.13 范式。  
**Track 2 附加：** 边缘 `CompressedVideo`（VPU H.264）+ derive **remux**（跳过 x264）。

### 15.2 硬性隔离清单（实施时必须遵守）

| 层 | 主线（禁止动） | 测试支路（仅此处改） |
|----|---------------|---------------------|
| **Git** | `deploy-release` 仅接收 Phase 4 评审后合并 | 全部开发在 `feat/ego-mcap-pi` 及子分支 |
| **130 systemd** | `z-production-egoverse.conf` 不变 | 新增 `z-mcap-pilot.conf`（`systemd drop-in` 叠加，可一键 disable） |
| **130 采集服务** | `ecs-record-oak-stream` 生产实例常开 | pilot 用 **独立 checkpoint / segment_root**（`/tmp/ego-mcap-pilot` 或 `~/cache/ego-mcap-pilot`） |
| **130 上传** | `ego-upload ego-001`、`UPLOAD_PROTOCOL=tarzst` | `ego-upload ego-mcap-pilot`、`UPLOAD_PROTOCOL=mcap` |
| **34 镜像** | `data-lab-lerobot-studio:v0.1.3` | `v0.1.4-mcap-rc` + **独立 compose overlay** |
| **34 存储** | `data-storage/stream/ego-001/` | `data-storage/stream/ego-mcap-pilot/`（或 `ego-lab-01` 专用子树） |
| **34 服务** | 现网 `stream-ingest` / `derive-worker` 环境变量不变 | overlay 仅 pilot 容器读新代码；或 derive 按 `sourceFormat` 分支（默认仍 tarzst） |
| **代码默认值** | 所有新 flag **默认关**：`SEGMENT_MCAP=0`、`UPLOAD_PROTOCOL=tarzst` | 仅 pilot profile / overlay 显式开启 |
| **RC / provision** | `rc-ego-001-production.sh`、`ego-130-verify-production.sh` 不变 | 新增 `rc-ego-mcap-pilot.sh`、`ego-130-verify-mcap-pilot.sh` |

**禁止操作：** 在测试分支合入前修改 `z-production-egoverse.conf`；在 ego-001 路径试跑 MCAP；`docker-compose` 热替换现网 v0.1.3 镜像 tag。

### 15.3 Git 与分支策略

```text
deploy-release          ← 生产主线，Phase 4 前只读
  └── feat/ego-mcap-pi  ← 长期集成分支（从 deploy-release 拉出）
        ├── feat/mcap-p0-schema
        ├── feat/mcap-p1-edge-writer
        ├── feat/mcap-p2-ingest
        ├── feat/mcap-p3-derive
        └── feat/mcap-p2b-h264-remux   ← Track 2，POC Gate 后
```

| 规则 | 说明 |
|------|------|
| 合入目标 | Phase 0–3 仅合入 `feat/ego-mcap-pi`，**不合入 `deploy-release`** |
| PR 要求 | 每个 Phase 独立 PR；CI 必须 **tar.zst 回归全绿**（证明未破坏默认路径） |
| 标签 | pilot 镜像 `v0.1.4-mcap-rc.{phase}`；正式版 `v0.1.4-mcap` 待 Phase 4 |
| 回滚 | 删 drop-in / 停 pilot overlay → 主线零变更 |

### 15.4 试点站点与路径（建议 D3/D11）

| 项 | 建议值 | 说明 |
|----|--------|------|
| **pilot station_id** | `ego-mcap-pilot` | 新建 registry 条目，与 `ego-001` 隔离；亦可复用 `ego-lab-01`（需确认 130 是否绑定） |
| **130 segment_root** | `~/cache/ego-mcap-pilot/segments` | 不与 `~/cache/ego-001` 混用 |
| **130 active_root** | `/tmp/ego-mcap-pilot-active` 或独立 shm | 避免占生产 tmpfs |
| **34 raw** | `data-storage/stream/ego-mcap-pilot/raw/segments/` | ingest 落盘 |
| **34 derived** | `.../ego-mcap-pilot/derived/<session_id>/` | unit layout（`unit.json` + parquet + 4×MP4 + `imu.parquet`） |
| **Collection URL** | `/collection?station=ego-mcap-pilot` | 验收入口 |

### 15.5 分 Phase 执行表（测试支路）

#### Phase 0 — 设计冻结（**仅文档 + fixture，零运行时变更**）

| 交付 | 位置 |
|------|------|
| MCAP schema 定稿（§3 + D1/D5/D8） | 本文档 + `fixtures/golden-seg.mcap` |
| HTTP 契约 `X-Upload-Protocol: mcap` | §7.1 |
| pilot station registry 草案 | `config/ego-pipeline-stations.yaml`（**新增条目**，不改 ego-001） |
| Foxglove 打开 golden fixture | 人工验收 |

**Gate：** 评审签字 → 才创建 `feat/ego-mcap-pi` 并开工 P1。

---

#### Track 1 — PI 容器主线（先完成）

| Phase | 分支 | 改动范围 | 验收（pilot only） |
|-------|------|----------|-------------------|
| **P1** | `feat/mcap-p1-edge-writer` | 130：`mcap_segment_writer.py`、`SEGMENT_MCAP=1` 分支、`z-mcap-pilot.conf` | 本地 3 段 MCAP；Foxglove 回放；**ego-001 服务未停** |
| **P2** | `feat/mcap-p2-ingest` | 34：`receive-mcap.mjs`、upload 扩展；pilot overlay 部署 | MCAP 入库 `raw/`；tar.zst ingest 测试全绿 |
| **P3** | `feat/mcap-p3-derive` | 34：`mcap-reader`、unit 分支 `sourceFormat=mcap` | 真实现场 `*.mcap.zst` → `session.READY`；unit layout 产出完整；**不含** Collection 预览（见 §15.10） |
| **P4** | `feat/mcap-p4-pilot-e2e` | ego-process-watcher 多站；session 发现；provision/deploy 脚本 | `ego-process ego-mcap-pilot` → Collection + egodome；7 天 §15.9 |

**Track 1 不承诺 derive 加速**（34 仍 JPEG→x264）；价值是 **PI 容器 + Foxglove 可观测**。

---

#### Track 2 — Foxglove 性能轨（POC Gate 后）

| Phase | 前置 Gate | 改动 | 验收 |
|-------|-----------|------|------|
| **P2b** | §14.2 POC 问题有修复方案 | 段关闭异步、copy-only、v0.0.12 调参合回 | 4 路 ffprobe = rows；`persist_q` 不飙升 |
| **P3b** | P2b 通过 | MCAP `CompressedVideo` topic | Foxglove 4 路 H.264 回放 |
| **P4b** | P3b 通过 | derive remux 分支 | derive 耗时 ↓50%+（对比 Track1 同 session） |

**Track 2 全部在 `feat/ego-mcap-pi` 上；未过 Gate 不开工。**

---

### 15.6 主线零影响验证（每 Phase 必跑）

```bash
# 34：现网回归（deploy-release 代码 + v0.1.3 镜像）
bash data-lab-platform/scripts/rc-ego-001-production.sh

# 130：生产契约（在 ego-001 上，非 pilot）
bash data-lab-platform/scripts/ego-130-verify-production.sh

# pilot 专项（新脚本，仅 MCAP 站）
bash data-lab-platform/scripts/rc-ego-mcap-pilot.sh   # Phase P2 起新增
```

任一 Phase 导致上述 **production 脚本失败 → 阻塞合入 `feat/ego-mcap-pi`**。

### 15.7 确认后首批代码变更（预览，**尚未执行**）

用户确认本节后，按序执行（仍在测试分支）：

1. 创建 `feat/ego-mcap-pi`（from `deploy-release`）
2. **P0：** 添加 `fixtures/mcap/golden-seg.mcap`、pilot station registry 条目、§15 相关脚本骨架（空实现 + TODO）
3. **P1：** `mcap_segment_writer.py` + `SEGMENT_MCAP` 分支（默认 0）+ `z-mcap-pilot.conf.example`
4. 部署 pilot overlay 到 34（**不** replace v0.1.3 现网 compose）

### 15.8 评审结论（已确认 · 2026-08-28）

| # | 决策 | 结论 |
|---|------|------|
| C1 | pilot 站名 | ✅ **`ego-mcap-pilot`**（新建，不复用 ego-lab-01） |
| C2 | 130 录制 | ✅ **独立 unit `ecs-record-oak-mcap-pilot`**；生产 `ecs-record-oak-stream` 不动 |
| C3 | 范围 | ✅ **先 Track 1 完整到 P4**；Track 2 单独里程碑 |
| C4 | 34 部署 | ✅ **独立 compose overlay** |
| C5 | 合入主线 | ✅ **pilot 连续 7 天达标** 后 PR → `deploy-release`（见 §15.9） |
| C6 | 上传压缩 | ✅ **`.mcap.zst`** |

#### 附加落地约束（强制执行）

1. **所有新增 env / flag 默认关闭**；仅 `ecs-record-oak-mcap-pilot` + `z-mcap-pilot.conf` 显式开启 MCAP 链路；**禁止**修改 `z-production-egoverse.conf`。
2. **Track 1 derive 保留现有 x264**，不引入编码改动；聚焦 MCAP 链路正确性 + Foxglove 回放。
3. **每轮迭代**必须跑 `rc-ego-001-production.sh` + `ego-130-verify-production.sh`；失败禁止进下一 Phase。
4. **P0 golden fixture**（`fixtures/mcap/golden-seg.mcap`）为回归基准；后续迭代须与该样本比对 topic / 帧数 / 时间单调性。

#### P4 七天稳定期验收指标（C5 补充）

| 指标 | 阈值 |
|------|------|
| pilot session `FAILED` 率 | 0（7 天内无 `session.FAILED`） |
| MCAP 段完整性 | 100% 段通过 `mcap-validator`（topic 齐全、时间单调） |
| derive READY 成功率 | 上传 session 100% 达 `session.READY` |
| Collection 可预览 | `/collection?station=ego-mcap-pilot` episode 数 = 上传 session 数 |
| 主线回归 | 每日 `rc-ego-001-production.sh` 全绿 |
| 130 生产采集 | `ego-001` 连续 7 天无掉帧告警（与 pilot 并行期间） |
| Foxglove 抽检 | 每日 ≥1 段 MCAP 可完整回放 4 路相机 |

**状态：** 已确认 → `feat/ego-mcap-pi` **P0 + P1 首批已落地**（见下表）。

| 交付物 | 路径 | Phase |
|--------|------|-------|
| golden MCAP fixture | `fixtures/mcap/golden-seg.mcap` + `.meta.json` | P0 |
| fixture 生成器 | `fixtures/mcap/generate_golden_fixture.py` | P0 |
| pilot station 注册 | `lerobot-studio/config/collection-*.json`、`config/ego-pipeline-stations.yaml` | P0 |
| pilot systemd unit | `ego-stream-client/systemd/ecs-record-oak-mcap-pilot.service` + `.d/z-mcap-pilot.conf` | P0 |
| RC / verify 骨架 | `scripts/rc-ego-mcap-pilot.sh`、`scripts/ego-130-verify-mcap-pilot.sh` | P0 |
| 34 pilot overlay 骨架 | `docker-compose.v0.1.4-mcap-pilot.yml`、`scripts/deploy-stream-ingest-v0.1.4-mcap-pilot.sh` | P0 |
| MCAP writer | `ego-stream-client/mcap_segment_writer.py` | P1 |
| segment_store 分支 | `SEGMENT_MCAP=0` 默认；`=1` 时写 `segment.mcap` | P1 |
| mcap.zst 打包 | `ego-stream-client/segment_mcap.py` | P1 |
| 单元测试 | `ego-stream-client/tests/test_mcap_segment_writer.py` | P1 |
| 依赖 | `requirements-edge.txt` → `mcap>=1.1.0` | P1 |

**P2 已落地（`7029e62` + 热修 `25ecf82`）：** ingest/upload mcap.zst 全链路；pilot overlay `:7863`。

| P2 交付物 | 状态 |
|-----------|------|
| `ingest/receive-mcap.mjs` + `mcap-validator.mjs` | ✅ |
| `scripts/validate-mcap-archive.py` | ✅ |
| `stream-ingest.mjs` `X-Upload-Protocol: mcap` / `tarzst` | ✅ |
| `segment_upload.py` `.mcap.zst` 上传 | ✅ |
| `docker-compose.v0.1.4-mcap-pilot.yml` `:7863` | ✅ |
| `ego-130-provision-mcap-pilot.sh` | ✅ |
| `ingest/ingest-mcap.test.mjs` | ✅ |
| 130 录段 + Foxglove / 现场 upload 验收 | ✅（ingest 链路已验证） |

**P3 已落地（`10b807d`）：** `mcap-reader` + `unit.mjs` `sourceFormat=mcap` 分支 + IMU 适配 + 单元测试。

| P3 交付物 | 状态 |
|-----------|------|
| `derive/mcap-reader.mjs` + `mcap-materialize.py` | ✅ |
| `derive/unit.mjs` mcap 分支（DLB1 路径保留） | ✅ |
| `derive/imu/ingest-raw.py` `--imu-mcap` | ✅ |
| `mcap-reader.test.mjs` + `unit-mcap.test.mjs` | ✅ |
| P3 现场 derive 验收 | ⏳ 见 §15.10 |

**下一步（P4）：** ego-process → Collection 预览 → egodome convert；7 天 pilot 指标（§15.9）。

### 15.10 P3 现场验收（derive 链路 · pilot overlay）

> **分支：** `feat/ego-mcap-pi` @ `10b807d`  
> **Scope：** 仅 34 derive-worker + `mcap-reader`；**不进入** ego-process / Collection / egodome。  
> **前置：** pilot overlay `:7863` 已部署；`raw/segments/*/*.mcap.zst` 已有 P2 现场样本；`rc-ego-001-production.sh` fail=0。

#### 易误判点（必读）

| # | 正确理解 | 常见误判 |
|---|----------|----------|
| 1 | **Session** 流转：`DONE_UPLOAD` → `DERIVING` → `READY` | 期待 segment 出现 `DERIVING` / `READY` |
| 2 | **Segment** 终态：`DERIVE_PENDING`（含 `sourceFormat: mcap`）全程不变 | 认为 segment 卡在 `DERIVE_PENDING` 是故障 |
| 3 | 产出在 `derived/<session_id>/`（unit layout） | 查找 `derived/sessions/.../segments/...` |
| 4 | IMU：`sensor_raw/imu/...` + unit 内 `imu.parquet` | 查找 `high_freq/imu_200hz.parquet` |

#### 预置环境变量（34 主机）

```bash
export DATALAB_ROOT=/path/to/data-lab
export PILOT_DERIVE_CTR="${PILOT_DERIVE_CTR:-data-lab-derive-worker-mcap-pilot-1}"
export PILOT_INGEST_CTR="${PILOT_INGEST_CTR:-data-lab-stream-ingest-mcap-pilot-1}"
export STATION=ego-mcap-pilot
export STREAM_ROOT="${DATALAB_ROOT}/data-storage/stream/${STATION}"
```

#### 一、derive-worker 健康检查

```bash
# 1. 容器 Up + 镜像标签
docker ps --filter "name=derive-worker-mcap-pilot" \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

# 2. PID 1 = ego-derive-watch.mjs
docker exec "${PILOT_DERIVE_CTR}" sh -c "tr '\0' ' ' < /proc/1/cmdline; echo"

# 3. 启动 banner
docker logs "${PILOT_DERIVE_CTR}" 2>&1 | head -20
# 预期：ego-derive-worker-entry → ego-derive-watch → derive_watch_started

# 4. 环境变量
docker exec "${PILOT_DERIVE_CTR}" printenv | grep -E '^(STATION_ID|STREAM_DATA_ROOT|DERIVE_STANDALONE)='

# 5. 轮询（无 pending → derive_watch_skip；有 pending → derive_watch_kick）
docker logs "${PILOT_DERIVE_CTR}" --since 30s 2>&1 | tail -20

# 6. API（pilot 端口 7863，非生产 7862）
curl -s "http://10.10.10.34:7863/lerobot/api/collection/stations/${STATION}/derive-status" | jq .
```

#### 二、日志查看

```bash
docker logs -f "${PILOT_DERIVE_CTR}" 2>&1
docker logs -f "${PILOT_DERIVE_CTR}" 2>&1 | grep -E 'derive_watch_|derive_unit_|mcap|FAILED|materialize'
ls -la "${STREAM_ROOT}/state/sessions/<SESSION_ID>/"
tail -20 "${STREAM_ROOT}/manifest/journal.jsonl"
```

| 日志关键字 | 含义 |
|------------|------|
| `derive_watch_started` | watch 循环启动 |
| `derive_watch_kick` | 拾取待派生 session |
| `derive_unit_pipeline_start` | unit derive 开始 |
| `derive_unit_ready` | derive 成功，gate 通过 |
| `derive_unit_failed` | 失败，查 reason |
| `mcap summary failed` / `mcap materialize failed` | mcap-reader 异常 |

#### 三、mcap-reader 五层校验

mcap-reader **延迟加载**，仅在处理 `sourceFormat=mcap` 时导入。

```bash
# L1 镜像文件
docker exec "${PILOT_DERIVE_CTR}" ls -la /app/derive/mcap-reader.mjs /app/derive/mcap-materialize.py

# L2 Node import
docker exec "${PILOT_DERIVE_CTR}" node --input-type=module -e \
  "import '/app/derive/mcap-reader.mjs'; console.log('OK')"

# L3 Python 依赖
docker exec "${PILOT_DERIVE_CTR}" python3 -c "from mcap.reader import make_reader; print('OK')"

# L4 现场样本 summary
SAMPLE=$(find "${STREAM_ROOT}/raw/segments" -name '*.mcap.zst' | head -1)
docker exec "${PILOT_DERIVE_CTR}" python3 /app/derive/mcap-materialize.py "$SAMPLE" --summary-only

# L5 手动触发 derive（优先复用 P2 已上传样本）
STREAM_INGEST_CONTAINER="${PILOT_INGEST_CTR}" STATION_ID="${STATION}" \
bash data-lab-platform/scripts/ego-derive run \
  --station "${STATION}" --session "<SESSION_ID>" --json | tee /tmp/p3-derive.json
```

#### 四、产出物与通过判定

```bash
SESSION_ID="<SESSION_ID>"
UNIT="${STREAM_ROOT}/derived/${SESSION_ID}"
test -f "${UNIT}/unit.json"
jq -e '.derive.source_format == "mcap"' "${UNIT}/unit.json"
test -f "${UNIT}/data.parquet" && test -f "${UNIT}/imu.parquet"
test "$(ls "${UNIT}/videos/"*.mp4 2>/dev/null | wc -l)" -eq 4
test -f "${STREAM_ROOT}/state/sessions/${SESSION_ID}/session.READY"
! test -f "${STREAM_ROOT}/state/sessions/${SESSION_ID}/session.FAILED"
```

**P3 通过：** worker 健康 + mcap-reader 五层 OK + 现场样本 `session.READY` + unit 产出完整 + `rc-ego-001-production.sh` fail=0。  
**不阻塞：** 130 persist 队列 2048 背压（独立 backlog）。  
**P4 已落地（工作区）：** ego-process-watcher 多站、`ego-pipeline-sessions` unit/MCAP 发现、`ego-station-runtime.sh`、`deploy-stream-ingest-v0.1.4-mcap.sh`、provision/verify profile、手册 §九。

#### P1 调优 backlog（不阻塞 P4）

| 项 | 说明 |
|----|------|
| JPEG payload 有效性 | `mcap_segment_writer` 写入须为可解码 JPEG；P2 现场样本曾出现 9B 占位导致 derive mux 失败 |
| persist 队列 2048 | 130 背压饱和、段卡 RECORDING；独立 pilot 调优 |

**P3 现场签收：** `sess_p3_accept_golden` → `session.READY`（2026-08-29）；P2 样本 JPEG 问题记入上表。

### 15.11 P4 现场验收（ego-process → Collection → egodome）

1. `deploy-stream-ingest-v0.1.4-mcap.sh` — pilot `:7863` Up，`:7862` 生产不变
2. `ego-pipeline-sessions.py all-sessions` / `source-format` — READY session 且 `mcap`
3. `ego-process ego-mcap-pilot --skip-derive` — convert + Viewer sync
4. Collection `/collection?station=ego-mcap-pilot` — episode 数 = READY 数，四路 MP4 可播
5. egodome `/data/ego_mcap_pilot` — episode 与 Collection 一致；`samples/ego_mcap_pilot.zip`
6. Watcher `EGO_PROCESS_WATCH_STATIONS=ego-001,ego-mcap-pilot`；`ego-upload --notify` 触发后处理
7. `rc-ego-001-production.sh` + `rc-ego-mcap-pilot.sh` 全绿
8. **7 天试点** §15.9 — 达标后 PR → `deploy-release`

---

*文档版本：v0.4 · P3 现场签收；P4 代码就绪；§15.10 P3 / §15.11 P4 现场验收*
