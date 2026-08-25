# data-lab v0.1.3 — 130 多 session 上传 + frame_index 契约

**镜像：** `data-lab-lerobot-studio:v0.1.3`  
**Compose overlay：** `docker-compose.v0.1.3.yml` (+ `docker-compose.v0.1.3-async.yml`)  
**部署：** `scripts/deploy-stream-ingest-v0.1.3-async.sh`

## 相对 v0.1.2 的变更

| 领域 | 内容 |
|------|------|
| **130 ego-upload** | 默认扫描全站所有 `sess_*` 待传段；支持 `--session-id` 单 session 上传 |
| **ego-stream-client** | `segment_store` / `segment_upload` 增强；与 130 现场代码一致 |
| **34 derive** | `frame-index.mjs` manifest v2 契约；staging / parquet / unit derive 对齐 |
| **ego-local-web** | 采集 UI 更新（capture_ui） |
| **文档** | GPD MicroPC 2 采集站配置手册；STABLE-v0.0.12 迁入 `_deprecated/` |

## 部署

```bash
bash data-lab-platform/scripts/deploy-stream-ingest-v0.1.3-async.sh
python3 data-lab-platform/scripts/ego-130-provision-paramiko.py
bash data-lab-platform/scripts/rc-ego-001-production.sh
```

## 日常 SOP

```text
130:  ego-upload ego-001
34:   watcher 自动 ego-process
手册: data-lab-platform/docs/ego-001-使用手册.md
```

## 回滚 v0.1.2

```bash
EGO_PRODUCTION_TAG=v0.1.2 \
  EGO_COMPOSE_OVERLAY=data-lab-platform/docker-compose.v0.1.2.yml \
  EGO_COMPOSE_OVERLAY_ASYNC=data-lab-platform/docker-compose.v0.1.2-async.yml \
  bash data-lab-platform/scripts/deploy-stream-ingest-v0.1.2-async.sh
```
