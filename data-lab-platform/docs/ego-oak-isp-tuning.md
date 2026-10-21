# OAK 画面亮度 / 曝光调节（按站点 · 003/004 参考）

**适用硬件：** OAK-4P-New / OAK-FFC-4P 四路彩色（ego-001/002/003… MCAP 采集栈）。  
**不适用：** 色温/曝光 **写入模组 EEPROM**（Luxonis EEPROM 主要为标定内参，见 `oak-4p-calib/README.md`）。

ego-002 现场结论（2026-10-07）：**保持生产默认 `OAK_AE_MAX_EXPOSURE_US=20000`，用 `OAK_AE_COMPENSATION=+1` 微调亮度**；8081 与 MCAP 须 **同一套** drop-in。

---

## 1. 概念：两种「永久」

| 层级 | 含义 | ego 做法 |
|------|------|----------|
| **边缘机持久** | 重启、provision 后仍生效 | `~/.config/systemd/user/*.service.d/zz-ego-00N-isp.conf` |
| **模组 EEPROM** | 写进 OAK 硬件 | **仅标定**（`flashCalibration2`），**不**存曝光/色温偏好 |

采集代码：`data-lab-platform/ego-stream-client/oak_4p_capture.py`（边缘同步为 `~/workspace/ego-studio/src/ego_capture_studio/capture/oak_4p_capture.py`）。

---

## 2. 环境变量（亮度相关）

与 **001 共用** `z-mcap-production.conf` 的 FSYNC / 30Hz 参数 **不要为变亮乱改**（`OAK_FSYNC_*`、`EGO_FRAME_INTERVAL_MS` 等）。

| 变量 | 生产默认 | 作用 | 调亮/调暗 |
|------|----------|------|-----------|
| **`OAK_AE_MAX_EXPOSURE_US`** | `20000` | 自动曝光 **时间上限**（μs）；配合 `OAK_FSYNC_FIX=1` 时 `setAutoExposureLimit` | **加大**更亮，易损 30Hz / 掉帧；`0` 关闭上限（高风险） |
| **`OAK_AE_COMPENSATION`** | `0`（未设） | Luxonis **曝光补偿** -9…+9，`setAutoExposureCompensation` | **+1～+2** 整体变亮；**0 或 +1** 略暗；优先于拉长 `AE_MAX` |

**代码未接环境变量、勿指望靠 systemd 调节的项：** 手动色温/ISO、`setCameraTuningBlobPath`、亮度/对比度滑块等（需改 `oak_4p_capture.py` 或 Luxonis 示例脚本）。  
**几乎不改变传感器曝光：** `PREVIEW_JPEG_QUALITY`、`OAK_H264_BITRATE_KBPS`（只影响压缩观感/码率）。

