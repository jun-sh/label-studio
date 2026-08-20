# ego-001 交付手册（一页）

**原则：** 130 HW JPEG 采集 · 手动 tar.zst 上传 · 34 staging mux · `session.READY` 后 Viewer 可播

**版本：** 34 `data-lab-lerobot-studio:v0.1.1` · 130 `z-production-egoverse.conf`（`OAK_H264=0`）

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
# 构建 + 部署 v0.1.1（生产推荐，Phase D unit-only）
bash data-lab-platform/scripts/deploy-stream-ingest-v0.1.1-async.sh

# 验收
RC_STATION=ego-001 bash data-lab-platform/scripts/rc-ego-001-v0.1.1-preflight.sh
bash data-lab-platform/scripts/rc-ego-001-p2-phase-d.sh
RC_STATION=ego-001 bash data-lab-platform/scripts/rc-ego-001-p2-ops-async-preflight.sh
```

详见 [RELEASE-v0.1.1.md](../RELEASE-v0.1.1.md)。

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

### 目标 SOP（P-Ops-1）

```text
130:  录制 Start/Stop（手机 UI）
130:  ego-upload ego-001
34:   ego-process ego-001
浏览器: /collection?station=ego-001  +  /data/egodome
```

或 34 上一键：`ego-deliver ego-001`（SSH 130 上传 + ego-process）。

### P-Ops-2：async derive-worker（生产默认）

```bash
# 34 — 一键切换 async 模式（DERIVE_ASYNC=1 + 启动 derive-worker）
bash data-lab-platform/scripts/ego-derive-mode.sh async

# 或完整部署（推荐首次/升级）
bash data-lab-platform/scripts/deploy-stream-ingest-v0.1.1-async.sh

# 验收
RC_STATION=ego-001 bash data-lab-platform/scripts/rc-ego-001-p2-ops-async-preflight.sh
bash data-lab-platform/scripts/rc-ego-001-p2-phase-d.sh
```

切换后 `ego-process ego-001` 会**等待 derive-worker** 而非手动 `ego-derive run`。  
回退 manual：`bash data-lab-platform/scripts/ego-derive-mode.sh manual`

### P-Ops-3：自动 Viewer sync + 上传通知

- convert 完成后 `ego-run-pipeline --skip-deploy` **自动**跑 `ego-viewer-sync.sh`
- 130 上传后通知 34（推荐）：
  ```bash
  ego-upload ego-001 --notify
  # 或 EGO_NOTIFY_PROCESS=1 ego-upload ego-001
  ```
- 34 安装 systemd timer（生产推荐，开机自启）：
  ```bash
  sudo bash data-lab-platform/scripts/install-ego-process-watcher-systemd.sh
  systemctl status data-lab-ego-process-watcher.timer
  journalctl -u data-lab-ego-process-watcher.service -f
  ```
  每 30s 轮询 `process-notify.pending.json` 并执行 `ego-process`。
- 前台调试：`bash data-lab-platform/scripts/ego-process-watcher.sh watch`

### Viewer 多 episode（Phase D / unit layout）

- `/data/egodome` 现为 **N 个独立 episode**（每 trip 一个），不再是整站连续时间轴
- Viewer 内用 episode 选择器切换；IMU 曲线不再跨 session 锯齿
- 操作员说明：一次采集 = 一个 episode = 一个训练样本（`STREAM_SESSION_SINGLE_EPISODE=0`）

### 1. 开始采集（130）

```bash
systemctl --user start ecs-record-oak-stream
```

### 2. 停止采集

```bash
systemctl --user stop ecs-record-oak-stream
```

### 3. 上传（130）

```bash
ego-upload ego-001
```

等价于 `upload_segments --limit 0 --ensure-session`（幂等，只传未 UPLOADED 段）。  
首次部署需 `ego-130-provision.sh` 安装 `~/.local/bin/ego-upload`。

### 4. 派生 + 后处理 + Viewer（34）

```bash
ego-process ego-001
```

步骤：derive 批量（`DERIVE_LAYOUT=unit`）→ convert 增量 → `ingest-bundled-datasets.sh`（非 full deploy）。

### 5. 查看派生状态（34）

```bash
STATION_ID=ego-001 data-lab-platform/scripts/ego-derive status --station ego-001 --json
```

期望：`phase=READY`，`mp4Ok=true`，`session.READY` 存在。

### 6. 一键交付（34，可选）

```bash
ego-deliver ego-001
```

### 7. Viewer

<http://10.10.10.34:8080/collection?station=ego-001> — 采集回放（四路 RGB MP4）

---

## 验收脚本

```bash
# 生产全套（v0.1.1 + Phase D + async + watcher timer）
bash data-lab-platform/scripts/rc-ego-001-production.sh

# 分项
bash data-lab-platform/scripts/rc-ego-001-v0.1.1-preflight.sh
bash data-lab-platform/scripts/rc-ego-001-p2-phase-d.sh
bash data-lab-platform/scripts/rc-ego-001-p2-ops-async-preflight.sh

# 容器 + JPEG ingest 基线
RC_STATION=ego-001 data-lab-platform/scripts/rc-acceptance.sh
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
