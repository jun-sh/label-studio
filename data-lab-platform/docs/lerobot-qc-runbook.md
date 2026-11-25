# LeRobot QC 运行手册

适用于当前部署形态（2026-11-25）：单机 docker-compose，服务 `lerobot-qc`，容器 `data-lab-lerobot-qc-1`。
上线进度和待决事项见 [lerobot-qc-launch-checklist.md](lerobot-qc-launch-checklist.md)。

## 1. 组成与路径

| 内容 | 容器内 | 宿主机 |
|---|---|---|
| 后端代码（只读绑定挂载） | `/app/backend` | `<仓库>/data-lab-platform/lerobot-qc/backend` |
| 审阅结果 sidecar（**唯一一份，务必备份**） | `/data/qc-sidecar` | `<仓库>/data-storage/lerobot-qc/sidecar` |
| 交付集 | `/data/qc-delivery` | `<仓库>/data-storage/lerobot-qc/delivery` |
| 缓存（可删，会重建） | `/data/cache` | `<仓库>/data-storage/lerobot-qc/cache` |
| 数据集合注册表 | `/data/qc-collections.registry.json` | `<仓库>/data-storage/lerobot-qc/collections.registry.json` |
| 源数据（只读） | `/host-media/...`、`/data/bookduo/...` | `/media/user01/...`（外接盘 BookDuo_2） |

- 入口：网关 `http://<host>:8080/qc/`（审阅页）、`/qc/overview`（总览）；健康检查 `GET /healthz`（容器内 7863 端口）。
- 一个 uvicorn 进程。会话、重建任务状态在内存里，同时持久化到各包 sidecar 的 `qc_manifest.json`；审计记录也在 sidecar 目录。
- sidecar 按包的“上级目录/包名”识别（`canonical_dataset_key`），源数据整体搬家后审阅结果仍能对上；多个 sidecar 对应同一包时取审阅数最多的那个。

## 2. 部署与回滚

当前生产代码就是**主检出目录里 main 分支的工作区**（绑定挂载），没有镜像版本。

部署：

```bash
cd <仓库> && git status --short data-lab-platform/lerobot-qc   # 必须为空
git pull --ff-only origin main
docker restart data-lab-lerobot-qc-1     # 只改了 static/ 下的前端文件时可以不重启
curl -fsS http://10.10.10.34:8080/qc/ -o /dev/null && echo up
```

- 重启前先看是否有重建在跑（见第 4 节）。正在跑的重建会在重启后自动续跑，但会多花时间。
- 前端文件带 `?v=<mtime>`，浏览器刷新即可拿到新版本。
- 回滚：`git checkout <上一个已知良好的提交> -- data-lab-platform/lerobot-qc/backend` 后重启，事后再回到 main。可回滚的正式部署方式见清单 D1。
- 只有改了 `requirements.txt` 或 Dockerfile 时才需要重建镜像：`docker-compose ... build lerobot-qc && docker-compose ... up -d lerobot-qc`（compose 需要同时带上基础 compose 文件）。

## 3. 配置（环境变量）

| 变量 | 默认 | 作用 |
|---|---|---|
| `LEROBOT_QC_SIDECAR` | `/data/qc-sidecar` | sidecar 根目录 |
| `LEROBOT_QC_DELIVERY` | `/data/qc-delivery` | 交付集根目录 |
| `LEROBOT_QC_CACHE` | `/tmp/lerobot_qc_cache` | 缓存目录 |
| `LEROBOT_QC_COLLECTIONS_REGISTRY` | 空 | 数据集合注册表文件 |
| `LEROBOT_QC_COLLECTION_ALLOWLIST` | 空（全部） | 逗号分隔，只显示这些集合 |
| `LEROBOT_QC_COLLECTION_CACHE_TTL` | `600` | 集合包列表缓存秒数 |
| `LEROBOT_QC_DATASETS` | — | 本地数据集根目录 |
| `LEROBOT_QC_HOST_MEDIA` / `..._HOST_PREFIX` | `/host-media` / `/media/user01` | 宿主机路径与容器路径互换 |
| `LEROBOT_QC_STORAGE_CONFIG` | `/data/qc-sidecar/storage.json` | 存储（外接盘）配置 |
| `LEROBOT_QC_MAX_SESSIONS` / `LEROBOT_QC_SESSION_TTL` | `64` / `7200` | 同时打开的数据集会话上限 / 闲置过期秒数 |
| `LEROBOT_QC_EPISODE_PAGE_SIZE` | `200` | episode 列表分页大小 |
| `LEROBOT_QC_OPERATOR` | `anonymous` | 页面未填审阅人时的默认身份 |
| `LEROBOT_QC_REBUILD_MODE` | `minimal` | `minimal`（选择性重编码，可续跑）或 `legacy` |
| `LEROBOT_QC_RESUME_REBUILDS` | `1` | `0` 时重启后不续跑中断的重建 |
| `LEROBOT_QC_MAX_RESUME_ATTEMPTS` | `3` | 同一任务最多续跑次数，超过即标记失败 |
| `LEROBOT_QC_ACCEPTANCE` | `1` | `0` 时重建后跳过自动验收 |
| `LEROBOT_QC_ACCEPTANCE_DECODE_TIMEOUT` | `3600` | 验收时单个视频文件解码超时秒数 |

