# Data Lab & SAM 服务维护（重启 / 异常退出）

工作目录（以下命令均在此执行）：

```bash
cd /media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/data-lab
```

Compose 叠加文件（与当前运行容器一致）：

```bash
COMPOSE="docker compose \
  -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml"
```

| 组件 | 地址 | 说明 |
|------|------|------|
| Data Lab Web | http://10.10.10.34:8080 | nginx + app + db（Docker） |
| SAM ML Backend | http://10.10.10.34:9090 | 宿主机 GPU 进程；**systemd 开机自启**（见下文） |
| LeRobot Annotate | http://10.10.10.34:8080/lerobot-annotate/ | 宿主机 :7861；nginx 反代；**systemd 开机自启**（见下文） |
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

# LeRobot Annotate（期望 200）
curl -s -o /dev/null -w "annotate %{http_code}\n" http://127.0.0.1:7861/
curl -s -o /dev/null -w "annotate-proxy %{http_code}\n" http://127.0.0.1:8080/lerobot-annotate/

# ML Backend 连通（期望 state: CO）
curl -s -H "Authorization: Token <YOUR_TOKEN>" http://127.0.0.1:8080/api/ml/1 \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('state'), d.get('url'))"
```

**典型现象：** Docker 栈靠 `restart: unless-stopped` 随 `docker.service` 自启；SAM 靠 **systemd `data-lab-sam-backend.service`** 在 Docker / GPU 就绪后自启。若 ML Backend 仍 `Disconnected`，见下方排错。

---

## 一次性：启用 LeRobot Annotate 开机自启

具身标注 iframe 依赖宿主机 **:7861**（nginx 转发 `/lerobot-annotate/`）。未启动时具身项目页为蓝屏。

**推荐（系统级，重启后无需登录）：**

```bash
cd /media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/data-lab
chmod +x data-lab-platform/install-lerobot-annotate-systemd.sh
sudo bash data-lab-platform/install-lerobot-annotate-systemd.sh
```

**备选（用户级）：**

```bash
bash data-lab-platform/install-lerobot-annotate-systemd.sh --user
sudo loginctl enable-linger $(whoami)   # 开机未登录时也启动 user systemd
```

验证：

```bash
systemctl --user is-enabled data-lab-lerobot-annotate   # 或 systemctl is-enabled（系统级）
systemctl --user status data-lab-lerobot-annotate
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:7861/
journalctl --user -u data-lab-lerobot-annotate -n 30 --no-pager
```

手动启停：

```bash
bash data-lab-platform/lerobot-annotate/start-lerobot-annotate.sh
bash data-lab-platform/lerobot-annotate/stop-lerobot-annotate.sh
```

---

## 一次性：启用 SAM 开机自启

**推荐（系统级，重启后无需登录）：**

```bash
cd /media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/data-lab
chmod +x ml-services/install-sam-systemd.sh
sudo bash ml-services/install-sam-systemd.sh
```

**备选（用户级，无需 sudo 安装单元）：**

```bash
bash ml-services/install-sam-systemd.sh --user
sudo loginctl enable-linger $(whoami)   # 开机未登录时也启动 user systemd
```

验证：

```bash
# 系统级
systemctl is-enabled data-lab-sam-backend
systemctl status data-lab-sam-backend

# 用户级
systemctl --user is-enabled data-lab-sam-backend
systemctl --user status data-lab-sam-backend

curl -s http://127.0.0.1:9090/health
journalctl -u data-lab-sam-backend -n 30 --no-pager
# 用户级日志: journalctl --user -u data-lab-sam-backend -f
```

卸载自启：

```bash
sudo systemctl disable --now data-lab-sam-backend
sudo rm -f /etc/systemd/system/data-lab-sam-backend.service
sudo systemctl daemon-reload

# 或用户级
systemctl --user disable --now data-lab-sam-backend
rm -f ~/.config/systemd/user/data-lab-sam-backend.service
systemctl --user daemon-reload
```

---

## 电脑重启后：最小启动顺序

重启后 **通常无需手工操作**。若 Docker 或 SAM 未起来：

```bash
cd /media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/data-lab

# 1) Docker 栈（容器已配置 unless-stopped，一般会自动起来）
$COMPOSE up -d

# 2) SAM（已 enable systemd 时自动启动；否则手动）
sudo systemctl start data-lab-sam-backend
# 或: bash ml-services/start-sam-backend.sh

# 2b) LeRobot Annotate（已 enable systemd 时自动启动；否则手动）
systemctl --user start data-lab-lerobot-annotate
# 或: bash data-lab-platform/lerobot-annotate/start-lerobot-annotate.sh

# 3) 验证（见上一节）
```

---

## 停止服务

```bash
cd /media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/data-lab

# 停止 SAM（宿主机进程）
sudo systemctl stop data-lab-sam-backend
# 或: bash ml-services/stop-sam-backend.sh

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
sudo systemctl restart data-lab-sam-backend
# 或: bash ml-services/stop-sam-backend.sh && bash ml-services/start-sam-backend.sh
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

## 前端构建后热更新（不改镜像时）

在 `node` 容器或本机 build 后拷贝进运行中的容器：

```bash
# docker exec -it data-lab-node-1 bash  # 若有 node 容器
# yarn nx run labelstudio:build:production --skip-nx-cache

docker cp web/dist/. data-lab-nginx-1:/label-studio/web/dist/
docker cp web/dist/. data-lab-app-1:/label-studio/web/dist/
```

---

## 相关文件

| 用途 | 路径 |
|------|------|
| SAM 启动 | `ml-services/start-sam-backend.sh` |
| SAM 停止 | `ml-services/stop-sam-backend.sh` |
| SAM systemd 单元 | `ml-services/systemd/data-lab-sam-backend.service` |
| SAM 自启安装 | `sudo bash ml-services/install-sam-systemd.sh` |
| LeRobot Annotate 启停 | `data-lab-platform/lerobot-annotate/start-lerobot-annotate.sh` |
| LeRobot Annotate systemd | `data-lab-platform/systemd/data-lab-lerobot-annotate.service` |
| LeRobot Annotate 自启安装 | `sudo bash data-lab-platform/install-lerobot-annotate-systemd.sh` |
| SAM 环境变量 | `ml-services/.env.sam`（从 `.env.sam.example` 复制） |
| SAM 日志 / pid | `ml-services/sam-backend.log`、`ml-services/sam-backend.pid` |
| 平台 Compose | `data-lab-platform/docker-compose.platform.yml` |
| 存储侧车 Compose | `data-lab-platform/docker-compose.storage.override.yml` |

---

## 已知注意点

- **stream-ingest** 可能显示 `unhealthy`，与 SAM / 标注主链路无关；需单独查 ingest 日志：`docker logs data-lab-stream-ingest-1 --tail 50`
- SAM 首次启动需 GPU（`DEVICE=cuda`）及 checkpoint：`ml-services/sam2/checkpoints/sam2.1_hiera_large.pt`
- ML Backend 在 Label Studio 中注册的 URL 需为宿主机可达地址（当前为 `http://10.10.10.34:9090`）
