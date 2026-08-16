# v0.0.8 — tar.zst → jsonl + 段内 MP4 → READY

## 数据契约（不可破坏）

| 项 | 要求 |
|----|------|
| tar.zst 成员 | `manifest.json`、`rows.jsonl`、`imu_raw.jsonl`；H264 生产路径 **`streams/*.mp4` 必须**；`frames/*.bin` 仅 legacy/staging_mux |
| 段内 MP4 | `streams/*.mp4` 为预览/QC 快路径，**不替代** raw 归档 |
| 后处理 | `ego-hand-pipeline` / rectify **仍在 READY 门禁之后**，接口不变 |
| LeRobot v3 | `meta/` + `data/*.parquet` + `videos/*.mp4` 三件套不变 |

## 唯一主路径

```
130: 采集关段 → SEGMENT_H264 mux → pack tar.zst (bin + rows + imu + streams/*.mp4)
     → upload_segments (tar.zst only)

34:  ingest → jsonl append + MP4 shard concat → parquet sync → READY
```

**已降级/关闭（v0.0.8 compose）：**

- `STREAM_FRAME_PUSH=0` — 禁止逐帧 multipart
- `DERIVE_ASYNC=0` — 禁止双调度 derive-async
- `STREAM_PRIMARY_PATH=segment_mp4` — 禁止 _staging JPEG 主路径 mux
- `STREAM_ARCHIVE_PURGE_MAX=0` + `STREAM_RAW_RETAIN_UNTIL_READY=1` — raw tar.zst 保留至后处理

**遗留回退（v0.0.8）：** 无 `streams/*.mp4` 的旧段仍从 `frames/*.bin` 解码 → staging mux。**v0.0.9+ 已移除该回退**，见 [RELEASE-v0.0.9.md](RELEASE-v0.0.9.md)。

## 部署（34）

```bash
docker build -t data-lab-lerobot-studio:v0.0.8 data-lab-platform/lerobot-studio

docker-compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  -f data-lab-platform/docker-compose.v0.0.8.yml \
  up -d stream-ingest
```

## 部署（130 采集端）

```bash
# 关段 mux 依赖 ffmpeg（缺省会 segment close 失败、persist 队列堆积）
sudo apt-get install -y ffmpeg

# 启用段内 H264 + tar.zst 上传
sudo cp data-lab-platform/ego-stream-client/systemd/ecs-record-oak-stream.service.d/v0.0.8-segment-mp4.conf \
  /etc/systemd/system/ecs-record-oak-stream.service.d/
systemctl --user daemon-reload && systemctl --user restart ecs-record-oak-stream
```

上传仅用：`python -m ego_capture_studio.cli.upload_segments`（`ego_upload` 已废弃）。

### 上传策略（生产 vs 调试）

| 环境 | 命令 |
|------|------|
| **生产** | 仅手动 `upload_segments`；`bash data-lab-platform/scripts/ego-130-upload-mode.sh production` |
| **RC/调试** | 可 `ego-130-upload-mode.sh debug` 启用 `upload_segments_loop` |

详见 [docs/ego-130-upload-policy.md](docs/ego-130-upload-policy.md)。

## 验收

```bash
data-lab-platform/scripts/rc-acceptance.sh
data-lab-platform/scripts/rc-e2e-upload.sh   # 需 130 已开 SEGMENT_H264
```

## 回滚

```bash
# 切回 v0.0.7-rc compose + 镜像；130 去掉 v0.0.8-segment-mp4.conf
STREAM_PRIMARY_PATH=staging_mux docker-compose ... -f docker-compose.v0.0.7-rc.yml up -d stream-ingest
```
