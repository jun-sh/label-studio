# ego-002 边缘采集站部署（52 · 迁移 003/004 模板）

| 项 | 值 |
|----|-----|
| 站点 | `ego-002` |
| 有线 IP | `10.10.11.52` |
| 手机热点 SSID | `EGO-002-COLLECT` |
| 热点网关 | `192.168.8.1` |
| OAK USB | `3-4` |
| USB 热点 | Tenda AIC8800DC，`EGO_USB_WIFI_MAC=c8:3a:35:94:90:58` |
| 光学 | pinhole / passthrough（见 `config/station-profiles/ego-002-mcap-production.env`） |
| 相机拓扑 | `camera_topology_ego-002` **v4**（与 ego-001 布线不同，见 §3） |

**通用 GPD 系统基线**（内核钉扎、显示、apt mask）：[gpd-micropc2-capture-station-setup.md](./gpd-micropc2-capture-station-setup.md)  
**001/002 差异与 CORRUPT 根因**：[ego-gpd-station-migration-checklist.md](./ego-gpd-station-migration-checklist.md)  
**003/004**：复制本文变量表 + 迁移清单 §2–§3，换 `station-id` / IP / MAC / profile 即可。

---

## 1. 推荐部署顺序（开发机 → 52）

```bash
cd /path/to/data-lab
STATION=ego-002
HOST=server@10.10.11.52

# A. 系统基线（在 52 本机按 gpd 手册 §1–§5；Tenda 见 §6.1）
EGO_OAK_USB_DEV=3-4 bash data-lab-platform/scripts/ego-130-provision-tenda-usb.sh "${HOST}"

EGO_HOTSPOT_CONN=EGO-002-COLLECT \
EGO_USB_WIFI_MAC=c8:3a:35:94:90:58 \
bash data-lab-platform/scripts/ego-130-provision-usb-hotspot.sh "${HOST}" wlp_usb_ap

EGO_HOTSPOT_CONN=EGO-002-COLLECT \
bash data-lab-platform/scripts/ego-130-provision-hotspot-watchdog.sh "${HOST}"

# B. MCAP 采集栈 + preview-standby + **采集 unit PYTHONPATH**（见 §8.1）
bash data-lab-platform/scripts/ego-130-provision.sh --profile mcap-production "${HOST}" "${STATION}"

# C. 手机 Web：8080 采集 + 8081 四路预览（在 52 上执行 deploy.sh，或依赖 B 已同步 staging）
#    ssh 到 52：cd .../ego-local-web && bash deploy.sh && loginctl enable-linger server

bash data-lab-platform/scripts/ego-130-upload-mode.sh production
bash data-lab-platform/scripts/ego-130-verify-production.sh "${HOST}"
```

---

## 2. 手机四路预览（必做，易漏）

与 **8080 采集页** 是两套 user systemd 服务：

| 端口 | 单元 | 用途 |
|------|------|------|
| **8080** | `ecs-ego-web` | 启停录制；录集中**单路**预览 |
| **8081** | `ecs-ego-web-staging` | **待机四路** 2×2 页（`EGO_WEB_UI_MODE=quad`） |
| **8765** | `ecs-preview-standby` | OAK 四路 JPEG 数据源 |

**部署（52 上，`server` 用户）：**

```bash
cd /path/to/data-lab/data-lab-platform/ego-local-web
bash deploy.sh          # 含 deploy-staging.sh → 8081
loginctl enable-linger server
systemctl --user enable --now ecs-ego-web.service ecs-ego-web-staging.service
```

`ego-130-provision.sh --profile mcap-production` 会安装：

- `ecs-preview-standby.service.d/z-pythonpath.conf`
- **`ecs-record-oak-mcap.service.d/z-pythonpath.conf`**（8080 开录必需；venv 未 `pip install -e` 时）

二者均设置 `PYTHONPATH=/home/server/workspace/ego-studio/src`。

**手机验收（未开始录制前）：**

1. 连 `EGO-002-COLLECT`，浏览器打开 `http://192.168.8.1:8081/`  
2. 边缘机自检：

```bash
curl -s -o /dev/null -w '8081:%{http_code}\n' http://127.0.0.1:8081/
curl -s http://127.0.0.1:8765/preview/status    # 期望 online:true
systemctl --user is-active ecs-ego-web-staging ecs-preview-standby
```

