# v0.0.9.2 — segment_mp4 derive 闭环（ego-001 交付版）

基于 v0.0.9.1。修复 ingest 多段 MP4 concat、`session_start` 拓扑、derive → READY 验收。

## 变更摘要

| 项 | v0.0.9.1 | v0.0.9.2 |
|----|----------|----------|
| 多段 MP4 concat | pairwise 原地写 / 并发风险 | `tmpOut` + per-camera 锁 + ffconcat 1.0（无 duration hint） |
| `session_start` info.json | 可能写入 `rear_left`（拓扑漂移） | 按 `station-topology` + intrinsics 写 `ego-standard` 四路 |
| derive segment_mp4 | 走 bin/staging append | jsonl→parquet，跳过 staging mux |
| MP4 帧数探针 | `nb_read_frames`（偏低） | `nb_read_packets`（与 ffprobe 一致） |
| READY 门禁 | 严格 ±1 帧 | segment_mp4：≥85% 行数 + 跨相机差 ≤120 帧 |
| `mux_validated` 探针 | `forceProbe` 仍读旧缓存 | **`forceProbe` 时跳过缓存，强制 ffprobe** |
| ingest 并发 | `SEGMENT_INGEST_BATCH_SIZE=2` | **1**（segment_mp4 串行 commit） |
| derive-worker | bind-mount `.mjs` | **镜像内代码**（`volumes: !reset`） |

## 涉及文件

- `segment-mp4-ingest.mjs` — concat 锁、段后帧数校验
- `mux-exec.mjs` — `buildMp4ConcatListPaths`、`nb_read_packets`
- `stream-ingest.mjs` — 拓扑同步、`evaluateMp4Readiness`、derive jsonl 路径
- `derive-pipeline.mjs` — segment_mp4 derive + parquet sync
- `scripts/sync-stream-parquet.py` — 动态 video keys
- `config/station-topology.json` — `ego-001` → `ego-standard`

## 部署（34）

```bash
# 自仓库根目录
docker build -t data-lab-lerobot-studio:v0.0.9.2 data-lab-platform/lerobot-studio

docker-compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  -f data-lab-platform/docker-compose.v0.0.9.2.yml \
  up -d stream-ingest derive-worker lerobot
```

或使用脚本：

```bash
data-lab-platform/scripts/deploy-stream-ingest-v0.0.9.2.sh
```

**注意**：`docker cp` 热修后必须 `docker restart stream-ingest`；生产以镜像为准。

## 130 配对要求

- `v0.0.8-segment-mp4.conf` 生效：`SEGMENT_H264=1`，`hw_h264=1`
- 确认无 `z-production-egoverse.conf` 覆盖 H264（`systemctl show ecs-record-oak-stream`）
- 上传：`upload_segments`（`upload_segments_loop` disabled）

## 验收

```bash
# 容器 + 主路径
RC_STATION=ego-001 data-lab-platform/scripts/rc-acceptance.sh

# 130 上传 + READY（见 docs/ego-001-runbook.md）
RC_STATION=ego-001 RC_UPLOAD_LIMIT=3 data-lab-platform/scripts/rc-e2e-upload.sh
```

运维手册：[docs/ego-001-runbook.md](docs/ego-001-runbook.md)

期望：

- `segment_mp4_ingest ok=true`（每段 4 相机）
- 3 段后每路 MP4 **~800–900 packets**（非 ~300）
- `derive-status` → `phase=READY`，`mp4Ok=true`
- `info.json` features 含 `observation.images.camera_depth_left`（OAK 深度相机原始流；**采集回放页不要求展示**）

## 回滚

```bash
docker-compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  -f data-lab-platform/docker-compose.v0.0.9.yml \
  up -d stream-ingest derive-worker
```

## 已知限制（不挡交付）

- H264 包数通常略少于 jsonl 行数（~87–99%）
- `front_left` 偶发帧数偏低，需后续在 130 编码侧对齐
- v0.0.10 计划：删除 staging 死代码（见 RELEASE-v0.0.9.md）
