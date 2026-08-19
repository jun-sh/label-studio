# ego-001 交付手册（一页）

**原则：** 130 HW JPEG 采集 · 手动 tar.zst 上传 · 34 staging mux · `session.READY` 后 Viewer 可播

**版本：** 34 `data-lab-lerobot-studio:v0.0.11.2` · 130 `z-production-egoverse.conf`（`OAK_H264=0`）

---

## 架构

```text
130 采集站 (10.10.10.130)          34 平台 (10.10.10.34)
─────────────────────────          ────────────────────────
record_oak_stream (HW JPEG)        stream-ingest
  → tar.zst (rows + frame bins) →    jsonl + staging JPG → MP4 + parquet
upload_segments (手动)               derive-worker → session.READY
                                     Viewer /data/egodome
```

| 项 | 值 |
|----|-----|
| Station | `ego-001` |
| Token | `dl-upload-ego-001-v1` |
| 段根 (130) | `/home/server/cache/ego-001/segments` |
| Stream (34) | `data-storage/stream/ego-001` |
| 拓扑 | `ego-standard`（4 路 RGB） |

---

## 首次 / 升级部署

### 34（平台）

```bash
data-lab-platform/scripts/deploy-stream-ingest-v0.0.11.2.sh
RC_STATION=ego-001 data-lab-platform/scripts/rc-acceptance.sh
```

### 130（采集）

```bash
data-lab-platform/scripts/ego-130-provision.sh server@10.10.10.130 ego-001
# 或: python3 data-lab-platform/scripts/ego-130-provision-paramiko.py
data-lab-platform/scripts/ego-130-verify-production.sh server@10.10.10.130
data-lab-platform/scripts/ego-130-upload-mode.sh production   # 在 130 上
```

验收：`SEGMENT_FRAME_BIN=1`、`OAK_HW_JPEG=1`、`OAK_H264=0`；段目录 `frames/*.bin`，无 `*.h264`。

---

## 日常操作

### 1. 开始采集（130）

```bash
systemctl --user start ecs-record-oak-stream
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
```

### 4. 查看派生状态（34）

```bash
STATION_ID=ego-001 data-lab-platform/scripts/ego-derive status --station ego-001 --json
```

期望：`phase=READY`，`mp4Ok=true`，`session.READY` 存在。

### 5. 一键交付（34）

```bash
ego-deliver ego-001
```

### 6. Viewer

<http://10.10.10.34:8080/collection?station=ego-001> — 采集回放（四路 RGB MP4）

---

## 验收脚本

```bash
# 容器 + JPEG ingest 基线
RC_STATION=ego-001 data-lab-platform/scripts/rc-acceptance.sh

# 平台 CI（无 130 SSH；有 stream 数据时才验 READY）
RC_STATION=ego-001 data-lab-platform/scripts/ci-ego-platform.sh

# 全链路 E2E（清场 + 短录 + 上传 + READY）
bash data-lab-platform/scripts/ego-001-plan-b-e2e.sh

# 仅上传 + READY
RC_STATION=ego-001 RC_UPLOAD_LIMIT=3 data-lab-platform/scripts/rc-e2e-upload.sh
```

---

## 清场重跑

```bash
# 边缘 + 34 全清
EGO_RESET_YES=1 bash data-lab-platform/scripts/ego-001-reset-for-rerun.sh --yes

# 仅清 34
STATION_ID=ego-001 EGO_RESET_YES=1 bash data-lab-platform/scripts/ego-reset-34-only.sh --yes
```

---

## 排障

| 现象 | 检查 |
|------|------|
| 采集启动失败 | `ego-130-verify-production.sh`；`journalctl -u ecs-record-oak-stream` |
| `mux_fail capture_codec_not_jpeg` | 130 是否误开 `OAK_H264`；重 provision |
| `phase=UPLOADED` 卡死 | `ego-derive run`；`mux_validated.json`；derive-worker 日志 |
| 改代码不生效 | 重建 `v0.0.11.2` 镜像并 `force-recreate` ingest |

---

## 遗留脚本

H264 / `ego-lan-214` / `segment_mp4` 相关脚本已移至 `data-lab-platform/scripts/legacy/`。
