# data-lab v0.1.2 — ego-001 运维收敛版

**镜像：** `data-lab-lerobot-studio:v0.1.2`  
**Compose overlay：** `docker-compose.v0.1.2.yml` (+ `docker-compose.v0.1.2-async.yml`)  
**部署：** `scripts/deploy-stream-ingest-v0.1.2-async.sh`

## 相对 v0.1.1 的变更

| 领域 | 内容 |
|------|------|
| **130 上传** | `ego-upload` 默认 `--notify`；支持 `--no-notify`；上传日志落盘 |
| **文档** | 新增 `docs/ego-001-使用手册.md`；旧 runbook 迁入 `docs/_deprecated/` |
| **脚本** | Phase RC / ego-214 / hamer 等迁入 `scripts/legacy/` |
| **systemd** | `ego-process-watcher` 用户级 unit 修复（去掉 User=/Group=） |
| **仓库** | 旧 compose 已归档至 `deploy/archive/compose/` |

## 部署

```bash
bash data-lab-platform/scripts/deploy-stream-ingest-v0.1.2-async.sh
python3 data-lab-platform/scripts/ego-130-provision-paramiko.py
bash data-lab-platform/scripts/rc-ego-001-production.sh
```

## 日常 SOP

```text
130:  ego-upload ego-001
34:   watcher 自动 ego-process
浏览器: /collection?station=ego-001 + /data/egodome
手册: data-lab-platform/docs/ego-001-使用手册.md
```

## 回滚 v0.1.1

```bash
EGO_PRODUCTION_TAG=v0.1.1 \
  EGO_COMPOSE_OVERLAY=data-lab-platform/docker-compose.v0.1.1.yml \
  EGO_COMPOSE_OVERLAY_ASYNC=data-lab-platform/docker-compose.v0.1.1-async.yml \
  bash data-lab-platform/scripts/deploy-stream-ingest-v0.1.1-async.sh
```