若 8081 连不上：多半未 deploy staging。若 8081 有页无图：`journalctl --user -u ecs-preview-standby -n 20`（常见 `ModuleNotFoundError: ego_capture_studio` → 缺 `z-pythonpath.conf`）。

**采集中**：四路预览会停或仅一路更新；先看 8081 再开 8080 录制。

协议细节：`data-lab-platform/ego-local-web/docs/ego-app-tcp-protocol.md` §7。

---

## 3. 相机拓扑（四路朝向 · v4）

**SSOT（边缘 + 34 须一致）：**

- `data-lab-platform/ego-stream-client/config/camera_topology_ego-002.{yaml,json}`
- `data-lab-platform/lerobot-studio/config/topologies/camera_topology_ego-002.json`
- 边缘路径：`EGO_TOPOLOGY_FILE=~/workspace/ego-studio/config/camera_topology_ego-002.yaml`（由 MCAP profile / provision 同步）

8081 格子名（前左/前右/后左/后右）固定；**画面来自哪颗物理相机**由 `socket_to_role` 决定（与 `EGO_CAMERA_OPTICS` 无关）。

**重要：** `:8765` / 8081 由 `ecs-preview-standby` 提供。只通过 **`~/.config/ego-station.env.d/ego-topology.env`**（仅一行 `EGO_TOPOLOGY_FILE`）注入拓扑；**不要**把整个 `station.conf` 打进 standby（其中 `OAK_HW_JPEG=0` / `OAK_H264=1` 会把待机预览变成 **单路 H.264**，8081 只剩一格「前左」）。未设拓扑时退回 **`ego-standard`**。

验收：`systemctl --user is-active ecs-preview-standby`；`ss -tlnp | grep 8765`；journal 应见 `mode=mjpeg` 与 `hw_jpeg=1`，四路 `curl -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/preview/<view>/jpg` 均为 200。

**ego-002 已标定映射（`topology_id: ego-002-physical`, version 4）：**

| OAK 插座 | 语义角色 | LeRobot 字段 |
|----------|----------|----------------|
| CAM_A | rear_left | `observation.images.camera_rear_left` |
| CAM_B | front_left | `observation.images.camera_front_left` |
| CAM_C | front_right | `observation.images.camera_front_right` |
| CAM_D | rear_right | `observation.images.camera_rear_right` |

`primary_socket: CAM_B`，`head_right_socket: CAM_C`。（v4 相对 v3 交换前右/后右插座角色。）

**与 ego-001 对比（勿复制 001 的 yaml）：**

| 插座 | ego-001 | ego-002 (v4) |
|------|---------|--------------|
| CAM_A | front_left | rear_left |
| CAM_B | rear_left | front_left |
| CAM_C | rear_right | front_right |
| CAM_D | front_right | rear_right |

改拓扑后同步 52 并重启待机预览：

```bash
# 开发机
rsync -av data-lab-platform/ego-stream-client/config/camera_topology_ego-002.* \
  server@10.10.11.52:~/workspace/ego-studio/config/
ssh server@10.10.11.52 'systemctl --user restart ecs-preview-standby.service'
```

录前在 `http://192.168.8.1:8081/` 确认四格与头盔朝向一致。**改拓扑只影响之后新录的 MCAP**；已上传旧段语义不变。

003/004 标定流程见 [ego-gpd-station-migration-checklist.md](./ego-gpd-station-migration-checklist.md) §2.13。

---

## 4. 日常操作

| 操作 | 说明 |
|------|------|
| 录前看四路 | `http://192.168.8.1:8081/` |
| 录制 | `http://192.168.8.1:8080/` |
| 上传 | 52 终端：`ego-upload ego-002` |
| 34 质检 | `http://10.10.10.34:8080/collection?station=ego-002` |

---

## 5. OAK 画面亮度（002 定稿 · 003/004 见专文）

002 与 001 共用 MCAP 基线 **`OAK_AE_MAX_EXPOSURE_US=20000`**；亮度微调使用 **`OAK_AE_COMPENSATION=+1`**（曝光补偿，不写 EEPROM）。

| 项 | ego-002 定稿 |
|----|----------------|
| `OAK_AE_MAX_EXPOSURE_US` | **20000**（仅 `z-mcap-production.conf`，勿用 25000 override） |
| `OAK_AE_COMPENSATION` | **+1** |
| 边缘 drop-in | `~/.config/systemd/user/ecs-record-oak-mcap.service.d/zz-ego-002-isp.conf` **与** `ecs-preview-standby.service.d/` 下同名文件 |

