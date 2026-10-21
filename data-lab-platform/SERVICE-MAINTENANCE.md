# Data Lab & SAM 服务维护（重启 / 异常退出）

工作目录（以下命令均在此执行）：

```bash
cd /media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/data-lab
```

Compose 叠加文件（EgoDome 生产栈，与当前运行容器一致）：

```bash
# 生产默认值见 scripts/ego-production-defaults.sh（当前 v0.1.1）
source data-lab-platform/scripts/ego-production-defaults.sh

COMPOSE="docker compose \
  -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  -f ${EGO_COMPOSE_OVERLAY}"

# async derive 模式需再加 async overlay（或读 .ego-derive-mode）：
# COMPOSE="$COMPOSE -f ${EGO_COMPOSE_OVERLAY_ASYNC}"
```

**注意：** 裸跑 `$COMPOSE up -d` 不带 overlay 会回退到 platform 默认（`:local` 镜像）。  
生产部署请用：`bash data-lab-platform/scripts/deploy-stream-ingest-v0.1.1-async.sh`

| 组件 | 地址 | 说明 |
|------|------|------|
| Data Lab Web | http://10.10.10.34:8080 | nginx + app + db（Docker） |
| SAM ML Backend | http://10.10.10.34:9090 | 宿主机 GPU 进程；**默认不启动**（按需 `sam-backendctl start`） |
| LeRobot Annotate | http://10.10.10.34:8080/lerobot-annotate/ | Docker `embodied-annotate`；nginx 反代；`restart: unless-stopped` |
| LeRobot / Stream | Docker 侧车 | stream-ingest、lerobot、stream-parquet-sync |

---

## 重启后快速检查

```bash
# Docker 容器
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep data-lab

# Data Lab HTTP（期望 302）
curl -s -o /dev/null -w "LS %{http_code}\n" http://127.0.0.1:8080/

# SAM 健康（期望 {"status":"UP",...}）
curl -s http://127.0.0.1:9090/health

# LeRobot Annotate（期望 200，经 nginx）
curl -s -o /dev/null -w "annotate-proxy %{http_code}\n" http://127.0.0.1:8080/lerobot-annotate/

# ML Backend 连通（期望 state: CO）
curl -s -H "Authorization: Token <YOUR_TOKEN>" http://127.0.0.1:8080/api/ml/1 \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('state'), d.get('url'))"
```

**典型现象：** Docker 栈靠 `restart: unless-stopped` 随 `docker.service` 自启（含 `embodied-annotate`）；**SAM 默认不占 GPU**，标注前需手工启动（见「SAM 按需启停」）。若 ML Backend 仍 `Disconnected`，见下方排错。

---

## embodied-annotate（Compose 运维）

```bash
# 构建
$COMPOSE build embodied-annotate

# 启动 / 重启
$COMPOSE up -d embodied-annotate nginx

# 日志
docker logs -f data-lab-embodied-annotate-1

# 健康
curl -fsS http://10.10.10.34:8080/lerobot-annotate/ >/dev/null && echo OK
```

源码与数据路径：

| 用途 | 路径 |
|------|------|
| 应用源码 | `data-lab-platform/embodied-annotate/` |
| 运行时数据 | `data-storage/embodied-annotate/{cache,exports,datasets}` |
| 容器内数据集路径 | `/data/datasets/pusht` |
| Nginx snippet | `data-lab-platform/gateway/nginx/snippets/datalab-lerobot-annotate.conf`（含 `sub_filter` 将默认数据集路径改写为 `/data/datasets/pusht`） |

---

## 一次性：EgoDome process-watcher 开机自启（34）

130 使用 `ego-upload ego-001`（默认含 process-notify）时，34 需 timer 消费 notify marker：

```bash
sudo bash data-lab-platform/scripts/install-ego-process-watcher-systemd.sh
systemctl status data-lab-ego-process-watcher.timer
journalctl -u data-lab-ego-process-watcher.service -n 20 --no-pager
```

同时启用开机 5 分钟后生产 preflight（`rc-ego-001-production.sh --boot-check`）。

---

## SAM 按需启停（默认不占 GPU）

生产策略：**重启后不自动加载 SAM 模型**；需要智能标注时再启动，用完可停止以释放显存（与 vLLM 等同卡共存时尤其重要）。

```bash
cd /media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/data-lab
chmod +x ml-services/sam-backendctl.sh

# 查看状态 / 健康 / GPU
bash ml-services/sam-backendctl.sh status

# 需要标注时启动（优先 systemd；无单元时用 start-sam-backend.sh）
bash ml-services/sam-backendctl.sh start
curl -s http://127.0.0.1:9090/health

# 标注结束，释放 GPU
bash ml-services/sam-backendctl.sh stop

# 关闭开机自启并停止（推荐长期与训练/推理共用 GPU 的机器）
bash ml-services/sam-backendctl.sh disable-boot
```

安装 / 同步 systemd 单元（**仅注册服务，默认不 enable、不 start**）：

```bash
sudo bash ml-services/apply-sam-systemd-policy.sh
# 或首次安装：
sudo bash ml-services/install-sam-systemd.sh
# 若确需开机自启（少见）：
sudo bash ml-services/install-sam-systemd.sh --enable-boot --start
```

用户级单元（无 sudo 写 `/etc`）：

```bash
bash ml-services/install-sam-systemd.sh --user
bash ml-services/sam-backendctl.sh start
```

日志：

```bash
journalctl -u data-lab-sam-backend -n 30 --no-pager
tail -f ml-services/sam-backend.log
```

重启后仍保持「不占 GPU」（推荐一次性安装用户级开机钩子，无需 sudo）：

```bash
bash ml-services/install-sam-policy-boot-hook.sh
# 若机器重启后无人登录也要生效：
sudo loginctl enable-linger $(whoami)
```

