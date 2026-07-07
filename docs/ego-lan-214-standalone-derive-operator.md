# ego-lan-214 独立派生架构（商用 Phase A）

## 目标

**上传 HTTP 与派生批处理彻底隔离** — stream-ingest 只做 raw 落盘；派生由独立 CLI / 容器执行。

## 架构

```
stream-ingest (HTTP, 轻量)
  → 接收 tar.zst、校验、写 raw/
  → 会话上传完成 → 写 session.DONE_UPLOAD
  → 绝不调用 extract / mux / parquet

ego-derive run / derive-worker (独立进程)
  → 读 session.DONE_UPLOAD
  → extract → parquet → mux → validate
  → 写 session.READY | session.FAILED
```

## 会话标记（磁盘唯一真相）

路径：`data-storage/stream/{station}/state/sessions/{sessionId}/`

| 文件 | 写入方 | 含义 |
|------|--------|------|
| `session.DONE_UPLOAD` | stream-ingest | 21 段 raw 已齐 |
| `session.DERIVING` | ego-derive run | 派生进行中 |
| `session.READY` | ego-derive run | parquet + MP4 校验通过 |
| `session.FAILED` | ego-derive run | 失败快照 |

全局锁：`state/deriver.lock`（同站同时仅 1 个派生进程）

## 开关

| 变量 | 默认 | 说明 |
|------|------|------|
| `DERIVE_STANDALONE` | **`1`**（生产灰度默认） | `1` = ingest 不写 in-process 派生，只写 DONE_UPLOAD |
| `DERIVE_MUX_MODE` | **`full`** | `full` = 全量一次性 MP4（推荐）；`incremental` = 旧增量 mux（仅回退） |
| `DERIVE_VIDEO_EXPORT_BACKEND` | **`auto`** | `auto` = 优先 `lerobot.encode_video_frames`，失败回退 streaming；`staging` = Phase A.1 回退 |
| `DERIVE_MUX_BACKEND` | `fluent` | ffmpeg 执行层（`legacy` / `fluent`） |
| `DERIVE_WATCH_POLL_MS` | `10000` | watch 轮询间隔 |

### 全量 Mux（`DERIVE_MUX_MODE=full`，当前生产默认）

- **不做**增量续写、`mux-state.json` 对账、分段 staging 清理
- parquet 就绪后：必要时从 **raw tar.zst 自动 rehydrate staging** → 四路相机各 **一次性** 编码完整 MP4
- 失败：**不修补、不增量重试**，直接 `FAILED` / 需人工触发全量 remux
- 灰度回退：`DERIVE_MUX_MODE=incremental`（不推荐，已知帧数漂移风险）

## 线上异常：MP4 帧数不对 / 长期卡在「MP4 合成中」

**根因（已验证）**：非 214 Raw 问题；多为旧 **增量 mux + `mux-state` 虚高 + staging 被清** 导致 skip 死循环。  
**标准恢复（34 上执行）**：

```bash
# 1. 暂停 derive-worker，避免抢锁
docker stop data-lab-derive-worker-1

# 2. 清除陈旧派生锁（worker 异常退出后可能残留）
docker exec data-lab-stream-ingest-1 rm -f /srv/stream/ego-lan-214/state/deriver.lock

# 3. 强制全量 mux（自动 rehydrate staging，约 5–10 分钟 / 21 段）
docker exec -e DERIVE_MUX_MODE=full data-lab-stream-ingest-1 \
  node /app/ego-derive-run.mjs run --station ego-lan-214 --mux-only

# 4. 恢复 worker
docker start data-lab-derive-worker-1

# 5. 验证（34 或 214）
curl -s 'http://10.10.10.34:8080/lerobot/api/collection/stations/ego-lan-214/derive-status' \
  | jq '.phase, .progress.mp4Ok'
# 期望：READY / true
```

等价封装：`bash data-lab-platform/scripts/ego-derive run --station ego-lan-214 --mux-only`（需先 `docker stop derive-worker` 并清锁）。

**勿删 raw/**；parquet 已正确时无需重传 214。

生产灰度（默认已启用）：

```bash
docker-compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml up -d stream-ingest derive-worker
```

`derive-worker` 随 compose 默认启动，轮询 `session.DONE_UPLOAD` 并执行派生。手动补跑：`bash data-lab-platform/scripts/ego-derive run`

## CLI

```bash
# 手动派生（阻塞直到 READY / FAILED）
bash data-lab-platform/scripts/ego-derive run --station ego-lan-214

# 仅 mux 阶段
bash data-lab-platform/scripts/ego-derive run --mux-only

# 自动 watch DONE_UPLOAD
bash data-lab-platform/scripts/ego-derive watch

# 状态（磁盘 + session 标记）
bash data-lab-platform/scripts/ego-derive status --json
ego-derive-status --watch   # 人类可读进度
```

## 与现有流程兼容

- `DERIVE_STANDALONE=0`：回退 in-process 派生（批次 0–2 旧行为）
- `POST …/derive-start`：standalone 模式下写 `session.DONE_UPLOAD`，不启动 heavy 计算
- `ego-rederive`：仍可用于清派生层；完成后用 `ego-derive run`

## 落地路线

| 阶段 | 内容 | 状态 |
|------|------|------|
| **A** | CLI `ego-derive run/watch`、session 标记、`DERIVE_STANDALONE` | ✅ |
| **A.1** | `DERIVE_MUX_MODE=full` 全量 mux + staging rehydrate | ✅ 当前生产默认 |
| **B** | `export-videos-from-parquet.py` + `DERIVE_VIDEO_EXPORT_BACKEND=auto`；已移除增量 mux / mux-state 死代码 | 🚧 灰度默认 `auto`，待镜像重建 + 回归封板 |
| **C** | `derive-worker` 生产化、资源限制、监控告警 | 骨架已有 |

### 阶段 B（当前灰度默认 `auto`）

1. Parquet 定帧序/帧数；像素从 raw `tar.zst` 解码；`auto` 优先官方 `encode_video_frames`，否则 `streaming`（ffmpeg image2pipe）
2. 已删除：`reconcileMuxLastMuxedFrame`、增量 mux 续写、`mux-state.json` 读写（遗留文件可手动删）
3. 镜像需重建以安装 `lerobot`（`requirements-parquet-lerobot.txt`，失败时 auto 自动回退 streaming）
4. 回归：`bash data-lab-platform/scripts/ego-lan-214-phase-b-regression.sh`

**全量回归（含导出耗时，READY 工位）：**

```bash
bash data-lab-platform/scripts/ego-lan-214-phase-b-regression.sh
# 仅校验 parquet/帧数，不重跑导出：
SKIP_EXPORT=1 bash data-lab-platform/scripts/ego-lan-214-phase-b-regression.sh
```

## 回退

```bash
DERIVE_STANDALONE=0 docker compose ... up -d stream-ingest
docker compose --profile derive-worker stop derive-worker
```