完整步骤、调参反例、验收与回滚：**[ego-oak-isp-tuning.md](./ego-oak-isp-tuning.md)**。  
仓库示例：`ego-stream-client/systemd/ecs-record-oak-mcap.service.d/zz-ego-002-isp.conf.example`。

---

## 6. 2026-10-06 现场备注

- 52 曾只装 8080，**未装 8081**，导致手机打不开四路页；已补 staging + preview PYTHONPATH。  
- 四路标定迭代：v2→v3 交换前左/前右；**v4** 交换前右/后右（§3）。  
- 内核/apt 钉扎、Tenda udev MAC 等仍见迁移清单 §4。  
- **2026-10-07** OAK 亮度：`OAK_AE_COMPENSATION=+1` + AE max 20000，见 §5 与 [ego-oak-isp-tuning.md](./ego-oak-isp-tuning.md)。  
- **2026-10-07** 桌面 **Apport** 弹窗已在 52 关闭；新机/重装后按 [gpd-micropc2-capture-station-setup.md](./gpd-micropc2-capture-station-setup.md) **§6.4** 复做。  
- **2026-10-07** 系统基线：52 已钉扎 **`7.0.0-29-generic`** + apt hold/mask；Tenda **`ego-tenda-wifi-init`** + `/etc/default/ego-tenda`（`EGO_OAK_USB_DEV=3-4`）；钉扎后须 **DKMS 重编 AIC8800**（§8.2）。
- **2026-10-08** 禁用自动转屏：52 上 `iio-sensor-proxy` 此前为 `static` + `active`，已 `mask`；仅 §2.5 **不够**（仍会合盖/唤醒方向乱跳）。  
- **2026-10-11** **显示 §2 与 001 对齐**：`ego-gpd-display-stack.sh` 已装 GRUB + logind + acpi/udev/systemd（见 [gpd §2.9](./gpd-micropc2-capture-station-setup.md)）。**003/004 新机必跑同一脚本并 reboot**。验收：`grep panel_orientation /etc/default/grub`；`systemctl is-enabled gpd-fix-display-rotate.service acpid`；开盖 1280×800 横屏。

---

## 7. 34 回放 L2（与 ego-001 对齐）

采集分辨率与 001 相同：**1280×800**（OAK ISP 2/3），**不是** GPD 内屏 1920×1200。  
34 `collection` 回放读的是 `data-storage/stream/<station>/` 下的 LeRobot v3 分片（`videos/.../file-NNN.mp4`），不是边缘实时流。

### 7.1 常见坑（ego-002 曾踩）

| 现象 | 原因 | 处理 |
|------|------|------|
| 四路全黑、时长很长但 MP4 只有 0.04s | `file-000.mp4` 仍是 **viewer 黑场占位**（建站 scaffold），与 manifest 已 publish 的 episode 0 脱节 | 见 §7.2 `rebuild-view` |
| 侧栏多条都显示成 `c787807f` | `meta/tasks.parquet` 只有一条 task，`task_index` 未按 episode 展开 | `rebuild-view` 会按 manifest 重写 tasks |
| 占位 MP4 为 1920×1200 | 旧版 `defaultInfo` 误用面板分辨率写 scaffold | 已改为 800×1280；重建后 `meta/info.json` features 同步 |

**禁止**：在已有 `manifest/manifest.json` episodes 的站上再跑 `--viewer-scaffold`（会写黑场 `file-000`）。ingest 启动时的 scaffold repair 现已跳过已 publish 的站。

### 7.2 修复命令（34 上，与 ego-001 相同入口）

在 **stream-ingest 容器**或本机（`STREAM_DATA_ROOT` 指向 `data-storage/stream`）：

```bash
cd /path/to/data-lab/data-lab-platform/lerobot-studio
export STREAM_DATA_ROOT=/path/to/data-lab/data-storage/stream

# 从 derived/sess_* 硬链/复制 MP4 + 重写 data/episodes/tasks/info（推荐）
node ego-derive-run.mjs rebuild-view --station ego-002

# 仅校验 L2 分片与 manifest 是否一致
node ego-derive-run.mjs fsck --station ego-002
```

验收：

