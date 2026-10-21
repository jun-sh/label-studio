# GPD 采集站迁移必做清单（ego-003 / ego-004 / 新 002 修复）

对照 **ego-001（10.10.10.130）** 与 **ego-002（10.10.11.52）** 现场 SSH 验收结论整理。  
**MCAP 采集 systemd 环境（`z-mcap-production.conf`）在 001/002 已一致**，`capture_ring_overflow` / 上传 **CORRUPT** 在 002 上主要来自 **系统基线 + USB 热点硬件栈**，不是少配一条 `EGO_OAK_MODE`。

---

## 1. 为何 001 正常、002 仍会 CORRUPT？

| 层级 | ego-001（130） | ego-002（52） | 与 ring overflow 关系 |
|------|----------------|---------------|------------------------|
| MCAP 生产 drop-in | `~/.config/systemd/user/.../z-mcap-production.conf`（`/dev/shm` active、`fsync_quad`、parallel drain、ring 阈值等） | **与 001 字节级同模板**（仅 station 路径不同） | 已对齐，**不是根因** |
| 内核 | `7.0.0-29-generic` + `apt-mark hold` | `7.0.0-38-generic`，**未 hold** | USB/xHCI 行为漂移风险 |
| apt 自动更新 | `apt-daily*` / `unattended-upgrades` **masked** | **enabled** | 可能悄悄升级内核/固件 |
| USB 热点 | **RTL8188GU**（`rtl8xxxu`），Port **3-5** | **Tenda AIC8800DC**（`aic8800_fdrv`），Port **3-5** | 与 OAK **同 480M 总线**争用 |
| OAK USB | Port **3-4**（与 001 同拓扑） | Port **3-4** | 热点驱动 reload 若不复位 Hub/OAK → xlink 积压 |
| Tenda 初始化 | N/A | 仓库有 `ego-tenda-wifi-init.sh`（Hub 复位 + **gate OAK 3-4** + 卸/装 AIC 驱动） | **52 上曾未装 `/usr/local/bin`，热点启动前未执行** |
| udev USB WiFi | 001 无 `70-ego-usb-wifi.rules`（NM 用 `wlx502b73…`） | `70-ego-usb-wifi.rules` 曾带 **001 的 MAC `50:2b:73:e8:46:0c`**，与实际 Tenda **`c8:3a:35:94:90:58`** 不一致 | 接口名/绑定混乱，加重 NM 重试与 USB 抖动 |
| `/etc/default/ego-hotspot` | MAC/SSID 与现场 dongle 一致 | MAC 已改为 Tenda | 需与 udev + `ego-130-provision-usb-hotspot.sh` 一并重跑 |
| 双 WiFi | USB AP + **`wlo1` → SRI-WIFI**（实验室上行） | 仅 USB AP（可接受） | 002 不连 SRI 不是 CORRUPT 主因 |
| 站点光学 profile | fisheye + rectify（001） | 仓库 `ego-002-mcap-production.env` 为 **pinhole + passthrough**；机上 `station.conf` 仍可能为 **rectify**（影响 34 L2，不直接导致 ring overflow） | 002 需 **re-provision profile** |
| 相机拓扑 `socket_to_role` | `camera_topology_ego-001` v1 | **`camera_topology_ego-002` v4**（见 §2.13 / ego-002-edge-deploy §3） | 与光学无关；**每顶头盔单独标定** |

**失败段特征（002）**：`integrity.issues` 含 `capture_ring_overflow`（四路 `ring_ovf`），journal 有 `ingest_overflow_final parallel_ring=[CAM_* …]` → **采集环在停录/高负载时来不及 drain**，与 001 在 USB 稳定栈下少见。

---

## 2. 所有新站必做（与 USB 芯片无关）

按 [gpd-micropc2-capture-station-setup.md](./gpd-micropc2-capture-station-setup.md) **全文**执行，至少包括：

