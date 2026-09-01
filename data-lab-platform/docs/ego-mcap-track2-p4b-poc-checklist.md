# EGO MCAP Track 2 — P4b VPU-H.264 POC 开发清单

> **分支：** `feat/ego-mcap-track2-vpuh264`（自 `11d7653` 拉出）  
> **并行约束：** **不改动** `feat/ego-mcap-pi`、不中断 Track 1 七天试点（`ego-mcap-pilot` / G1–G8）  
> **参考：** `ego-mcap-pi-migration-plan.md` §5.2、§14.2–§14.3、§15.9.4  
> **编解码：** 仅 VPU **H.264**；代码库无 H.265/HEVC 路径

---

## 0. 双轨隔离（硬性）

| 层 | Track 1 试点（**禁止动**） | Track 2 POC（本分支） |
|----|---------------------------|----------------------|
| Git 分支 | `feat/ego-mcap-pi` | `feat/ego-mcap-track2-vpuh264` |
| 130 systemd | `ecs-record-oak-mcap-pilot` + `z-mcap-pilot.conf` | `ecs-record-oak-mcap-track2` + `z-mcap-track2-h264.conf` |
| 130 编码 | `OAK_HW_JPEG=1` `OAK_H264=0` | `OAK_HW_JPEG=0` `OAK_H264=1` |
| station_id | `ego-mcap-pilot` | `ego-mcap-track2`（建议） |
| segment_root | `~/cache/ego-mcap-pilot/segments` | `~/cache/ego-mcap-track2/segments` |
| 34 ingest | `:7863` pilot overlay | `:7864` track2 overlay（建议，与 pilot 硬隔离） |
| 34 raw/derived | `.../ego-mcap-pilot/` | `.../ego-mcap-track2/` |
| 生产 | `ego-001` + `z-production-egoverse.conf` | **禁止触碰** |

**互斥规则：** 130 上 `ecs-record-oak-mcap-pilot` 与 `ecs-record-oak-mcap-track2` **不可同时 enable**（`Conflicts=` 同 `ecs-record-oak-stream`）。Track 2 录制前须 `systemctl --user stop ecs-record-oak-mcap-pilot`（试点日录完再开 Track 2，或反之）。

---

## 1. POC Round 1 目标（首轮签收）

| # | 目标 | 验收 |
|---|------|------|
| R1 | 130：4 路 1280×800 VPU H.264 写入 MCAP | `foxglove.CompressedVideo` topic 齐全；Foxglove Studio 4 路可回放 |
| R2 | 对比 Track 1 HW-JPEG 基线 | 功耗、码率、MCAP 体积、本地可存时长（见观测模板） |
| R3 | 34 derive：H.264 remux → MP4 | 跳过 `MUX_ENCODE` libx264；`derive_total_s` 较同帧数 JPEG 基线 ↓50%+（目标） |
| R4 | POC Gate 四类卡点 | GOP 边界、rollover、persist 队列、帧索引/parquet 对齐（§3） |

---

## 2. 工作包分解

### 2.1 P2b — 130 采集稳定性（合回 v0.0.12 调参 + 段关闭改造）

> 历史 POC（§14.2）硬件可行，软件卡点：persist_q 飙升、rollover libx264 trim、rear_right 帧不齐。

| 任务 | 文件 / 模块 | 状态 | 说明 |
|------|------------|------|------|
| T2-01 | `systemd/.../z-mcap-track2-h264.conf` | 🟡 骨架 | `OAK_HW_JPEG=0` `OAK_H264=1` `SEGMENT_MCAP=1`；合回 v0.0.12 调参 |
| T2-02 | `ecs-record-oak-mcap-track2.service` | 🟡 骨架 | 独立 unit，不读 `ego-station.env` |
| T2-03 | `oak_4p_capture.py` | ⬜ | 确认 4 路 `H264_MAIN` @ 1280×800；`OAK_H264_BITRATE_KBPS` 默认对齐 POC |
| T2-04 | `record_oak_stream.py` | ✅ | Track2 profile 放宽 JPEG-only 硬门禁（`EGO_CAPTURE_JPEG_ONLY=0` + `SEGMENT_MCAP=1`） |
| T2-05 | `segment_store.py` | ⬜ | 段关闭 **异步 persist**；采集线程禁止 await mux/trim |
| T2-06 | rollover / 段尾 mux | ⬜ | **copy-only**（`SEGMENT_H264_MUX_MODE=copy`）；删除 libx264 trim 分支 |
| T2-07 | FIFO 消费 | ✅ | `OAK_H264_SEQUENTIAL=1` 已在 `iter_strict_sync_frames` 实现 FIFO popleft |
| T2-08 | USB 队列 | ⬜ | `OAK_CAM_QUEUE_MAX=32` |
| T2-09 | 运行时观测 | ⬜ | 日志/指标输出 `persist_q`、`dropped`、段关闭耗时 |
| T2-10 | `scripts/ego-130-provision-mcap-track2.sh` | ⬜ | 部署 track2 drop-in + verify（**不改** pilot provision） |
| T2-11 | `scripts/ego-130-record-mcap-track2-poc.sh` | ⬜ | 一键录段 + 上传 `:7864`（对标 pilot SOP） |