官方 API：[CameraControl](https://docs.luxonis.com/software/depthai-components/messages/camera_control/) · 示例 [rgb_camera_control.py](https://github.com/luxonis/depthai-python/blob/main/examples/ColorCamera/rgb_camera_control.py)。

---

## 3. ego-002 定稿（金标）

| 项 | 值 |
|----|-----|
| 站点 | `ego-002` · `10.10.11.52` |
| `OAK_AE_MAX_EXPOSURE_US` | **20000**（仅 `z-mcap-production.conf`，**不要**在站点 override 里写 25000） |
| `OAK_AE_COMPENSATION` | **+1**（较 +2 略暗一档，四路观感满意） |
| drop-in 文件名 | `zz-ego-002-isp.conf`（**两份**，见 §4） |
| 仓库示例 | `ego-stream-client/systemd/ecs-record-oak-mcap.service.d/zz-ego-002-isp.conf.example` |

调参过程摘要（供 003/004 复现思路，**数值须每站重试**）：

1. 曾试 `OAK_AE_MAX_EXPOSURE_US=25000` → 8081 可更亮，但牺牲与 001 一致的 FSYNC 曝光策略，**已回退 20000**。
2. 实现 `OAK_AE_COMPENSATION` 后扫 **0～4**：**+3** 四路均值好看但 **前两路可近乎黑屏**（不能只看均值）；**+2** 四路均衡偏亮；**+1** 略暗，**采用**。
3. 验收：8081 四格 + 短录 MCAP，journal 中 `ae_max_us=20000 ae_comp=1`。

---

## 4. 边缘部署步骤（ego-00N 通用）

将 `00N` 换成 `002`、`003`、`004`…

### 4.1 确认采集代码含 `OAK_AE_COMPENSATION`

边缘 `oak_4p_capture.py` 需含 `OAK_AE_COMPENSATION` 及对彩色路的 `setAutoExposureCompensation`（与仓库 main 一致）。  
provision / `git pull` 后若未更新，从开发机 rsync：

```bash
rsync -av data-lab-platform/ego-stream-client/oak_4p_capture.py \
  server@<边缘IP>:~/workspace/ego-studio/src/ego_capture_studio/capture/
```

### 4.2 站点 drop-in（MCAP + 8081 各一份）

在 **边缘机 `server` 用户**执行（示例 **comp=+1**，003/004 按 §5 改数字）：

```bash
STATION=ego-003   # 改成 ego-003 / ego-004
COMP=1            # -9..9，003/004 现场试定

for UNIT in ecs-record-oak-mcap ecs-preview-standby; do
  D=~/.config/systemd/user/${UNIT}.service.d
  mkdir -p "$D"
  # 勿保留仅改 AE_MAX 的旧文件（例如 zz-ego-002-ae.conf 写 25000）
  rm -f "$D"/zz-ego-002-ae.conf 2>/dev/null || true
  cat > "$D/zz-${STATION}-isp.conf" <<EOF
[Service]
Environment=OAK_AE_COMPENSATION=${COMP}
EOF
done

systemctl --user daemon-reload
systemctl --user restart ecs-preview-standby.service
```

说明：

- **不要**在 `zz-*-isp.conf` 里覆盖 `OAK_AE_MAX_EXPOSURE_US`，除非明确要做 FSYNC 风险试验；生产保持 **20000** 由 `z-mcap-production.conf` 提供。
- **8081** 只读 `ego-topology.env` 的拓扑规则不变；**亮度**靠 `ecs-preview-standby.service.d` 里上述 `Environment=`，与 MCAP **成对**配置。
- **MCAP**：下次 **开录**（或停录再开）加载；warm idle 时建议停录再录一轮验证。

### 4.3 验收

```bash
# 环境
grep -h OAK_AE ~/.config/systemd/user/ecs-record-oak-mcap.service.d/*.conf \
  ~/.config/systemd/user/ecs-preview-standby.service.d/*.conf 2>/dev/null | sort -u
# 期望: OAK_AE_MAX_EXPOSURE_US=20000 与 OAK_AE_COMPENSATION=<你的值>

systemctl --user is-active ecs-preview-standby
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/preview/status   # 200

# 录中日志（开录后）
journalctl --user -u ecs-record-oak-mcap -n 80 --no-pager | grep -E 'ae_max_us|ae_comp'
```

**手机：** 未开录 → `http://192.168.8.1:8081/` 四路；开录 → 以 MCAP/8080 为准（standby 会停）。

### 4.4 回滚

```bash
rm -f ~/.config/systemd/user/ecs-record-oak-mcap.service.d/zz-ego-00N-isp.conf
rm -f ~/.config/systemd/user/ecs-preview-standby.service.d/zz-ego-00N-isp.conf
systemctl --user daemon-reload
systemctl --user restart ecs-preview-standby.service
```

未设 `OAK_AE_COMPENSATION` 时等价于 **0**（设备默认 3A）。

---

## 5. 003/004 推荐调参流程

每站头盔/环境光不同，**不要复制 002 的 `+1` 而不看一眼画面**；流程可复用：

1. 完成 [ego-002-edge-deploy.md](./ego-002-edge-deploy.md) 同级 provision（MCAP + 8081 + 拓扑）。
2. 保持 **`OAK_AE_MAX_EXPOSURE_US=20000`**。
3. 在 **8081** 上试 `OAK_AE_COMPENSATION`：**0 → 1 → 2**（一般足够；避免贸然 **+3+**）。
4. 每改一档：`daemon-reload` + `restart ecs-preview-standby`，等 **≥5～10 s** 让 AE 收敛，**四格都看**（前左/前右/后左/后右）。
5. 满意后写入 `zz-ego-00N-isp.conf`（MCAP + standby 各一），短录 1 段 MCAP 确认与 8081 一致。
6. 将 **定稿 `COMP` 值**记入本站 `docs/ego-00N-edge-deploy.md` 或本节下的站点表（可选）。

可选机内粗测（边缘机，需 OpenCV）：

```bash
# 保存为 /tmp/check_luma.py 后执行；四路均值仅供参考，须结合 min(四路)
/home/server/workspace/ego-studio/.venv/bin/python /tmp/check_luma.py
```

**反例：** 仅最大化四路 JPEG 灰度均值 → 可能选出 **+3**，前两路严重欠曝。

---

## 6. 与 provision / 001 的关系

| 话题 | 说明 |
|------|------|
| `ego-130-provision.sh --profile mcap-production` | 下发 `z-mcap-production.conf`（含 `OAK_AE_MAX_EXPOSURE_US=20000`），**不会**自动下发各站 `zz-ego-00N-isp.conf` |
| ego-001 | 现场未加 `OAK_AE_COMPENSATION`（等价 0）；002+ 按站加 drop-in |
| 站点光学 | `EGO_OAK_MODE` / `pinhole` 等见 `config/station-profiles/ego-*-mcap-production.env`，与曝光补偿 **独立** |

---

## 7. 相关文档与文件

| 路径 | 用途 |
|------|------|
| 本文 | OAK 亮度/曝光 **按站 SOP** |
| [ego-002-edge-deploy.md](./ego-002-edge-deploy.md) | 002 部署总览 · §7 指向本文 |
| [ego-gpd-station-migration-checklist.md](./ego-gpd-station-migration-checklist.md) | 003/004 迁移清单 · 文件索引 |
| `ego-stream-client/oak_4p_capture.py` | `OAK_AE_*` 实现 |
| `ego-stream-client/systemd/.../z-mcap-production.conf` | 001/002 共用 MCAP 基线（含 AE max 20000） |
| `ego-stream-client/systemd/.../zz-ego-002-isp.conf.example` | 002 定稿示例 |
| [oak-4p-calib/README.md](../oak-4p-calib/README.md) | EEPROM **标定**，非亮度 |

---

## 8. 站点定稿表（随现场更新）

| 站点 | `OAK_AE_MAX_EXPOSURE_US` | `OAK_AE_COMPENSATION` | 备注 |
|------|--------------------------|------------------------|------|
| ego-001 | 20000 | 0（默认） | 与 `z-mcap-production` 一致 |
| ego-002 | 20000 | **+1** | 2026-10-07 定稿 |
| ego-003 | 20000 | _待标定_ | 复制 §4–§5 |
| ego-004 | 20000 | _待标定_ | 复制 §4–§5 |