1. **显示 §2 全套**（与 001 一致，**非**仅 mask 加速度计）：GRUB `panel_orientation`、Xorg、`gpd-fix-display-rotate.sh`、logind 合盖不休眠、acpi/udev/`gpd-fix-display-rotate.service`、`iio-sensor-proxy` mask + GNOME `orientation-lock`。**一键**：`bash data-lab-platform/scripts/ego-gpd-display-stack.sh server@<IP>` → **reboot** → 按 [gpd §2.8–§2.9](./gpd-micropc2-capture-station-setup.md) 验收  
2. **内核钉扎** `PINNED_KERNEL=7.0.0-29-generic`（或现场新验证版本）+ `apt-mark hold` 七个包  
3. **mask** `apt-daily.timer`、`apt-daily-upgrade.timer`、`unattended-upgrades`；`/root/NO-AUTO-UPGRADE.txt`  
3b. **关闭 Apport 崩溃弹窗**（002 现场曾反复弹出 “System program problem detected”）：见 [gpd-micropc2-capture-station-setup.md](./gpd-micropc2-capture-station-setup.md) **§6.4**（`apport`/`whoopsie` mask + `enabled=0`）  
4. **Luxonis udev**：`81-luxonis-usb-power.rules`（`power/control=on`，`autosuspend=-1`）  
5. **OAK 口位**：确认 `lsusb -t` 上 OAK 为 **Bus 3 Port 4**（与 001 一致）；记录 `EGO_OAK_USB_DEV=3-4`  
6. **用户 MCAP 栈**：`bash data-lab-platform/scripts/ego-130-provision.sh --profile mcap-production server@<IP> <station-id>`  
7. **热点 watchdog**：`ego-130-provision-hotspot-watchdog.sh`（或完整 USB 热点 provision，见下节）  
8. **oak bootloader 预检**：`ecs-oak-bootloader-precheck.timer`（user systemd）已 enable  
9. **34 注册**：topology、`datasets.json`、`ego-pipeline-stations.yaml`、upload token  
10. **站点 profile SSOT**：`data-lab-platform/config/station-profiles/<station>-mcap-production.env`（001 fisheye / 002+ pinhole 等）  
11. **手机四路预览（必做，与 MCAP 独立）**  
    - 在采集站执行 `data-lab-platform/ego-local-web/deploy.sh`（会调用 `deploy-staging.sh`，安装 **8081** `ecs-ego-web-staging`）  
    - `loginctl enable-linger server`  
    - `systemctl --user enable --now ecs-ego-web.service ecs-ego-web-staging.service`  
    - MCAP provision 已带 `ecs-preview-standby` + `z-pythonpath.conf`（`8765` 四路 JPEG）  
    - 手机录前验收：`http://<热点网关>:8081/`；本机 `curl http://127.0.0.1:8765/preview/status` → `online:true`  
    - **8080 ≠ 8081**：8080 只控采集 + 录中单路；四路同屏只在待机看 8081（或 App 拉 `:8765`）  
12. **验收**：`ego-130-verify-production.sh`；短录 → manifest **无** `capture_ring_overflow` → `ego-upload` → 34 derive  
13. **相机拓扑（每站必做，禁止默认复制 ego-001）**  
    - SSOT：`ego-stream-client/config/camera_topology_<station>.yaml` + 同名 `.json`  
    - 34 同步：`lerobot-studio/config/topologies/camera_topology_<station>.json`  
    - `station-profiles/<station>-mcap-production.env` 中 `EGO_TOPOLOGY_FILE` 指向边缘 `~/workspace/ego-studio/config/...`  
    - provision：`ego-130-provision.sh --profile mcap-production` 会 rsync 上述文件到边缘机  
    - **机制**：`CAM_A..D`（板上固定）→ `socket_to_role`（前左/前右/后左/后右）→ `observation.images.camera_*`；8081/App 的 `front_left` 等 URL 按 LeRobot 字段取帧，UI 文案不读拓扑  
    - **`ecs-preview-standby` 只读** `~/.config/ego-station.env.d/ego-topology.env`（仅 `EGO_TOPOLOGY_FILE`）；**禁止**加载完整 `station.conf`（`OAK_HW_JPEG=0` 会导致 8081 只剩单路）。未设则退回 **`ego-standard`**。MCAP 仍从 `z-mcap-production.conf` + `station.conf` 读拓扑。provision 会生成 `ego-topology.env`  
    - **参考布局**（勿混用）：`camera_topology_standard.yaml`（fleet 金标）与 `camera_topology_ego-001.yaml`（001 头盔实测）**不同**；002 v2 又与 001 不同，见 [ego-002-edge-deploy.md](./ego-002-edge-deploy.md) §3  
    - **标定 SOP**：待机开 `http://<网关>:8081/` → 依次遮住一颗镜头，记录哪一格变黑 → 填写 `socket_to_role` → 同步边缘 + 34 json → `systemctl --user restart ecs-preview-standby` → 四格与头盔朝向一致再录  
    - **7b 扩展**：检查清单 §7 增加「四路预览朝向与头盔一致」  

**ego-002 逐步命令**：见 [ego-002-edge-deploy.md](./ego-002-edge-deploy.md)。

---

## 3. USB 热点分支（二选一，必做其一）

### 3A. Realtek RTL8188GU / 8821CU（ego-001 路径）

