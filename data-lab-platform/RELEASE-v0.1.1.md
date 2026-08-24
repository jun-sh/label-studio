# data-lab v0.1.1 — Phase D 生产版（EgoDome）

**镜像：** `data-lab-lerobot-studio:v0.1.1`  
**Compose overlay：** `docker-compose.v0.1.1.yml` (+ `docker-compose.v0.1.1-async.yml`)  
**生产默认值：** `scripts/ego-production-defaults.sh`（所有运维脚本统一引用）  
**部署：** `scripts/deploy-stream-ingest-v0.1.1-async.sh`

## 相对 v0.1.0 的变更

| 领域 | 内容 |
|------|------|
| **Phase D** | 删除 legacy 增量合并路径；`runDerivePipeline` 始终走 unit |
| **默认布局** | `DERIVE_LAYOUT` 代码默认 `unit` |
| **运维统一** | `ego-production-defaults.sh` 单一版本源；v0.1.0 脚本 deprecated |

## 部署

```bash
# 34 — async derive-worker（生产推荐）
bash data-lab-platform/scripts/deploy-stream-ingest-v0.1.1-async.sh

# 验收
bash data-lab-platform/scripts/rc-ego-001-v0.1.1-preflight.sh
bash data-lab-platform/scripts/rc-ego-001-p2-phase-d.sh
bash data-lab-platform/scripts/rc-ego-001-p2-ops-async-preflight.sh
```

## 日常 SOP

```text
130:  ego-upload ego-001
34:   （watcher 自动 ego-process，或手动 ego-process ego-001）
浏览器: /collection?station=ego-001 + /data/egodome

手册: docs/ego-001-使用手册.md
```

## 重启后

容器 `restart: unless-stopped` 会自启并保留 v0.1.1 配置。  
**勿**裸跑无 overlay 的 `docker compose up`（会回退到 `:local` 镜像）。

```bash
bash data-lab-platform/scripts/rc-ego-001-v0.1.1-preflight.sh
```

## 回滚

```bash
EGO_PRODUCTION_TAG=v0.1.0 \
  EGO_COMPOSE_OVERLAY=data-lab-platform/deploy/archive/compose/docker-compose.v0.1.0.yml \
  EGO_COMPOSE_OVERLAY_ASYNC=data-lab-platform/deploy/archive/compose/docker-compose.v0.1.0-async.yml \
  LEROBOT_IMAGE_TAG=v0.1.0 \
  bash data-lab-platform/scripts/deploy-stream-ingest-v0.1.1-async.sh
```
