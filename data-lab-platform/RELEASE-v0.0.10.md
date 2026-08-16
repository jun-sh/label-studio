# v0.0.10 — platform hardening (post ego-001 / EgoDome sign-off)

基于 v0.0.9.2。不改动 130 采集契约；聚焦 34 平台可维护性与回归门禁。

## 变更摘要

| 项 | v0.0.9.2 | v0.0.10 |
|----|----------|---------|
| derive-worker 挂载 | platform.yml 绑定 `.mjs`（Compose 2.6 无法 `!reset`） | **platform.yml 仅 `/srv/stream`**；热修走 `docker-compose.dev-mjs-bind.yml` |
| staging JPEG mux | segment_mp4 下仍可能被 `scheduleMux` 调度 | **`scheduleMux` / `runMux` 在 segment_mp4 下 no-op** |
| CI | 无 | `ci-ego-platform.sh` + workflow 占位（自托管 34 执行） |
| H264 包数审计 | 人工 ffprobe | `ego-130-h264-packet-audit.sh` |
| pipeline 站表 | 仅 ego-hand-pipeline 本地 | `config/ego-pipeline-stations.yaml` 纳入 git |

## 部署

```bash
docker build -t data-lab-lerobot-studio:v0.0.10 data-lab-platform/lerobot-studio

docker-compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  -f data-lab-platform/docker-compose.v0.0.10.yml \
  up -d stream-ingest derive-worker lerobot

docker restart data-lab-nginx-1
```

或使用脚本：`data-lab-platform/scripts/deploy-stream-ingest-v0.0.10.sh`

## 验收

```bash
RC_STATION=ego-001 bash data-lab-platform/scripts/ci-ego-platform.sh
RC_STATION=ego-001 bash data-lab-platform/scripts/ego-130-h264-packet-audit.sh
```

## 开发热修（可选）

```bash
# 额外叠加 dev-mjs-bind.yml，勿用于生产
docker-compose ... -f data-lab-platform/docker-compose.dev-mjs-bind.yml up -d stream-ingest derive-worker
```

## 未纳入本版（后续）

- 删除 `stream-ingest.mjs` 内 legacy staging mux 实现体（segment_mp4 下已全部禁用调度入口）
- 130 `genpts` 调优需新 session A/B 验证（`ego-130-h264-tuning.sh enable`）
