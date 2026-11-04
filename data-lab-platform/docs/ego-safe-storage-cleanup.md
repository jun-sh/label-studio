# ego / QC 安全清理说明

避免误删 **http://10.10.10.34:8080/qc** 上的标注与交付数据。统一使用：

`data-lab-platform/scripts/ego-safe-storage-cleanup.sh`

---

## QC 保护清单（永远不要删）

| 路径 | 作用 |
|------|------|
| `data-storage/lerobot-qc/sidecar/` | **标注真源**：`qc_manifest.json`、`annotation_corrections.csv`、`audit_log.jsonl`、`removed_episodes.json` |
| `data-storage/lerobot-qc/delivery/` | QC 重建/导出的交付包 |
| `data-storage/lerobot-qc/collections.registry.json` | 工位/数据集注册表 |

**禁止**：`rm -rf data-storage/lerobot-qc` 或任何会递归删除上述目录的命令。

脚本内对 sidecar / delivery / registry / 整个 `lerobot-qc` 根目录有 **硬拒绝**（`abort_if_qc_protected_target`）。

---

## 允许清理的 QC 路径

仅 **视频裁剪缓存**（可随播放重新生成，不含标注）：

`data-storage/lerobot-qc/cache/trimmed_videos/`

```bash
# 预览
bash data-lab-platform/scripts/ego-safe-storage-cleanup.sh --qc-trim-cache

# 执行（只删 trimmed_videos 下内容）
bash data-lab-platform/scripts/ego-safe-storage-cleanup.sh --qc-trim-cache --apply
```

等价手工命令（仍建议用脚本，避免路径打错）：

优先用脚本（大量文件 + 缓存多为 **容器 root 属主**，脚本会 `docker exec` 删除）。手工等价：

```bash
docker exec data-lab-lerobot-qc-1 find /data/cache/trimmed_videos -mindepth 1 -delete
```

**不要**删除整个 `cache/` 以外的 QC 树。

---

## QC sidecar 定期备份（建议每周或清 cache 前）

体积小（通常几十 MB），丢了 cache 能忍，丢了 sidecar 不能忍。

```bash
# 预览
bash data-lab-platform/scripts/ego-safe-storage-cleanup.sh --backup-qc-sidecar

# 写入 data-storage/lerobot-qc/backups/qc-sidecar-<UTC时间>.tar.gz
bash data-lab-platform/scripts/ego-safe-storage-cleanup.sh --backup-qc-sidecar --apply
```

备份只打包 `sidecar/` 目录，不含 `delivery` 与 `cache`。

---

## ego stream 工位清理（与 QC 无关）

不触碰 `raw/`、`derived/sess_*`、`videos/`、`data/`、`state/`。

```bash
bash data-lab-platform/scripts/ego-safe-storage-cleanup.sh              # dry-run
bash data-lab-platform/scripts/ego-safe-storage-cleanup.sh --apply
bash data-lab-platform/scripts/ego-safe-storage-cleanup.sh --apply --station ego-002
```

**整站删除（含 raw）**：`scripts/ego-trash-station.sh ego-002 --apply`（挪到 `_trash/`，可 `mv` 还原）。  
**只清 collection / 重 derive（保留 raw）**：`scripts/ego-reset-34-only.sh` — **禁止**当整站删除用。  
**删左侧若干条（按 `#` / episode_index）** — **首选 Collection UI**：Episodes 工具栏 **「多选」→ 勾选 →「删除」**（仅 L2，保留 raw）。  
运维备用 CLI：`scripts/ego-delete-episodes.sh ego-002 --index 12,31 --apply`（默认 dry-run）。  
API：`POST /lerobot/api/collection/stations/{id}/episodes/delete`，body `{ "episode_indices": [12,31], "confirm": true }`。

`.bak` 迁移目录仍用：`scripts/ego-cleanup-bak-dirs.sh --apply`

---

## 推荐运维顺序（腾 QC 磁盘）

1. `--backup-qc-sidecar --apply`
2. `--qc-trim-cache --apply`
3. 需要时再跑 stream `--apply`

---

*文档版本：2026-10-24*