## 4. 重建与验收

- 页面“交付集重建”发起；输出到 `/data/qc-delivery/<包名>_v<日期>[ _n]`。阶段：复制 → 修补数据 → 处理视频 → 修补 episodes → 重算统计 → 拆分深度特征 → `verifying`（自动验收）。
- 进行中的任务：输出目录里有 `.qc_rebuild_job.json`（任务标记）。标记存在 = 未完成，不要拿去交付。
- 查看是否有任务在跑：

```bash
docker exec data-lab-lerobot-qc-1 sh -c 'grep -l "\"status\": \"running\"" /data/qc-sidecar/*/qc_manifest.json'
```

- 自动验收结果在审阅页交付状态后、总览页交付列徽章上；未通过时鼠标悬停可看问题列表（最多 20 条）。验收耗时约 16 ms/帧（2.7 万帧约 7 分钟）。
- 验收未通过不会让重建失败，但**该交付集不应交付**。按问题类型处理：
  - `episodes/frames != expected`、`task text mismatches`：重建期间审阅被改动过 → 重新重建。
  - `decodes as yuv420p, expected 16-bit`：源 info.json 的深度特征没有声明 `video.codec`/`video.pix_fmt`，被按 8 位重编码 → 补全源元数据后重建（清单 A4 会修复兜底）。
  - `missing` / `decode failed`：视频文件缺失或损坏 → 检查磁盘后重建。

## 5. 运维报告（只读）

`tools/qc_ops_report.py`，在容器内运行；`--json <文件>` 另存完整报告。

```bash
# sidecar 健康：路径失效、重复 sidecar（含被隐藏的审阅数）、空 sidecar、卡住的任务、输出已删除的任务
docker exec -w /app/backend data-lab-lerobot-qc-1 python tools/qc_ops_report.py sidecars
# 对每个包最新的现存交付集跑验收，并列出没有任务引用的交付目录；有失败时退出码 1
docker exec -w /app/backend data-lab-lerobot-qc-1 python tools/qc_ops_report.py deliveries [--package 681448]
```

`deliveries` 全量跑较慢（每个包数分钟），建议后台运行。

## 6. 故障处理

| 现象 | 处理 |
|---|---|
| 页面 502/打不开 | `docker ps` 看容器状态；`docker logs --tail 100 data-lab-lerobot-qc-1`；`docker restart data-lab-lerobot-qc-1` |
| 重建失败，错误 “Service restarted …; gave up after 3 resume attempts” | 连续重启打断了任务；确认服务稳定后重新发起 |
| 重建失败，“Output directory exists but is not owned by job” | 目标目录被别的任务或手工占用；核对后由负责人决定删除或改批次号 |
| 打开包报找不到数据 | 外接盘未挂载或路径变化：`ls /media/user01/BookDuo_2`；必要时在存储设置里重新激活 |
| 总览显示“未开始”但实际审过 | 跑 `qc_ops_report.py sidecars`，看是否有重复 sidecar 隐藏了审阅（`hidden_reviews`） |
| 磁盘紧张 | 缓存目录可直接清空；交付集按保留策略清理（清单 C2），删除前确认没有被交付方使用 |

## 7. 备份与恢复

审阅结果只存在 sidecar 目录里。正式备份方案待定（清单 C1），在那之前至少在每次大批量审阅后手工打包：

```bash
cd <仓库>/data-storage/lerobot-qc && tar czf /path/to/backup/qc-sidecar-$(date +%Y%m%d-%H%M).tgz sidecar
```

恢复：停容器 → 将备份解压覆盖 `sidecar/` → 启动容器 → 跑 `qc_ops_report.py sidecars` 核对。
