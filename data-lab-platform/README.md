# Data Lab 平台叠加层

在 **Label Studio** 之上以「零侵入叠加」方式扩展：**不修改** Label Studio 核心业务代码（仅通过 nginx 注入前端壳层、独立静态页与独立 `lerobot` 服务）。统一入口为同一域名下的路径，例如 `http://10.10.10.34:8080`。

## 功能概览

| 能力 | 路径 | 说明 |
|------|------|------|
| 数据可视化 | `/data` | Label Studio SPA（`DataVizPage`）+ 与主页相同顶栏/侧栏；内容区 iframe 嵌入 `/lerobot/?datalab_embed=1` |
| 采集站 | `/collection` | EGO 采集站列表与预览占位（API 可扩展） |
| 侧栏菜单 | 全站 SPA | 注入「数据」「采集」，与 LS 原生「标注」等菜单并存 |
| 内置样例 | `/data` 卡片 | 4 个 LeRobot v3 官方样例数据集，与 io-ai.tech 展示一致 |

Label Studio 项目内的任务数据仍在 `/projects/:id/data`，**未改动**。

## 架构

```
浏览器
  └─ nginx（data-lab-platform/gateway/nginx）
        ├─ /、/projects/…     → Label Studio（app）
        ├─ /_datalab/*        → client/（shell.js、data.html、collection.html）
        ├─ /data              → DataVizPage（Menubar + iframe → /lerobot/?datalab_embed=1）
        ├─ /collection        → collection.html
        └─ /lerobot/*         → lerobot 容器（Node server.mjs + 静态资源 + bundled 卷）

仓库 data-storage/samples/  ──挂载──►  /datalab-samples（只读）
ingest 脚本拷贝 ──►  Docker 卷 lerobot-bundled-data ──►  /lerobot/bundled/*
```

**样例数据两阶段：**

1. **源文件**（跟仓库走）：`data-storage/samples/*.zip`  
2. **运行时**（Docker 卷）：ingest 后由 `/lerobot/bundled/*.zip` 对外提供；换机需重新 ingest 或一并迁移卷（见下文）。

## 目录结构

```
data-lab/                          # 仓库根（compose 在此执行）
├── data-storage/
│   └── samples/                   # 四个官方 zip（大文件已 gitignore；说明见仓库根 README.md）
├── docker-compose.yml
├── data-lab-platform/
│   ├── README.md                  # 本文件
│   ├── docker-compose.platform.yml
│   ├── deploy-local-datasets.sh   # 推荐：样例 ingest + 同步配置/branding
│   ├── deploy-lerobot-studio.sh   # 仅构建/启动 lerobot + nginx
│   ├── client/
│   │   ├── shell.js / shell.css   # 侧栏注入（当前 v11）
│   │   ├── data.html / data-page.js（已废弃，/data 由 SPA 接管）
│   │   └── collection.html / collection-page.js / collection-page.css
│   ├── gateway/nginx/
│   └── lerobot-studio/
│       ├── server.mjs             # 静态资源、manifest、bundled、branding 注入
│       ├── ingest-bundled-datasets.sh
│       ├── branding/overlay.js|css   # 运行时文案与 chrome 修正（v12）
│       ├── patches/apply-branding.sh # 对上游 JS 的 sed 补丁
│       └── config/
│           ├── sample-datasets.manifest.json
│           ├── datasets.json
│           └── collection-stations.json
```

## 环境要求

- Docker、Docker Compose（v1 `docker-compose` 或 v2 `docker compose`）
- 从**仓库根目录**执行 compose（保证 `./data-storage/samples` 相对路径正确）
- 可选环境变量：`LABEL_STUDIO_HOST`（默认 `http://10.10.10.34:8080`）

## 首次部署

### 1. 准备样例 zip

