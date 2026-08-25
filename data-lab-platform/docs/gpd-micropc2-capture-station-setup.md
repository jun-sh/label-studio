# GPD MicroPC 2 采集站配置手册

基于 **ego-001 采集站 130**（`10.10.10.130` / `server-ego-001`）上已验证的配置整理。  
新机（另一台 GPD MicroPC 2）按本文档操作，可复现：

- 屏幕旋转 + **固定 1280×800 横屏**（合盖/开盖自动恢复）
- 内核/固件/微码 **钉扎**，禁止无人值守系统更新
- WiFi 热点 + EGO 采集栈部署

> 参考机型：`GPD MicroPC 2`（DMI `G1688-08`），Ubuntu **24.04 LTS**，GNOME **X11**（Wayland 下 `xrandr` 修复无效）。  
> 社区脚本 [gpd-micropc2-ubuntu](https://github.com/netsuperman/gpd-micropc2-ubuntu) 的**屏幕旋转部分可跳过**（本文已覆盖）；其余模块（触摸板、TDP 等）按需选用。

---

## 0. 变量（按站点修改）

| 变量 | ego-001 / 130 示例 | 说明 |
|------|-------------------|------|
| `STATION_ID` | `ego-001` | 站点 ID |
| `HOST_IP` | `10.10.10.130` | 有线网 IP |
| `LINUX_USER` | `server` | 登录用户 |
| `HOTSPOT_SSID` | `EGO-001-COLLECT` | 手机连接热点名 |
| `HOTSPOT_PSK` | *(现场设定)* | WPA 密码 |
| `HOTSPOT_GATEWAY` | `192.168.8.1/24` | 热点网关 |
| `WIFI_IFACE` | `wlo1` | `nmcli` 看到的 WiFi 网卡名（130 上为 `wlo1`，altname `wlp0s20f3`） |
| `PLATFORM_URL` | `http://10.10.10.34:8080` | 34 平台地址 |
| `PINNED_KERNEL` | `7.0.0-29-generic` | 已验证稳定的 HWE 内核 |

---

## 1. 系统基线

1. 安装 **Ubuntu 24.04.x Desktop**（建议与 130 同版本）。
2. 登录会话使用 **Xorg**（登录界面齿轮 → “Ubuntu on Xorg”）。
3. 创建用户 `server`，加入 `sudo`。
4. 配置静态 IP（示例 `10.10.10.130/24`）或 DHCP 保留。
5. 安装基础包：

```bash
sudo apt update
sudo apt install -y git curl rsync ffmpeg acpid network-manager \
  python3 python3-venv python3-pip
sudo systemctl enable --now acpid NetworkManager
```

---

## 2. 显示：旋转 + 固定 1280×800

### 2.1 背景

- GPD 内屏物理为 **1080×1920 竖屏**。
- 内核参数 `panel_orientation=right_side_up` 已参与旋转；用户态需 **`1080x1920` + `rotate right` + scale** 得到 **1280×800 横屏**。
- 合盖/开盖、休眠唤醒后 Xorg 的静态配置**不会自动重新生效**，需事件钩子 + 修复脚本。
- **必须 mask `iio-sensor-proxy`**，否则加速度计会干扰方向。

### 2.2 GRUB 内核参数

编辑 `/etc/default/grub`：

```bash
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash fbcon=rotate:1 video=DSI-1:panel_orientation=right_side_up"
```

然后：

```bash
sudo update-grub
```

### 2.3 Xorg 首选旋转

`/etc/X11/xorg.conf.d/20-monitor.conf`：

```ini
Section "Monitor"
    Identifier "DSI-1"
    Option "Rotate" "right"
EndSection
```

### 2.4 核心修复脚本

`/usr/local/bin/gpd-fix-display-rotate.sh`：

```bash
#!/bin/bash
# GPD MicroPC 2: 1280x800 landscape (native 1080x1920 + rotate right + scale).
set -euo pipefail
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/home/server/.Xauthority}"
OUT="DSI-1"
MODE="1080x1920"
ROTATE="right"
SCALE="0.6666666666666666x0.7407407407407407"
TARGET_W=1280
TARGET_H=800

if ! xrandr --query 2>/dev/null | grep -q "^${OUT} connected"; then
  exit 0
fi

line=$(xrandr --query | awk -v o="$OUT" '$1==o {print; exit}')
rot="unknown"
if [[ "$line" =~ \+[0-9]+\+[0-9]+\ \(0x[0-9a-fA-F]+\)\ ([a-z]+) ]]; then
  rot="${BASH_REMATCH[1]}"
elif [[ "$line" =~ \+[0-9]+\+[0-9]+\ \(([a-z]+) ]]; then
  rot="${BASH_REMATCH[1]}"
fi

read -r cur_w cur_h _ < <(xdpyinfo 2>/dev/null | awk '/dimensions:/ {gsub(/x/, " "); print $2, $3}')

if [[ "${cur_w:-0}" == "$TARGET_W" && "${cur_h:-0}" == "$TARGET_H" && "$rot" == "$ROTATE" ]]; then
  exit 0
fi

xrandr --output "$OUT" --mode "$MODE" --rotate "$ROTATE" --scale "$SCALE"
logger -t gpd-fix-display "set ${OUT} to ${TARGET_W}x${TARGET_H} ${ROTATE} (was ${cur_w:-?}x${cur_h:-?} ${rot})"
```

```bash
sudo install -m 0755 /path/to/gpd-fix-display-rotate.sh /usr/local/bin/gpd-fix-display-rotate.sh
sudo ln -sf /usr/local/bin/gpd-fix-display-rotate.sh /usr/local/bin/gpd-display-fix.sh
```

手动验证：

```bash
DISPLAY=:0 XAUTHORITY=/home/server/.Xauthority /usr/local/bin/gpd-fix-display-rotate.sh
DISPLAY=:0 XAUTHORITY=/home/server/.Xauthority xrandr --query | head -4
# 期望：DSI-1 ... right，桌面 dimensions 1280x800
```

### 2.5 禁用加速度计自动转屏

```bash
sudo systemctl stop iio-sensor-proxy
sudo systemctl mask iio-sensor-proxy
```

### 2.6 合盖不休眠（采集站必须）

`/etc/systemd/logind.conf.d/ego-capture.conf`：

```ini
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
IdleAction=ignore
```

```bash
sudo systemctl restart systemd-logind
```

### 2.7 合盖/开盖、唤醒、登录时自动修复

**ACPI 脚本** `/etc/acpi/gpd-lid-fix.sh`：

```bash
#!/bin/bash
sleep 1
su - server -c 'DISPLAY=:0 XAUTHORITY=/home/server/.Xauthority /usr/local/bin/gpd-fix-display-rotate.sh'
```

**ACPI 事件** `/etc/acpi/events/lid-fix`：

```
event=button/lid.*
action=/etc/acpi/gpd-lid-fix.sh
```

**休眠唤醒** `/etc/systemd/system-sleep/99-gpd-fix-display-rotate.sh`：

```bash
#!/bin/bash
case "$1" in
  post)
    sleep 2
    su - server -c 'DISPLAY=:0 XAUTHORITY=/home/server/.Xauthority /usr/local/bin/gpd-fix-display-rotate.sh'
    ;;
esac
```

**登录时** `/etc/X11/Xsession.d/99-gpd-fix-display-rotate`：

```bash
#!/bin/sh
/usr/local/bin/gpd-fix-display-rotate.sh >/dev/null 2>&1 || true
```

**udev** `/etc/udev/rules.d/99-gpd-display-rotate.rules`：

```
ACTION=="change", SUBSYSTEM=="acpi", KERNEL=="PNP0C0D:00", RUN+="/bin/systemctl start gpd-fix-display-rotate.service"
ACTION=="change", SUBSYSTEM=="drm", KERNEL=="card0-DSI-1", RUN+="/bin/systemctl start gpd-fix-display-rotate.service"
```

**systemd oneshot** `/etc/systemd/system/gpd-fix-display-rotate.service`：

```ini
[Unit]
Description=Fix GPD MicroPC 2 display rotation
After=graphical.target

[Service]
Type=oneshot
ExecStartPre=/bin/sleep 1
ExecStart=/bin/su - server -c 'DISPLAY=:0 XAUTHORITY=/home/server/.Xauthority /usr/local/bin/gpd-fix-display-rotate.sh'

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo chmod +x /etc/acpi/gpd-lid-fix.sh /etc/systemd/system-sleep/99-gpd-fix-display-rotate.sh /etc/X11/Xsession.d/99-gpd-fix-display-rotate
sudo systemctl restart acpid
sudo systemctl daemon-reload
sudo udevadm control --reload-rules
```

### 2.8 验收（显示）

1. 开盖：桌面为 **1280×800 横屏**。
2. 合上盖子再打开：约 1–2 秒内恢复横屏。
3. 应急：`/usr/local/bin/gpd-fix-display-rotate.sh`

---

## 3. 内核钉扎（WiFi 稳定性）

130 上 **7.0.0-30 + linux-firmware .29** 曾触发 Intel AX201 `Microcode SW error` 风暴（开机约 9–10 分钟），影响热点。  
**7.0.0-29** 仍可能有固件错误，但已作为生产钉扎版本；**禁止再自动升级内核/固件**（见第 4 节）。

### 3.1 固定默认启动内核

```bash
# 确认旧内核仍在：ls /boot/vmlinuz-7.0.0-29-generic
sudo sed -i 's/^GRUB_DEFAULT=.*/GRUB_DEFAULT=saved/' /etc/default/grub
sudo update-grub
sudo grub-set-default "Advanced options for Ubuntu>Ubuntu, with Linux 7.0.0-29-generic"
```

重启后确认：

```bash
uname -r   # 7.0.0-29-generic
```

### 3.2 钉扎内核包

```bash
sudo apt-mark hold \
  linux-generic-hwe-24.04 \
  linux-headers-generic-hwe-24.04 \
  linux-image-generic-hwe-24.04 \
  linux-image-7.0.0-30-generic
```

---

## 4. 禁止无人值守系统更新

采集站为**专用设备**，禁止 `unattended-upgrades` 在后台升级内核/固件。

### 4.1 钉扎固件与微码

```bash
sudo apt-mark hold linux-firmware intel-microcode amd64-microcode
```

### 4.2 关闭周期性 apt 与自动升级

`/etc/apt/apt.conf.d/20auto-upgrades`：

```
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::AutocleanInterval "0";
APT::Periodic::Unattended-Upgrade "0";
```

```bash
sudo systemctl stop unattended-upgrades apt-daily.service apt-daily-upgrade.service 2>/dev/null || true
sudo systemctl disable unattended-upgrades apt-daily.timer apt-daily-upgrade.timer
sudo systemctl mask unattended-upgrades apt-daily.timer apt-daily-upgrade.timer
```

### 4.3 记录说明（可选）

写入 `/root/NO-AUTO-UPGRADE.txt`，注明钉扎包列表与恢复步骤（维护窗口再手动 `apt upgrade`）。

### 4.4 验收

```bash
apt-mark showhold
cat /etc/apt/apt.conf.d/20auto-upgrades
systemctl is-enabled unattended-upgrades apt-daily.timer  # 应为 masked
```

---

## 5. WiFi 热点（手机采集 UI）

### 5.1 一次性配置

在 **34 开发机** 或本机仓库根目录执行（需能 SSH 到采集站）：

```bash
# 在采集站上，按站点修改变量后执行 hotspot-setup.sh
cd data-lab-platform/ego-local-web
sudo EGO_WIFI_IFACE=wlo1 \
     EGO_HOTSPOT_SSID=EGO-001-COLLECT \
     EGO_HOTSPOT_PSK='你的密码' \
     EGO_HOTSPOT_GATEWAY=192.168.8.1/24 \
     EGO_HOTSPOT_CONN=EGO-001-COLLECT \
     bash scripts/hotspot-setup.sh
```

该脚本会安装 polkit/sudoers、`ecs-ego-hotspot.service` 并创建 NetworkManager 连接。

### 5.2 推荐：带重试的热点启动脚本

仓库内 `ego-hotspot-up.sh` 仍为旧版（`EGO-214-COLLECT`），130 上使用如下版本。  
覆盖 `/home/server/ego-web/ego-hotspot-up.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail
CONN="${EGO_HOTSPOT_CONN:-EGO-001-COLLECT}"
IFACE="${EGO_WIFI_IFACE:-wlo1}"
MAX_ATTEMPTS="${EGO_HOTSPOT_MAX_ATTEMPTS:-5}"
RETRY_SLEEP="${EGO_HOTSPOT_RETRY_SLEEP:-5}"

for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  if sudo -n /usr/bin/nmcli connection up "$CONN"; then
    if iw dev "$IFACE" info 2>/dev/null | grep -q "type AP"; then
      logger -t ego-hotspot "Hotspot ${CONN} up on ${IFACE} (attempt ${attempt})"
      exit 0
    fi
  fi
  sleep "$RETRY_SLEEP"
done
logger -t ego-hotspot "Hotspot ${CONN} failed after ${MAX_ATTEMPTS} attempts"
exit 1
```

### 5.3 推荐：热点 systemd 单元

`/etc/systemd/system/ecs-ego-hotspot.service`（覆盖仓库默认的 3s sleep）：

```ini
[Unit]
Description=EGO collection WiFi hotspot (EGO-001-COLLECT)
After=NetworkManager.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
# iwlwifi 固件在部分开机窗口内不稳定；至少等待 15s，生产环境建议改为“就绪探测”
ExecStartPre=/bin/sleep 15
ExecStart=/home/server/ego-web/ego-hotspot-up.sh
ExecStop=/usr/bin/nmcli connection down EGO-001-COLLECT

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ecs-ego-hotspot.service
```

### 5.4 iwlwifi 已知问题与检查

- 芯片：Intel AX201（CNVi `8086:54f0`），固件 `iwlwifi-so-a0-hr-b0-89.ucode`。
- 开机后约 **9–10 分钟** 内可能出现 `Microcode SW error`；此期间热点可能无法被手机搜到。
- **不要** 通过删除 89 号固件降级到 86（驱动强制要求 89 API，会导致 WiFi 完全不可用）。
- 检查命令：

```bash
journalctl -k -b | grep -ci 'Microcode SW error'
iw dev wlo1 info | grep -E 'ssid|type|channel'
nmcli connection show --active | grep EGO-001-COLLECT
```

若热点在重启后 10 分钟内不可用，等待固件稳定后执行：

```bash
sudo systemctl restart ecs-ego-hotspot.service
# 或
/home/server/ego-web/ego-hotspot-up.sh
```

---

## 6. EGO 采集栈部署

在 **34 或本机**（能访问采集站与仓库）执行：

```bash
cd /path/to/data-lab
# 同步采集代码、systemd、JPEG 生产路径、ego-upload CLI
bash data-lab-platform/scripts/ego-130-provision.sh server@10.10.10.130 ego-001
# 若 SSH 密钥不可用：
RC_CAPTURE_PASS=1 python3 data-lab-platform/scripts/ego-130-provision-paramiko.py
```

部署 **手机控制 Web**（在采集站上）：

```bash
cd data-lab-platform/ego-local-web
bash deploy.sh
loginctl enable-linger server
systemctl --user enable --now ecs-ego-web.service
```

生产上传模式：

```bash
bash data-lab-platform/scripts/ego-130-upload-mode.sh production
```

验证：

```bash
bash data-lab-platform/scripts/ego-130-verify-production.sh server@10.10.10.130
```

更完整的日常操作见 [ego-001-使用手册.md](./ego-001-使用手册.md)。

---

## 7. 新机完整检查清单

| # | 检查项 | 命令 / 期望 |
|---|--------|-------------|
| 1 | 内核版本 | `uname -r` → `7.0.0-29-generic` |
| 2 | 钉扎包 | `apt-mark showhold` 含 7 个包（内核×4 + firmware + microcode×2） |
| 3 | 自动更新已关 | `systemctl is-enabled apt-daily.timer` → `masked` |
| 4 | 显示分辨率 | `xrandr` → `1280x800`，`DSI-1` 为 `right` |
| 5 | 合盖恢复 | 合盖→开盖后 1–2s 内横屏 |
| 6 | 热点 | 手机可搜到 `EGO-001-COLLECT`，网关 `192.168.8.1` |
| 7 | 采集 Web | `http://192.168.8.1:8080` 或 `http://10.10.10.130:8080` |
| 8 | 采集路径 | `ego-130-verify-production.sh` 通过 |
| 9 | WiFi 微码 | 重启 12 分钟后 `Microcode SW error` 是否可接受；热点是否仍可用 |

---

## 8. 故障速查

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| 合盖后竖屏 | ACPI/修复脚本未生效 | 运行 `gpd-fix-display-rotate.sh`；检查 `acpid` |
| 桌面字太小 | 误用 1920×1080 | 确认脚本 scale 方案，勿单独 `rotate normal` |
| 重启后搜不到热点 | iwlwifi 固件崩溃窗口 | 等 10min 或重启热点服务；长期考虑 USB AP |
| 系统自动升级内核 | 未 mask apt 定时器 | 重做第 4 节 |
| 手机 UI 打不开 | `ecs-ego-web` 未运行 | `systemctl --user status ecs-ego-web` |

---

## 9. 维护窗口（仅人工执行）

需要安全补丁时：

```bash
sudo systemctl unmask unattended-upgrades apt-daily.timer apt-daily-upgrade.timer
sudo apt-mark unhold linux-firmware intel-microcode   # 按需
sudo apt update && sudo apt upgrade
# 验证 WiFi + 热点 + 采集后，重新 hold + mask
```

---

## 10. 文件索引（130 参考）

| 路径 | 用途 |
|------|------|
| `/usr/local/bin/gpd-fix-display-rotate.sh` | 1280×800 横屏修复 |
| `/etc/X11/xorg.conf.d/20-monitor.conf` | Xorg 旋转 |
| `/etc/systemd/logind.conf.d/ego-capture.conf` | 合盖不休眠 |
| `/etc/acpi/events/lid-fix` | 合盖事件 |
| `/root/NO-AUTO-UPGRADE.txt` | 禁止自动更新说明 |
| `/home/server/.config/ego-station.env` | 站点上传/采集环境 |
| `/home/server/ego-web/` | 手机控制 Web + 热点脚本 |
| `/home/server/cache/ego-001/segments` | 采集段目录 |

---

*文档版本：2026-08-25，依据 ego-001 @ 10.10.10.130 现场配置整理。*