```bash
EGO_HOTSPOT_CONN=EGO-xxx-COLLECT \
EGO_USB_WIFI_MAC=<dongle-mac> \
bash data-lab-platform/scripts/ego-130-provision-usb-hotspot.sh server@<IP> wlp_usb_ap
```

- 推荐 **USB dongle 只发 AP**，`wlo1` 只连实验室 Wi‑Fi（§5.5 WPA2-only、信道 11）。  
- 换 dongle 后必须重跑并更新 **MAC + udev + NM connection**。

### 3B. Tenda AIC8800DC（ego-002 路径，**001 上没有的一步**）

在 **3A 的脚本之外**，还必须：

```bash
EGO_OAK_USB_DEV=3-4 \
bash data-lab-platform/scripts/ego-130-provision-tenda-usb.sh server@<IP>
```

该脚本会：

- 安装 `ego-tenda-wifi-init.sh` → `/usr/local/bin/ego-tenda-wifi-init.sh`  
- 写入 `/etc/default/ego-tenda`（`EGO_OAK_USB_DEV`）  
- 为 `ecs-ego-hotspot.service` 增加 **ExecStartPre**：热点拉起前先 Tenda 初始化（避免 AIC 驱动与 OAK 同 Hub 打架）  

**手动验收**（开机后、开录前）：

```bash
sudo EGO_OAK_USB_DEV=3-4 /usr/local/bin/ego-tenda-wifi-init.sh
lsusb -t   # OAK 应能离开长时间 bootloader-only（配合 precheck timer）
```

热点 provision 时 **udev MAC 必须用 Tenda 真实 MAC**（勿复制 001 模板）：

```bash
EGO_HOTSPOT_CONN=EGO-002-COLLECT \
EGO_USB_WIFI_MAC=c8:3a:35:94:90:58 \
bash data-lab-platform/scripts/ego-130-provision-usb-hotspot.sh server@10.10.11.52 wlp_usb_ap
```

---

## 4. ego-002 专项修复顺序（建议）

1. 按 §2 补齐 **内核 hold + apt mask**（与 001 一致）；boot **`7.0.0-29-generic`** 后执行 **§4.1**（Tenda DKMS）  
2. `EGO_OAK_USB_DEV=3-4 bash ego-130-provision-tenda-usb.sh server@10.10.11.52`  
3. 用 **正确 MAC** 重跑 `ego-130-provision-usb-hotspot.sh`（`EGO-002-COLLECT` + Tenda MAC）  
4. Re-provision MCAP + 合并 profile：  
   `bash data-lab-platform/scripts/ego-130-provision.sh --profile mcap-production server@10.10.11.52 ego-002`  
   （使 `EGO_CAMERA_OPTICS=pinhole`、`EGO_OAK_MODE=passthrough` 写入 `station.conf`；并下发 **mcap + preview 的 `z-pythonpath.conf`**，见 §4.2）  
5. **`EGO_HOTSPOT_CONN=EGO-002-COLLECT`** 重跑 `ego-130-provision-hotspot-watchdog.sh`（勿用默认 `EGO-001-COLLECT`）  
6. 确认 **`camera_topology_ego-002` v4** 已同步（§2.13）；8081 四路朝向验收  
7. **（003/004+ 可选 · 002 已定稿）** 按站调节 OAK 亮度：`OAK_AE_COMPENSATION` drop-in（MCAP + `ecs-preview-standby` 各一份），勿盲目复制 002 的 `+1`；见 [ego-oak-isp-tuning.md](./ego-oak-isp-tuning.md)  
8. 重启 → 等热点稳定 → **单次**短录（避免连续三段压测）→ 查 manifest **无** `capture_ring_overflow` → upload  
9. **34 回放验收**（与 ego-001 同流程）：上传 derive 完成后在 34 执行  
   `node data-lab-platform/lerobot-studio/ego-derive-run.mjs rebuild-view --station ego-002`  
   再打开 `collection?station=ego-002` 确认四路非黑、侧栏 session 标签与时长正确（详见 [ego-002-edge-deploy.md](./ego-002-edge-deploy.md) §7）。

### 4.1 内核 7.0.0-29 + Tenda（002 必做）

钉扎内核后 **AIC8800 仅编过旧内核** 会导致 `aic_load_fw` 找不到、热点 `activating` 失败：

```bash
sudo apt-get install -y linux-image-7.0.0-29-generic linux-headers-7.0.0-29-generic
# apt-mark hold …（见 gpd §3–§4）
sudo reboot
# 上线后
sudo dkms status    # 应含 aic8800dc … 7.0.0-29-generic
sudo /usr/local/bin/ego-tenda-wifi-init.sh
```