将下列文件放入 `data-storage/samples/`（**文件名需完全一致**），详见仓库根 [README.md](../README.md#样例数据目录-data-storage)：

| 文件名 |
|--------|
| `SenseXperience Ego.zip` |
| `SenseXperience UMI.zip` |
| `DualAirbot Folding.zip` |
| `DualPiper Pulling.zip` |

若无 `DualPiper Pulling.zip`，可放 `DualPiper Pulling.tar`，或在 ingest 时由脚本从官方 COS 拉取 `.tar`。

`/data` 由 Django 返回 SPA 壳（`base.html`）+ React `DataVizPage`，不再使用独立 `data.html`。

### 2. 启动全栈

```bash
export LABEL_STUDIO_HOST=http://10.10.10.34:8080   # 按实际访问地址修改

docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml build lerobot
docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml up -d
```

首次启动 `lerobot` 会从上游下载可视化器静态资源（数分钟），可查看日志：

```bash
docker logs -f data-lab-lerobot-1
```

### 3. 导入样例并同步配置（推荐一步完成）

```bash
bash data-lab-platform/deploy-local-datasets.sh
```

该脚本会：

- 使用 `./data-storage/samples` 挂载重建 `lerobot`
- 将 manifest / datasets / collection-stations、branding、`server.mjs` 同步进容器
- 执行 `apply-branding.sh` 与 `ingest-bundled-datasets.sh`
- 重启 `lerobot` 与 `nginx`

## 换机 / 迁移

**推荐做法：拷贝整个 `data-lab` 目录**（必须包含 `data-storage/samples/` 下四个 zip，约 500MB；大文件未纳入 git，需自行 rsync/scp）。

在新机器上：

```bash
cd /path/to/data-lab

export LABEL_STUDIO_HOST=http://<新机器IP或域名>:8080

docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml up -d
bash data-lab-platform/deploy-local-datasets.sh
```

说明：

- **不要依赖** `~/Downloads`；样例源路径已固定为仓库内 `data-storage/samples/`。
- Docker 命名卷 `lerobot-bundled-data`、`lerobot-studio-data` **默认不随文件夹复制**；在新环境执行 `deploy-local-datasets.sh` 会从 `samples/` 重新 ingest 到 bundled 卷。
- 若需保留已 ingest 的卷以加快启动，可额外导出/导入 Docker volume（可选，非必须）。

## 常用地址

| URL | 用途 |
|-----|------|
| `http://10.10.10.34:8080/` | Label Studio 首页 |
| `http://10.10.10.34:8080/data` | 数据可视化（嵌入 LeRobot） |
| `http://10.10.10.34:8080/collection` | 采集站列表 |
| `http://10.10.10.34:8080/lerobot/` | LeRobot 可视化器直连 |
| `http://10.10.10.34:8080/lerobot/sample-datasets.manifest.json` | 样例 manifest API |
| `/lerobot-data` | 301 → `/data`（已废弃，保留兼容） |

## 四个内置样例数据集

| ID | 展示名 | bundled 路径 |
|----|--------|----------------|
| `sensexperience_ego` | SenseXperience Ego | `/lerobot/bundled/sensexperience_ego.zip` |
| `sensexperience_umi` | SenseXperience UMI | `/lerobot/bundled/sensexperience_umi.zip` |
| `dualairbot_fold` | DualAirbot Folding | `/lerobot/bundled/dualairbot_fold.zip` |
| `dualpiper_pulling` | DualPiper Pulling | `/lerobot/bundled/dualpiper_pulling.zip` |

卡片封面为与 io-ai.tech 相同的静态 `.webp`（ingest 时下载到 `bundled/covers/`）。点击卡片以 `?url=sample://<id>` 打开多相机预览。

配置修改位置：

- 列表与 URL：`lerobot-studio/config/sample-datasets.manifest.json`、`datasets.json`
- 修改后执行 `bash data-lab-platform/deploy-local-datasets.sh` 或仅 `docker cp` 配置后重启 `lerobot`

## 品牌与界面定制

- **左下角品牌**：显示 **Data Lab**，链接为站点根路径 `/`（非外链 `https://`）
- **实现**：`patches/apply-branding.sh` 补丁上游 bundle + `branding/overlay.js`（v12）运行时修正
- **嵌入模式**（`/data` iframe）：`datalab_embed=1`，隐藏部分语言切换与外链菜单项；**保留**左下角品牌区

仅改 nginx / `client/` 时：

```bash
docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml restart nginx
```

改 `lerobot-studio/branding/` 或 `server.mjs` 后，请走 `deploy-local-datasets.sh`，避免只 `recreate` 容器而丢失容器内同步文件。

## 日常运维

| 场景 | 命令 |
|------|------|
| 更新样例 zip 后重新导入 | `bash data-lab-platform/deploy-local-datasets.sh` |
| 仅重建 lerobot 镜像并启动 | `bash data-lab-platform/deploy-lerobot-studio.sh` |
| 查看 shell 是否注入 | 浏览器控制台：`window.__DATALAB_SHELL_VERSION__` → `"11"` |
| 验证 manifest | `curl http://<host>:8080/lerobot/sample-datasets.manifest.json` |

## 设计原则

1. **叠加而非 fork**：平台逻辑集中在 `data-lab-platform/` 与 `data-storage/`。  
2. **样例与代码同仓路径**：`data-storage/samples` 相对挂载，便于整机迁移。  
3. **运行时与源分离**：页面读 bundled 卷；换机后执行 deploy 即可恢复。  
4. **recreate 容器时注意**：镜像内可能是旧版 branding；务必用 `deploy-local-datasets.sh` 同步 `branding/`、`server.mjs` 与配置。

## 上游与许可

- 可视化器静态资源来自 [io-ai.tech/lerobot](https://io-ai.tech/lerobot/)，首次启动自动下载到卷 `lerobot-studio-data`。  
- Label Studio 部分遵循原仓库许可与文档；Data Lab 叠加层文档以本目录为准。