**P2b 建议 env（`z-mcap-track2-h264.conf`）：**

```ini
Environment=OAK_HW_JPEG=0
Environment=OAK_H264=1
Environment=SEGMENT_H264=1
Environment=SEGMENT_MCAP=1
Environment=SEGMENT_FRAME_BIN=0
Environment=EGO_CAPTURE_JPEG_ONLY=0

Environment=OAK_H264_SEQUENTIAL=1
Environment=OAK_CAM_QUEUE_MAX=32
Environment=OAK_H264_BITRATE_KBPS=6000
Environment=OAK_H264_KEYFRAME_FREQUENCY=30
Environment=SEGMENT_H264_MUX_MODE=copy

Environment=EGO_STATION_ID=ego-mcap-track2
Environment=EGO_SEGMENT_ROOT=/home/server/cache/ego-mcap-track2/segments
Environment=EGO_SEGMENT_ACTIVE_ROOT=/tmp/ego-mcap-track2-active
Environment=EGO_CAPTURE_CHECKPOINT=/home/server/cache/ego-mcap-track2/checkpoint.json
```

**P2b Gate：** 连续 2 段录制；4 路 ffprobe 可解码帧数 = rows（目标 4/4）；`persist_q` 段关闭后 < 64 且在 30s 内回落到 0。

---

### 2.2 P3b — MCAP `CompressedVideo` schema + Foxglove

| 任务 | 文件 / 模块 | 状态 | 说明 |
|------|------------|------|------|
| T2-20 | `mcap_segment_writer.py` | ✅ | H.264 分支：`foxglove.CompressedVideo` + `session_meta.video_codec=h264` |
| T2-21 | topic schema | ⬜ | 4 路 `/ego/camera/{role}` 或独立 `/ego/camera/{role}/h264`；`session_meta.video_codec=h264` |
| T2-22 | `fixtures/mcap/` | ⬜ | golden H.264 MCAP fixture + validator |
| T2-23 | Foxglove 人工验收 | ⬜ | Studio 打开 POC 段，4 路同步回放无花屏 |
| T2-24 | `validate-mcap-archive.py` | ⬜ | H.264 topic 计数、`frame_count` 推断（对标 JPEG 路径） |

**P3b Gate：** Foxglove 4 路 H.264 回放 OK；`mcap-validator` 通过；topic schema 版本写入 `session_meta`。

---

### 2.3 P4b — 34 derive remux（跳过 libx264）

| 任务 | 文件 / 模块 | 状态 | 说明 |
|------|------------|------|------|
| T2-30 | `derive/mcap-materialize.py` | ✅ | 读 `CompressedVideo` → `streams/{cam}.h264` |
| T2-31 | `derive/mcap-reader.mjs` | ✅ | `summarizeMcapArchive` 返回 `video_codec=h264` |
| T2-32 | `derive/unit.mjs` | ✅ | `video_codec=h264` 分支 remux，不调用 `encodeFramesToMp4` |
| T2-33 | `mux-exec.mjs` | ✅ | `remuxH264AnnexBToMp4` ffmpeg `-c copy` |
| T2-34 | `derive/progress.mjs` | ✅ | H.264 路径 phase `MUX_REMUX` |
| T2-35 | ingest / registry | ✅ | `ego-mcap-track2` + `:7864` overlay |
| T2-36 | `unit.json` | ✅ | `derive.video_codec=h264` `derive.mux_mode=remux` |
| T2-37 | 对比基准 | ✅ | sess_5a86 derive 57.5s@1585帧；归一化1042≈37.8s vs T1 47s；4×remux ~200ms |
| T2-38 | `rc-ego-mcap-track2.sh` | ✅ | Track2 专用 RC |

**P4b Gate：** derive 跳过 `MUX_ENCODE`；`derive_total_s` 较 Track1 同量级 session ↓50%+；`session.READY` + 4×MP4 ffprobe 帧数 = parquet rows。

---

### 2.4 观测与对比（Round 1 并行）

