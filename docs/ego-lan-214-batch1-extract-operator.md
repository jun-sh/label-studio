# ego-lan-214 批次 1：tar.zst 解压后端运维指南

## 概述

批次 1 将 Node 侧 `zstd|tar` + `vendor/fzstd.mjs` 解压替换为 Python `tarfile` + `zstandard` 流式解压。默认仍走 **legacy**，灰度开启 `python` 后端。

| 组件 | 路径 |
|------|------|
| Python 解压脚本 | `data-lab-platform/lerobot-studio/scripts/extract-tar-zst.py` |
| Node 开关逻辑 | `stream-ingest.mjs` → `deriveExtractBackend()` |
| 依赖 | `requirements-derive.txt`（`zstandard==0.22.0`） |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DERIVE_EXTRACT_BACKEND` | `legacy` | `legacy` = zstd CLI / fzstd；`python` = tarfile + zstandard |
| `DERIVE_EXTRACT_VERIFY_SHA256` | `1` | `python` 后端解压前校验归档 sha256（与 raw 侧一致） |

配置位置：`data-lab-platform/docker-compose.platform.yml` → `stream-ingest.environment`。

## 灰度步骤

1. **重建镜像**（安装 `py3-zstandard`）：
   ```bash
   cd data-lab-platform
   docker compose -f docker-compose.platform.yml build stream-ingest
   ```

2. **确认默认 legacy 无回归**：
   ```bash
   bash data-lab-platform/scripts/ego-lan-214-phase1-lib-acceptance.sh
   ```

3. **单节点解压专项**（容器内需已挂载 `extract-tar-zst.py`）：
   ```bash
   RUN_EXTRACT_BATCH1=1 bash data-lab-platform/scripts/ego-lan-214-phase1-lib-acceptance.sh
   ```

4. **21 段全量 A/B 一致性**：
   ```bash
   RUN_EXTRACT_BATCH1=1 RUN_EXTRACT_ALL_SEGMENTS=1 \
     bash data-lab-platform/scripts/ego-lan-214-phase1-lib-acceptance.sh
   ```

5. **启用 python 后端**（单容器灰度）：
   ```bash
   DERIVE_EXTRACT_BACKEND=python docker compose -f docker-compose.platform.yml up -d stream-ingest
   ```

6. **全量重派生验证**（可选，耗时）：
   ```bash
   DERIVE_EXTRACT_BACKEND=python bash data-lab-platform/scripts/ego-rederive ego-lan-214
   ```

## 回退操作

立即回退至批次 0 行为：

```bash
DERIVE_EXTRACT_BACKEND=legacy docker compose -f docker-compose.platform.yml up -d stream-ingest
```

回退后验收：

```bash
bash data-lab-platform/scripts/ego-lan-214-phase1-lib-acceptance.sh
RUN_EXTRACT_BATCH1=1 bash data-lab-platform/scripts/ego-lan-214-phase1-lib-acceptance.sh
```

## 常见异常排查

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| `extract-tar-zst.py missing` | 卷未挂载或镜像未 COPY | 检查 compose volumes / 重建镜像 |
| `zstandard package not installed` | apk 未装 py3-zstandard | 重建 `stream-ingest` 镜像 |
| `sha256 mismatch` | 归档损坏或传输不完整 | 查 214 raw 与 34 `raw/segments` sha256；重新上传该段 |
| `unsafe tar path` | 归档含路径穿越 | 拒绝解压，查 214 打包脚本 |
| `archive contained no regular files` | 空包或截断 | 重新采集/上传 |
| 解压后派生失败但 legacy 正常 | python 与 legacy 输出差异 | `RUN_EXTRACT_ALL_SEGMENTS=1` 定位段；保持 `legacy` 并提 issue |

日志：

```bash
docker logs data-lab-stream-ingest-1 --since 1h 2>&1 | grep -E 'derive_segment|extract|sha256'
```

## 实现要点（运维参考）

- **原子落地**：解压至 `{dest}.tmp.{pid}`，成功后 `rename` 至目标目录；失败自动 `rmtree` 临时目录。
- **peek 统一**：`peekManifestFromTarZstArchive` 在 `python` 后端走 `--peek manifest.json`，legacy 走 `zstd|tar -xO`。
- **214 / 协议不变**：仅 34 派生解压层切换；Raw `tar.zst` 上传格式与冷备份不受影响。

## 验收清单

- [ ] 默认 `DERIVE_EXTRACT_BACKEND=legacy`，批次 0 脚本 15/15 PASS
- [ ] `RUN_EXTRACT_BATCH1=1`：一致性、截断拒绝、路径穿越拒绝
- [ ] `RUN_EXTRACT_ALL_SEGMENTS=1`：21 段树一致
- [ ] `DERIVE_EXTRACT_BACKEND=python` 全量派生 READY
- [ ] 回退 legacy 后验收 100% PASS
