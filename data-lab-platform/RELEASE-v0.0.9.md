# v0.0.9 — 单主路径硬失败（去掉 staging 假回退）

基于 v0.0.8。行为变化仅 ingest 失败策略与 raw 契约澄清。

## 变更

| 项 | v0.0.8 | v0.0.9 |
|----|--------|--------|
| `segment_mp4_ingest` 失败 | 尝试 `segment_mp4_fallback_staging`（H264 段通常无 bin，**实际无效**） | **`segment_mp4_fail` + 整段拒绝**，不 commit |
| 无 `streams/*.mp4` 的 tar.zst | 可能走 bin→staging | **上传即拒** `segment_mp4_required` |
| MP4 ingest 顺序 | jsonl 先写再 mux | **先 mux 再写 jsonl**（避免半提交） |
| raw 契约 | 文档写必须有 `frames/*.bin` | **H264 段 raw = rows + imu + streams/*.mp4**；整包 tar.zst 在 34 冷归档保留 |

## 数据契约（v0.0.9 采集端）

tar.zst 成员（H264 生产路径）：

- `manifest.json`
- `rows.jsonl`
- `imu_raw.jsonl`
- `streams/*.mp4`（**必须**，每相机 1 个）

`frames/*.bin`：**v0.0.9 生产采集不再写入**；仅 `STREAM_PRIMARY_PATH=staging_mux` 遗留导入需要。

## 部署（34）

```bash
docker build -t data-lab-lerobot-studio:v0.0.9 data-lab-platform/lerobot-studio

docker-compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  -f data-lab-platform/docker-compose.v0.0.9.yml \
  up -d stream-ingest
```

130 采集端配置同 [RELEASE-v0.0.8.md](RELEASE-v0.0.8.md)（ffmpeg + v0.0.8-segment-mp4.conf）。

## 验收

```bash
data-lab-platform/scripts/rc-acceptance.sh
data-lab-platform/scripts/rc-e2e-upload.sh
```

日志应出现 `segment_mp4_ingest ok=true`，**不应**出现 `segment_mp4_fallback_staging`。

## 回滚

```bash
# 回到 v0.0.8 镜像（恢复 staging 回退行为）
docker-compose ... -f data-lab-platform/docker-compose.v0.0.8.yml up -d stream-ingest
```

## 后续（v0.0.10 计划）

- 删除 bin→staging mux 死代码（需确认无 legacy tar 待导入）
- 拆分 `stream-ingest.mjs` legacy 模块

## v0.0.9.1（采集/部署加固）

| 项 | 说明 |
|----|------|
| 130 preflight | 采集启动检查 `ffmpeg`（`preflight_segment_h264_capture`） |
| 关段校验 | `verify_segment_stream_mp4s`（默认 ≥4 路 MP4） |
| 上传门禁 | `SEGMENT_H264=1` 时段无 MP4 则 skip，不打包上传 |
| RC 脚本 | `rc-acceptance.sh` 验 `segment_mp4` 主路径，不再测 staging mux |
| 130 部署 | `scripts/ego-130-provision.sh` 从仓库 rsync + systemd |
| derive | compose 显式 `DERIVE_ASYNC_*=0` |

## v0.0.9.2（segment_mp4 derive 闭环 — ego-001 交付）

见 [RELEASE-v0.0.9.2.md](RELEASE-v0.0.9.2.md)：多段 MP4 concat、拓扑同步、READY 探针与 compose 镜像化部署。