| 任务 | 说明 |
|------|------|
| T2-40 | 填写 `ego-mcap-track2-130-observation-template.md` 每次录制一行 |
| T2-41 | Track1 基线引用：`sess_b4bc`（1042 帧，derive ~47s）+ 试点每日 O1/O2 |
| T2-42 | 130 功耗：`powertop` / `rapl` / GPD 外接功率计（若有）录前录中录后各 1 样本 |
| T2-43 | 码率：MCAP 内 H.264 载荷字节 ÷ 时长 ÷ 4 路 |
| T2-44 | 存储：段 `.mcap.zst` 体积 ÷ 帧数 → MB/千帧；推算 `EGO_SEGMENT_ROOT` 可存小时数 |

---

## 3. POC Gate 验收矩阵（§14.2 复现项 → Round 1 必核）

| Gate ID | 历史 POC 问题 | 核验方法 | Round 1 通过标准 |
|---------|--------------|----------|------------------|
| **G-T2-01** | GOP 破坏 / 帧对齐 ~87% | `OAK_H264_SEQUENTIAL=1` + rows vs ffprobe | 4/4 路 **100%** parity（1280×800） |
| **G-T2-02** | persist_q 飙升（曾 1229） | 录段日志 `persist_q=`；段关闭后 30s 采样 | 段关闭峰值 < 256；30s 内 → 0 |
| **G-T2-03** | rollover libx264 trim 失败 | 触发 `EGO_SEGMENT_MAX_FRAMES` rollover，检查 mux 日志 | **无** libx264 调用；copy-only 成功 |
| **G-T2-04** | rear_right 1029/1235 | 每路 `ffprobe -count_frames` vs `rows.jsonl` | 各路差值 = 0 |
| **G-T2-05** | parquet 索引间隙 | derive 后 `PARQUET_INDEX_GAP` / unit fsck | 0 gap；`global_index` 连续 |
| **G-T2-06** | 2h 连续录 | 可选 Round 2；首轮至少 2×35s + 1×rollover | 无掉帧告警；温升记录入观测表 |
| **G-T2-07** | derive 性能 | O1 `derive_total_s`、无 `MUX_ENCODE` phase | remux 路径；较 Track1 ↓50%+（同帧数档） |

**不上线条件（任一命中 → hold，继续 P2b）：** 与 `ego-mcap-pi-migration-plan.md` §5.2 一致。

---

## 4. 首轮执行顺序（建议）

```text
Week POC-R1
├── D1  合入 z-mcap-track2-h264.conf + provision/verify；130 单段 H.264 MCAP 落盘
├── D2  mcap_segment_writer CompressedVideo；Foxglove 回放签收
├── D3  P2b：2 段连续 + persist_q / ffprobe parity 采证
├── D4  P4b：mcap-materialize + unit.mjs remux 分支；34 `:7864` 单 session derive
├── D5  rollover 专项 + POC Gate 表填满；观测模板对比 Track1
└── D6  评审：POC Gate 是否放行合入 feat/ego-mcap-pi（仍不等同生产上线）
```

**130 录制 SOP（Track 2，与 pilot 互斥）：**

```bash
# 停 pilot（若曾 enable）
systemctl --user stop ecs-record-oak-mcap-pilot.service

# Track 2 录制（脚本待 T2-11）
bash data-lab-platform/scripts/ego-130-record-mcap-track2-poc.sh --notify

# 录完恢复 pilot（Track 1 试点日程）
systemctl --user start ecs-record-oak-mcap-pilot.service
```

**34 derive SOP（Track 2）：**

```bash
ego-process ego-mcap-track2    # overlay :7864 就绪后
# 记录 derive_total_s；确认 unitProgress 无 MUX_ENCODE
```

---

## 5. 与 Track 1 试点关系

| 项 | Track 1（`feat/ego-mcap-pi`） | Track 2（本分支） |
|----|------------------------------|-------------------|
| 七日 G1–G8 | **照常每日执行** | 不参与、不阻断 |
| O1/O2 观测 | pilot 每日记录 | Track2 单独记录，不入 pilot 表 |
| 合入时机 | G1–G8 达标 → `deploy-release` 评审 | POC Gate 通过 → PR 回 `feat/ego-mcap-pi`（**不自动进生产**） |
| derive SLA 触发 | 试点结束后若阻塞 → 加速 Track2 评审 | 本 POC 为评审输入 |

---

## 6. 里程碑

| 里程碑 | 交付 | 目标日期 |
|--------|------|----------|
| **M0** | 本清单 + 观测模板 + track2 systemd 骨架 | Round 1 D0 |
| **M1** | P2b 130 单段 H.264 MCAP + Foxglove | Round 1 D2 |
| **M2** | POC Gate G-T2-01..04 证据包 | Round 1 D5 |
| **M3** | P4b remux derive E2E + 耗时对比 | Round 1 D5 |
| **M4** | POC 评审 → 是否启动 Round 2（2h soak） | Track 1 试点结束后 |

---

*文档版本：v0.1 · 分支 `feat/ego-mcap-track2-vpuh264` @ `11d7653`*
