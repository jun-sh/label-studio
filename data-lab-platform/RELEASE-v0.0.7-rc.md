# v0.0.7-rc — ego-001 线性采集商用收口

## 部署（34）

```bash
# 构建
docker build -t data-lab-lerobot-studio:v0.0.7-rc data-lab-platform/lerobot-studio

# 启动（禁止 .mjs bind-mount）
docker-compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  -f data-lab-platform/docker-compose.v0.0.7-rc.yml \
  up -d stream-ingest
```

## 验收

```bash
# 本地：healthz + 空 staging mux + derive-status 延迟
data-lab-platform/scripts/rc-acceptance.sh

# 端到端：130 上传 1 段
data-lab-platform/scripts/rc-e2e-upload.sh
```

## 回滚

```bash
docker tag data-lab-lerobot-studio:local data-lab-lerobot-studio:v0.0.7-rc  # 如需保留
# 切回 v0.0.6 镜像或 deploy-release-v0.0.6 分支 compose
git checkout deploy-release-v0.0.6 -- data-lab-platform/docker-compose.platform.yml
docker-compose ... up -d stream-ingest   # 使用 v0.0.6 镜像 tag
```

## 130 客户端

与 RC 同分支部署 `ego_capture_studio`（`segment_store.py` 可靠删段已包含）。
上传：`ego-upload --limit 1` 或 `upload_segments_loop`。

## 不在本 RC

- 历史 MP4 全量恢复（需重传或 v0.0.8 parquet 导出）
- MCAP / lerobot-qc WIP（`feature/ego-mcap-pr3`）