同步 `/etc` 下 SAM 单元（优雅停止参数等，需 sudo 一次）：

```bash
sudo bash ml-services/apply-sam-systemd-policy.sh
```

完全移除 systemd 单元：

```bash
bash ml-services/sam-backendctl.sh disable-boot
sudo rm -f /etc/systemd/system/data-lab-sam-backend.service
sudo systemctl daemon-reload
```

---

## 电脑重启后：最小启动顺序

重启后 **通常无需手工操作**。若 Docker 或 SAM 未起来：

```bash
cd /media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/data-lab

# 1) Docker 栈（容器已配置 unless-stopped，一般会自动起来）
#    若需手工拉起生产栈（勿省略 overlay）：
bash data-lab-platform/scripts/deploy-stream-ingest-v0.1.1-async.sh
# 或仅确认已在跑：
bash data-lab-platform/scripts/rc-ego-001-v0.1.1-preflight.sh

# 2) SAM（仅在做标注时需要）
bash ml-services/sam-backendctl.sh start

# 3) 验证（见上一节）
```

---

## 停止服务

```bash
cd /media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/data-lab

# 停止 SAM（释放 GPU）
bash ml-services/sam-backendctl.sh stop

# 停止 Data Lab Docker 栈（保留数据卷）
$COMPOSE down

# 仅停某个容器（示例）
docker stop data-lab-app-1
```

---

## 重启 / 恢复（异常退出后）

```bash
cd /media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/data-lab

# --- SAM：进程挂了但 pid 文件还在时，先停再启 ---
bash ml-services/sam-backendctl.sh restart
tail -f ml-services/sam-backend.log   # 排错
journalctl -u data-lab-sam-backend -f

# --- Data Lab：整栈重建 ---
$COMPOSE down
$COMPOSE up -d

# --- 仅重启后端（改 Python 后未生效时）---
docker restart data-lab-app-1

# --- 仅重启前端静态资源所在 nginx ---
docker restart data-lab-nginx-1
```

---

## 前端构建后热更新（不改镜像时 · **日常推荐**）

全量 `deploy-label-studio-image.sh` 容易因 yarn lockfile / 权限失败；**改 React 前端请用热部署**：

```bash
# 一键：Node 容器内编译 + docker cp 进 app/nginx（推荐）
bash data-lab-platform/deploy-label-studio-frontend-hot.sh
```

或手动：

```bash
# docker exec -it data-lab-node-1 bash  # 若有 node 容器
# yarn nx run labelstudio:build:production --skip-nx-cache

docker cp web/dist/. data-lab-nginx-1:/label-studio/web/dist/
docker cp web/dist/. data-lab-app-1:/label-studio/web/dist/
```

| 改动类型 | 部署方式 |
|----------|----------|
| `web/` React 前端（具身 embed 等） | **热部署脚本** 或 `docker cp` |
| `embodied-annotate/backend` Python/静态 | 卷挂载已 ro，**`docker restart data-lab-embodied-annotate-1`** |
| 镜像 / Python 依赖大改 | 才用 `deploy-label-studio-image.sh`（易失败，非日常） |

---

## 相关文件

| 用途 | 路径 |
|------|------|
| SAM 运维入口 | `ml-services/sam-backendctl.sh` |
| SAM 启动（底层） | `ml-services/start-sam-backend.sh` |
| SAM 停止（底层） | `ml-services/stop-sam-backend.sh` |
| SAM systemd 单元 | `ml-services/systemd/data-lab-sam-backend.service` |
| SAM 安装单元 | `sudo bash ml-services/install-sam-systemd.sh` |
| SAM 同步策略到 systemd | `sudo bash ml-services/apply-sam-systemd-policy.sh` |
| SAM 开机策略（preflight） | `ml-services/ensure-sam-policy.sh`（`SAM_BOOT_AUTOSTART≠1` 时 disable+stop） |
| SAM 重启后策略钩子 | `bash ml-services/install-sam-policy-boot-hook.sh`（用户 systemd，已含 disable 双单元） |
| embodied-annotate Dockerfile | `data-lab-platform/embodied-annotate/Dockerfile` |
| SAM 环境变量 | `ml-services/.env.sam`（从 `.env.sam.example` 复制） |
| SAM 日志 / pid | `ml-services/sam-backend.log`、`ml-services/sam-backend.pid` |
| 平台 Compose 生产默认 | `data-lab-platform/scripts/ego-production-defaults.sh` |
| Ego process-watcher systemd | `data-lab-platform/systemd/data-lab-ego-process-watcher.*` |
| Ego watcher 安装 | `sudo bash data-lab-platform/scripts/install-ego-process-watcher-systemd.sh` |
| Ego 生产验收 | `bash data-lab-platform/scripts/rc-ego-001-production.sh` |
| 旧镜像 prune | `bash data-lab-platform/scripts/ego-prune-old-images.sh`（默认 dry-run） |
| `.bak` 清理 | `bash data-lab-platform/scripts/ego-cleanup-bak-dirs.sh`（7 天 retention） |
| 平台 Compose base | `data-lab-platform/docker-compose.platform.yml` |
| 存储侧车 Compose | `data-lab-platform/docker-compose.storage.override.yml` |

---

## 已知注意点

- **stream-ingest** 可能显示 `unhealthy`，与 SAM / 标注主链路无关；需单独查 ingest 日志：`docker logs data-lab-stream-ingest-1 --tail 50`
- SAM 首次启动需 GPU（`DEVICE=cuda`）及 checkpoint：`ml-services/sam2/checkpoints/sam2.1_hiera_large.pt`
- ML Backend 在 Label Studio 中注册的 URL 需为宿主机可达地址（当前为 `http://10.10.10.34:9090`）
