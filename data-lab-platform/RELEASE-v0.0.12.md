# v0.0.12 — H264 100% 帧对齐稳定版（ego-001 生产签收）

基于 v0.0.11。130 采集 + 34 ingest 全链路帧对齐；rows.jsonl 与 4 路 MP4 可解码帧 **100% 一致**。

## 变更摘要

| 项 | v0.0.11 | v0.0.12 |
|----|---------|---------|
| H264 ring 消费 | 最近邻子采样（破坏 GOP） | **FIFO 顺序消费**（`OAK_H264_SEQUENTIAL=1`） |
| USB 队列 | `maxSize=8` | **`OAK_CAM_QUEUE_MAX=32`** |
| segment 关段 | 容忍 ~85% 帧差 | **`enforce_segment_frame_parity`** 裁至 min(行, 可解码包) |
| ingest 门禁 | 宽松 ±1 / 85% | **严格 parity** + 分辨率不匹配 fail-fast |
| 1920×1200 污染 | concat 静默 reencode | **分辨率探针 + 删旧 MP4 重拷** |
| high_freq corpus | `already_appended` 跳过 | **`merge_high_freq_from_stream`** 补合并 |
| 可解码率 | ~87–96% | **100%**（1417 rows = 1417×4 MP4 @ 1280×800） |

## 130 采集（ego-stream-client）

```bash
data-lab-platform/scripts/ego-130-provision.sh server@10.10.10.130 ego-001
```

关键 env（`v0.0.8-segment-mp4.conf`）：

- `OAK_H264_SEQUENTIAL=1`
- `OAK_CAM_QUEUE_MAX=32`
- `OAK_H264_BITRATE_KBPS=6000`
- `OAK_H264_KEYFRAME_FREQUENCY=30`
- `SEGMENT_H264_MUX_MODE=copy`

## 34 平台

沿用 v0.0.11 镜像；ingest 逻辑在本版 git 中更新，需 **rebuild stream-ingest**：

```bash
docker build -t data-lab-lerobot-studio:v0.0.12 data-lab-platform/lerobot-studio
# 或复用 v0.0.11 镜像 tag 并 rebuild（代码以 git 为准）
```

## 验收

```bash
# 130 采集 ~1min 后
RC_STATION=ego-001 RC_SESSION=<sess_...> data-lab-platform/scripts/rc-e2e-upload.sh
EGO_PIPELINE_BACKEND=oak EGO_OAK_MODE=hands data-lab-platform/scripts/ego-run-pipeline ego-001
```

期望：`segment_frame_parity_ok` · `mp4Ok=true` · 4 路 **1280×800** · ffmpeg 全帧 decode 0 error。

## 版本配对

| 组件 | 版本 |
|------|------|
| data-lab git | `v0.0.12` |
| ego-platform git | `v0.0.12`（兄弟目录快照） |
| 130 systemd drop-in | `v0.0.8-segment-mp4.conf` |
| 34 stream-ingest | `data-lab-lerobot-studio:v0.0.11+`（rebuild） |

详见 [docs/STABLE-v0.0.12.md](docs/STABLE-v0.0.12.md)。
