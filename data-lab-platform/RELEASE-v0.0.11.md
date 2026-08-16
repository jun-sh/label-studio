# v0.0.11 — MCAP 剥离 + LeRobot high_freq IMU

基于 v0.0.10。130 采集契约不变（`imu_raw.jsonl` 仍写入 tar.zst）；34 交付路径迁移至 LeRobot v3 vendor 扩展。

## 变更摘要

| 项 | v0.0.10 | v0.0.11 |
|----|---------|---------|
| MCAP 导出 / viewer | 代码仍在镜像内 | **完全删除**（脚本、依赖、derive 钩子、mcap-viewer） |
| 200Hz IMU 交付 | 仅归档于 `raw/segments/*.tar.zst` | **`data/chunk-000/high_freq/imu_200hz.parquet`** + `meta/info.json` → `vendor_meta.high_freq` |
| derive segment_mp4 | parquet sync 后即 mux | parquet sync 后 **自动 ingest high_freq IMU** |
| `total_frames` | 统计所有 `data/**/*.parquet` | **排除 `high_freq/`** 目录 |
| CI | rc-acceptance + e2e READY | 追加 **RC-5 high_freq IMU** |
| vendor_annotations | 无 | **`meta/vendor_annotations/*`** + derive 自动 sync |
| UI | 无 vendor 展示 | **lerobot-qc** + **collection_viz** 显示 IMU / 子任务统计 |

## 部署

```bash
bash data-lab-platform/scripts/deploy-stream-ingest-v0.0.11.sh
```

或手动：

```bash
docker build -t data-lab-lerobot-studio:v0.0.11 data-lab-platform/lerobot-studio

docker-compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  -f data-lab-platform/docker-compose.v0.0.11.yml \
  up -d stream-ingest derive-worker lerobot

docker restart data-lab-nginx-1
```

## 历史会话回填（Phase C）

对已有 READY 会话，derive 不会重跑 parquet sync；需手动回填一次：

```bash
python3 data-lab-platform/lerobot-studio/scripts/ingest-imu-high-freq.py \
  data-storage/stream/ego-001 \
  --session sess_adeec0b9d2b5470596ff643793c55499
```

## 验收

```bash
RC_STATION=ego-001 bash data-lab-platform/scripts/ci-ego-platform.sh
```

## 设计文档

[data-lab-platform/docs/ego-lerobot-vendor-ext-migration.md](docs/ego-lerobot-vendor-ext-migration.md)

## 未纳入本版

- `contact_pixel_per_frame` 实体数据（骨架已就绪，待 embodied-annotate 写入）
- EgoDome pipeline 深度消费 vendor_annotations（仅 toolbar 摘要）