`/etc/default/ego-tenda`：**`EGO_OAK_USB_DEV=3-4`**。

### 4.2 手机开录失败（`ego_capture_studio` import）

| 检查 | 期望 |
|------|------|
| `~/.config/systemd/user/ecs-record-oak-mcap.service.d/z-pythonpath.conf` | `PYTHONPATH=…/ego-studio/src` |
| `journalctl --user -u ecs-record-oak-mcap` | 无 `ModuleNotFoundError` |
| `curl -s -X POST http://127.0.0.1:8080/api/capture/start` | `"success": true` |

provision 脚本：`ego-130-provision-mcap-production.sh` 会同步 **preview + mcap** 两份 `z-pythonpath.conf`。边缘 **不必**强依赖 `pip install -e .`（SSH 易超时）。

### 4.3 本地 CORRUPT 段与 upload 告警

- `integrity.issues` 含 **`capture_ring_overflow`** → 性能/USB，不是 timeline；可 `rm -rf ~/cache/ego-002/segments/sessions/<bad_sess>` 避免 upload 反复隔离 `seg_000001`。  
- **`ego-upload` 显示 `4/3 段`**：进度计数噪音，以 34 raw 段数与 manifest 为准。  
- 详情：[ego-002-edge-deploy.md](./ego-002-edge-deploy.md) **§8.4**。

---

## 5. 文件索引（仓库）

| 路径 | 用途 |
|------|------|
| `docs/gpd-micropc2-capture-station-setup.md` | 001 系统基线 SSOT |
| `scripts/ego-gpd-display-stack.sh` | GPD 显示 §2 一键（GRUB/logind/acpi/udev；003/004 必跑 + reboot） |
| `scripts/ego-pull-edge-snapshot.sh` | 边缘机代码/配置镜像到 `edge-station-snapshots/<station>-<date>/`（git 备份） |
| `docs/ego-gpd-station-migration-checklist.md` | 本文（003/004 必做） |
| `scripts/ego-130-provision-mcap-production.sh` | MCAP + `station.conf` |
| `scripts/ego-130-provision-usb-hotspot.sh` | 双 WiFi + udev + NM 热点 |
| `scripts/ego-130-provision-tenda-usb.sh` | Tenda-only 初始化与 hotspot Pre |
| `scripts/ego-tenda-wifi-init.sh` | Hub 复位 + OAK gate + AIC 驱动 |
| `config/station-profiles/ego-*-mcap-production.env` | 光学/上传/L2 站点差异 |
| `ego-stream-client/systemd/.../z-mcap-production.conf` | MCAP 环与 parallel drain |
| `ego-stream-client/systemd/.../ecs-record-oak-mcap.service.d/z-pythonpath.conf` | 8080 开录 import 路径 |
| `docs/ego-oak-isp-tuning.md` | **按站 OAK 亮度**（`OAK_AE_COMPENSATION` · 002 定稿 +1） |
| `ego-stream-client/systemd/.../zz-ego-002-isp.conf.example` | 002 ISP drop-in 示例（003 复制为 `zz-ego-00N-isp.conf`） |
| `ego-local-web/deploy.sh` + `deploy-staging.sh` | 8080 采集页 + **8081 四路预览** |
| `ego-local-web/docs/ego-app-tcp-protocol.md` | 8080/8081/8765/9100 分工 |
| `docs/ego-002-edge-deploy.md` | **002 边缘一站式部署**（003/004 改表即用） |
| `ego-stream-client/config/camera_topology_ego-*.yaml` | 每站 `socket_to_role` SSOT |
| `ego-stream-client/topology.py` | 运行时加载 `EGO_TOPOLOGY_FILE` |

---

## 6. 记录（2026-10-06）

- **001**：内核 29、apt masked、RTL USB AP、MCAP drop-in 完整、热点/预检 timer 正常；拓扑 `camera_topology_ego-001` v1；**2026-10-07** 130 已部署 `ego-topology.env` + standby unit（8081 四路 MJPEG 与 MCAP 拓扑一致）。  
- **002**：MCAP drop-in 已与 001 对齐；**2026-10-07** 已钉扎内核 29、Tenda init、mcap **`z-pythonpath.conf`**、热点 `EGO-002-COLLECT`；拓扑 **`camera_topology_ego-002` v4**；**OAK 亮度** `OAK_AE_COMPENSATION=+1`；故障表 [ego-002-edge-deploy.md](./ego-002-edge-deploy.md) **§8**。  
- **003/004**：复制本清单 + `station-profiles` + **新建并标定** `camera_topology_ego-00N`（勿复制 001/002）；硬件与 002 同则用 **§3B**，与 001 同则用 **§3A**。