```bash
ST=ego-002
ls -la data-storage/stream/$ST/videos/observation.images.camera_front_left/chunk-000/
# file-000.mp4 应为 MB 级，ffprobe 约 1280×800，非 1993 字节黑场

curl -s "http://127.0.0.1:8080/lerobot/api/stream/$ST/" | jq '.total_episodes, .features["observation.images.camera_front_left"].shape'
```

浏览器打开 `http://10.10.10.34:8080/collection?station=ego-002`，每条 session 时长应与侧栏一致，四路有画面。

新机 **003/004** 在首次上传 derive 完成后，若回放异常，同样执行 `rebuild-view --station ego-00N`。

---

## 8. 故障速查（002 现场 · 2026-10-07）

### 8.1 手机 8080「开始采集」失败 / OAK 长期 `03e7:f63c` bootloader

| 现象 | 原因 | 处理 |
|------|------|------|
| 点录制无画面、很快失败 | `ecs-record-oak-mcap` 退出 | `journalctl --user -u ecs-record-oak-mcap -n 30` |
| `ModuleNotFoundError: No module named 'ego_capture_studio'` | 采集 unit **缺 PYTHONPATH**（仅 sync 了 `src/`、venv 未 `pip install -e .`） | 确认存在 `~/.config/systemd/user/ecs-record-oak-mcap.service.d/z-pythonpath.conf`；`systemctl --user daemon-reload`；或重跑 **§1 B** mcap provision |
| `oak_boot_from_bootloader` 同样报 No module | `z-oak-boot-pre.conf` 与采集共用同一 python 环境 | 同上 |
| 本机自测 | — | `curl -s -X POST http://127.0.0.1:8080/api/capture/start` → `success:true`；`curl -s http://127.0.0.1:8080/api/status` → `state:recording` |

**不必**依赖边缘 `pip install -e .`（易超时）；仓库 SSOT 为 **PYTHONPATH drop-in**（`ego-stream-client/systemd/ecs-record-oak-mcap.service.d/z-pythonpath.conf`）。

### 8.2 内核钉扎到 7.0.0-29 后热点 / Tenda 起不来

Tenda 驱动为 **DKMS**（`aic8800dc`），默认可能只编过旧内核（如 `7.0.0-38`）。钉扎并 reboot 到 **29** 后若 `modprobe: FATAL: Module aic_load_fw not found`：

```bash
sudo apt-get install -y linux-headers-7.0.0-29-generic
sudo dkms install aic8800dc/6.4.3.0-patched.16 -k 7.0.0-29-generic   # 或依赖 headers 的 postinst autoinstall
sudo /usr/local/bin/ego-tenda-wifi-init.sh
sudo systemctl restart ecs-ego-hotspot.service
```

确认 `/etc/default/ego-tenda` 为 **`EGO_OAK_USB_DEV=3-4`**（勿误写 `4-1`；OAK 在 Bus 3 Port 4）。

### 8.3 热点 `unknown connection EGO-001-COLLECT`

`ego-130-provision-hotspot-watchdog.sh` 默认连接名为 **001**。002 必须：

```bash
EGO_HOTSPOT_CONN=EGO-002-COLLECT bash data-lab-platform/scripts/ego-130-provision-hotspot-watchdog.sh server@10.10.11.52
```

`/etc/default/ego-hotspot` 中 `EGO_HOTSPOT_CONN=EGO-002-COLLECT` 与 Tenda MAC 一致后：`sudo systemctl restart ecs-ego-hotspot.service`。

### 8.4 `ego-upload` 提示 `seg_000001` 隔离 / `capture_ring_overflow`

与 **30Hz timeline 不合格** 不是同一类问题。看本地 manifest：

```bash
jq '.status, .integrity.issues, .timeline.ok' \
  ~/cache/ego-002/segments/sessions/<session>/segments/seg_000001/manifest.json
```

- `capture_ring_overflow:*` → USB/ingest 背压（见 [迁移清单](./ego-gpd-station-migration-checklist.md) §1）；坏段可删目录避免下次 upload 再报 `seg_000001` 误导。  
- `timeline_incoherent:*` → 真实 span 与声称 fps 偏差 >3%（上传 QC）。  
- 进度条 **`4/3 段`**：常为「已传 + 隔离跳过」计数与 pending 不一致，**不代表** sealed 了 4 段有效数据。

### 8.5 `ego-derive-status` 显示 ego-001 / 无派生

须在边缘使用已 source `ego-station.env` 的脚本，或显式 `--station ego-002`；在 34 上查询时用 `http://10.10.10.34:8080/.../ego-002/...`。
