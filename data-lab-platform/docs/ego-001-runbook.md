# ego-001 交付手册（一页）

**原则：** 130 采集与 34 派生解耦 · 手动上传 · segment_mp4 单主路径 · READY 后 Viewer 可播

**版本配对：** 130 `v0.0.8-segment-mp4` + 34 `data-lab-lerobot-studio:v0.0.9.2`

---

## 架构

```text
130 采集站 (10.10.10.130)          34 平台 (10.10.10.34)
─────────────────────────          ────────────────────────
record_oak_stream                  stream-ingest (segment_mp4)
  → tar.zst (rows + 4×MP4)    →      jsonl + MP4 concat + parquet
upload_segments (手动)               derive-worker → session.READY
                                     Viewer /data/ego_001
```

| 项 | 值 |
|----|-----|
| Station | `ego-001` |
| Token | `dl-upload-ego-001-v1` |
| 段根 (130) | `/home/server/cache/ego-001/segments` |
| Stream (34) | `data-storage/stream/ego-001` |
| 拓扑 | `ego-standard`（4 路，含 `depth_left`） |

---

## 首次 / 升级部署

### 34（平台）

```bash
data-lab-platform/scripts/deploy-stream-ingest-v0.0.9.2.sh
RC_STATION=ego-001 data-lab-platform/scripts/rc-acceptance.sh
```

### 130（采集）

```bash
data-lab-platform/scripts/ego-130-provision.sh server@10.10.10.130 ego-001
data-lab-platform/scripts/ego-130-verify-h264.sh server@10.10.10.130
data-lab-platform/scripts/ego-130-upload-mode.sh production   # 在 130 上
```

验收：`SEGMENT_H264=1`、`SEGMENT_FRAME_BIN=0`、日志 `hw_h264=True storage_h264=1`。

---

## 日常操作

### 1. 开始采集（130）

```bash
systemctl --user start ecs-record-oak-stream
# 或手机 UI start（仅采集，不上传）
```

### 2. 停止采集

```bash
systemctl --user stop ecs-record-oak-stream
```

### 3. 手动上传（生产）

```bash
set -a; source ~/.config/ego-station.env 2>/dev/null || true; set +a
export PYTHONPATH=/home/server/workspace/ego-studio/src

/home/server/workspace/ego-studio/.venv/bin/python -m ego_capture_studio.cli.upload_segments \
  --ensure-session \
  --segment-root /home/server/cache/ego-001/segments \
  --upload-url http://10.10.10.34:8080/lerobot/api/collection/stations/ego-001/upload
# 调试可加: --limit 3
```

**禁止** 生产环境 enable `ecs-upload-segments-loop`（见 [ego-130-upload-policy.md](ego-130-upload-policy.md)）。

### 4. 查看派生状态（34）

```bash
STATION_ID=ego-001 data-lab-platform/scripts/ego-derive status --station ego-001 --json
```

期望：`phase=READY`，`mp4Ok=true`，`session.READY` 存在。

### 5. 手动触发 derive（若未自动完成）

```bash
STATION_ID=ego-001 data-lab-platform/scripts/ego-derive run --station ego-001 --session <sess_...>
```

### 6. Viewer 签收

| 页面 | 用途 | 期望 |
|------|------|------|
| `/collection?station=ego-001` | **采集回放**（实时/已上传段预览） | 3 路 RGB 可播、时间轴同步；**不含**后处理深度图 |
| 数据集浏览（`stream/ego-001` READY 后） | **LeRobot 派生结果** | 4 路 MP4（含 `camera_depth_left` = OAK 深度相机原始 H264，非深度图生成流水线产物） |

采集回放签收：前左 / 前右 / 后右可播，帧数与 session 一致。  
派生签收：`derive-status` → `phase=READY`，`parquetRows≈5995`，`mp4Ok=true`（21 段生产 session）。

<http://10.10.10.34:8080/collection?station=ego-001> — 采集状态  
数据集浏览 — `stream/ego-001` 对应 LeRobot 目录

---

## 验收脚本（34 上）

```bash
# 容器 + 主路径
RC_STATION=ego-001 data-lab-platform/scripts/rc-acceptance.sh

# 平台 CI 门禁（rc-acceptance + derive READY，无 130 SSH）
RC_STATION=ego-001 data-lab-platform/scripts/ci-ego-platform.sh

# H264 包数 vs jsonl 审计（≥85%，跨相机差 ≤120）
RC_STATION=ego-001 data-lab-platform/scripts/ego-130-h264-packet-audit.sh

# 130 上传 + READY（自动发现 session，默认 3 段）
RC_STATION=ego-001 RC_UPLOAD_LIMIT=3 data-lab-platform/scripts/rc-e2e-upload.sh

# 全 session（所有 pending 段，耗时长）
RC_STATION=ego-001 RC_UPLOAD_LIMIT=999 RC_DERIVE_TIMEOUT_S=900 data-lab-platform/scripts/rc-e2e-upload.sh
```

---

## 排障

| 现象 | 检查 |
|------|------|
| 无 `streams/*.mp4` | 130：`ego-130-verify-h264.sh`；是否 `z-production-egoverse.conf` 覆盖 |
| ingest 仅 ~300 帧/路 | 34 镜像是否 `v0.0.9.2`；`STREAM_SEGMENT_INGEST_BATCH_SIZE=1` |
| `rear_left` 在 info.json | `session_start` 拓扑；`station-topology.json` ego-001→ego-standard |
| `phase=UPLOADED` 卡死 | `ego-derive run`；查 `session.FAILED`；`mux_validated.json` |
| MP4 帧数 < jsonl | 已知 H264 包数偏差；v0.0.9.2 容忍 ≥85% |
| 改代码不生效 | `docker cp` 后必须 `docker restart stream-ingest`；生产用镜像 |
| 8080 上传 502 | 重建 stream-ingest/lerobot 后 **restart nginx**（`docker restart data-lab-nginx-1`） |

```bash
# 日志
docker logs data-lab-stream-ingest-1 --since 10m | grep ego-001
journalctl --user -u ecs-record-oak-stream --since "1 hour ago" | tail -50
```

---

## 回滚

```bash
# 34 → v0.0.9
docker-compose ... -f data-lab-platform/docker-compose.v0.0.9.yml up -d stream-ingest derive-worker
```

---

## 相关文档

- [RELEASE-v0.0.9.2.md](../RELEASE-v0.0.9.2.md) — 变更与部署
- [ego-130-upload-policy.md](ego-130-upload-policy.md) — 上传策略
- [RELEASE-v0.0.8.md](../RELEASE-v0.0.8.md) — tar.zst 契约
