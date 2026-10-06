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
| 相机拓扑 | `camera_topology_ego-002` **v2**（与 ego-001 布线不同，见 §5） |

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

bash data-lab-platform/scripts/ego-130-provision-hotspot-watchdog.sh "${HOST}"

# B. MCAP 采集栈 + preview-standby（含 PYTHONPATH drop-in）
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

`ego-130-provision.sh --profile mcap-production` 会安装 `ecs-preview-standby` 与  
`ecs-preview-standby.service.d/z-pythonpath.conf`（venv 未 `pip install -e` 时必需）。

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

## 3. 相机拓扑（四路朝向 · v2）

**SSOT（边缘 + 34 须一致）：**

- `data-lab-platform/ego-stream-client/config/camera_topology_ego-002.{yaml,json}`
- `data-lab-platform/lerobot-studio/config/topologies/camera_topology_ego-002.json`
- 边缘路径：`EGO_TOPOLOGY_FILE=~/workspace/ego-studio/config/camera_topology_ego-002.yaml`（由 MCAP profile / provision 同步）

8081 格子名（前左/前右/后左/后右）固定；**画面来自哪颗物理相机**由 `socket_to_role` 决定（与 `EGO_CAMERA_OPTICS` 无关）。

**ego-002 已标定映射（`topology_id: ego-002-physical`, version 2）：**

| OAK 插座 | 语义角色 | LeRobot 字段 |
|----------|----------|----------------|
| CAM_A | rear_left | `observation.images.camera_rear_left` |
| CAM_B | front_right | `observation.images.camera_front_right` |
| CAM_C | rear_right | `observation.images.camera_rear_right` |
| CAM_D | front_left | `observation.images.camera_front_left` |

`primary_socket: CAM_D`，`head_right_socket: CAM_B`。

**与 ego-001 对比（勿复制 001 的 yaml）：**

| 插座 | ego-001 | ego-002 (v2) |
|------|---------|--------------|
| CAM_A | front_left | rear_left |
| CAM_B | rear_left | front_right |
| CAM_C | rear_right | rear_right |
| CAM_D | front_right | front_left |

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

## 5. 2026-10-06 现场备注

- 52 曾只装 8080，**未装 8081**，导致手机打不开四路页；已补 staging + preview PYTHONPATH。  
- 四路标签错位：曾误用 ego-001 的 `socket_to_role`；已标定为拓扑 **v2**（§3）。  
- 内核/apt 钉扎、Tenda udev MAC 等仍见迁移清单 §4。
