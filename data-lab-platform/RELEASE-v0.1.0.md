# data-lab v0.1.0 — EgoDome 运维固化版

> **已 superseded：** 请使用 [RELEASE-v0.1.1.md](./RELEASE-v0.1.1.md)（Phase D unit-only）。

**镜像：** `data-lab-lerobot-studio:v0.1.0`  
**Compose overlay：** `docker-compose.v0.1.0.yml` (+ `docker-compose.v0.1.0-async.yml`)  
**部署：** `scripts/deploy-stream-ingest-v0.1.0.sh` → 现为 deprecated wrapper，转发至 v0.1.1

## 相对 v0.0.13-p1 的变更（热更新已 bake-in）

| 领域 | 内容 |
|------|------|
| **P2 unit layout** | `DERIVE_LAYOUT=unit`、multi-episode `file_index`、`sync-stream-parquet.py` unit manifest |
| **P-Ops-1** | `ego-upload`（130）、`ego-process`（34）、`derive-pending` |
| **P-Ops-2** | `ego-derive-mode.sh` manual/async、`ego-wait-derive-sessions.sh` |
| **P-Ops-3** | convert 后 auto `ego-viewer-sync`、`POST …/process-notify`、`ego-process-watcher.sh` |
| **Gateway** | nginx `process-notify` 路由 |
| **门禁** | `session.READY` → parquet ready（unit 布局 convert 不再空等） |

## 升级至 v0.1.1

```bash
bash data-lab-platform/scripts/deploy-stream-ingest-v0.1.1-async.sh
bash data-lab-platform/scripts/rc-ego-001-p2-phase-d.sh
```

## 回滚（仅应急）

```bash
EGO_PRODUCTION_TAG=v0.1.0 EGO_COMPOSE_OVERLAY=data-lab-platform/deploy/archive/compose/docker-compose.v0.1.0.yml \
  LEROBOT_IMAGE_TAG=v0.1.0 bash data-lab-platform/scripts/deploy-stream-ingest-v0.1.1-async.sh
# 注意：v0.1.0 仍含 legacy derive 路径
```
