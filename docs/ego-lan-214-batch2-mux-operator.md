# ego-lan-214 批次 2：fluent-ffmpeg mux 运维指南

## 概述

批次 2 将 `ffmpeg`/`ffprobe` 裸 `spawn` 执行层替换为可切换的 **legacy | fluent-ffmpeg** 双后端。业务 mux 流程、状态机、重试逻辑不变。

| 组件 | 路径 |
|------|------|
| 执行层 | `mux-exec.mjs` |
| 业务流程 | `stream-ingest.mjs`（`muxOneCamera` 等未改） |
| 依赖 | `package.json` → `fluent-ffmpeg@2.1.3` |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DERIVE_MUX_BACKEND` | `legacy` | `legacy` = spawn ffmpeg；`fluent` = fluent-ffmpeg + MP4 concat duration 修正 |

配置位置：`docker-compose.platform.yml` → `stream-ingest.environment`。

## 核心改进（fluent 后端）

- **JPG→MP4**：与 legacy 相同 ffconcat（含 `duration` 行），经 fluent-ffmpeg 执行
- **MP4 增量拼接**：`buildMp4ConcatList` 对第一段探测 `duration` 并写入 concat 列表，缓解无 duration 导致的帧数漂移

## 灰度步骤

1. 重建镜像（`npm install fluent-ffmpeg`）：
   ```bash
   docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml build stream-ingest
   ```

2. 默认 legacy 回归：
   ```bash
   bash data-lab-platform/scripts/ego-lan-214-phase1-lib-acceptance.sh
   RUN_MUX_BATCH2=1 bash data-lab-platform/scripts/ego-lan-214-phase1-lib-acceptance.sh
   ```

3. 启用 fluent mux（仅 mux 层，可与 `DERIVE_EXTRACT_BACKEND=python` 叠加）：
   ```bash
   DERIVE_MUX_BACKEND=fluent docker-compose ... up -d stream-ingest
   ```

4. 仅重跑 mux（不重解压/parquet）：
   ```bash
   # 清除 MP4 + mux 状态后触发 mux-only（沿用 derive-pipeline mux 重试）
   DERIVE_MUX_BACKEND=fluent bash data-lab-platform/scripts/ego-rederive --prepare-only
   # 手动清除 videos/ 与 mux_validated 后 derive pipeline 会在 parquet ready 时 mux
   ```

## 回退

```bash
DERIVE_MUX_BACKEND=legacy docker-compose ... up -d stream-ingest
```

## 验收

```bash
RUN_MUX_BATCH2=1 bash data-lab-platform/scripts/ego-lan-214-phase1-lib-acceptance.sh
```

覆盖：模块挂载、fluent 可导入、probe 一致性、concat duration 列表、无 `.muxing.tmp` 残留。

## 常见异常

| 现象 | 处理 |
|------|------|
| `Cannot find module 'fluent-ffmpeg'` | 重建 stream-ingest 镜像 |
| MP4 帧数漂移 | 确认 `DERIVE_MUX_BACKEND=fluent`；检查 `mux_validated.json` |
| mux 失败自动重试 | 批次 0 `DERIVE_MUX_AUTO_RETRY=1` 仍生效，无需改动 |
