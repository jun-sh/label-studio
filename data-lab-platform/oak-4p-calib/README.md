# OAK 四目鱼眼标定工具（oak-4p-calib）

面向 **EGO 采集站** 使用的 **OAK-FFC-4P** / **OAK-4P-New** 四路鱼眼模组：读取 EEPROM 内参、备份 JSON、四路去畸变预览。

源码自边缘机 `10.10.10.214` 的 `~/workspace/oak-4p-calib` 同步入库，供 **ego-001（10.10.10.130）** 等采集站复用。

> 本工具覆盖「读标定 → 验证 → 去畸变预览」。**Charuco 重新标定并写回 EEPROM** 需使用 Luxonis `depthai-calib`（见下文「进阶：重新标定」）。

---

## 硬件与软件映射

EGO 四路相机与 DepthAI socket 对应关系（与 `ego-stream-client/config/camera_topology_ego-001.yaml` 一致）：

| Socket | 语义 | 典型 HFOV |
|--------|------|-----------|
| CAM_A | front_left（前左） | 180° 鱼眼 |
| CAM_B | front_right（前右） | 180° 鱼眼 |
| CAM_C | rear_left（后左） | 180° 鱼眼 |
| CAM_D | rear_right（后右） | 180° 鱼眼 |

板卡拓扑配置（外参先验，非 EEPROM 内参）：

- `../ego-stream-client/config/OAK-FFC-4P-ego.json`
- `../ego-stream-client/config/OAK-FFC-4P-ego-intrinsics-only.json`

采集栈读取 EEPROM 的逻辑见 `../ego-stream-client/camera_intrinsics.py`。生产开录若启用严格模式，EEPROM 无有效内参将拒绝录制。

---

## 环境准备

在**已接 OAK 的边缘机**上操作（需 USB 可见、用户有 `server` 权限）。

### 依赖

- Python **3.10+**
- [uv](https://github.com/astral-sh/uv)（推荐）或 pip
- DepthAI 系统依赖（udev 等）：见 [Luxonis 安装说明](https://docs.luxonis.com/en/latest/pages/tutorials/first_steps/)

### 安装

```bash
cd data-lab-platform/oak-4p-calib
uv sync
# 或: pip install -e .
```

验证 DepthAI：

```bash
uv run python -c "import depthai as dai; print(dai.__version__)"
```

---

## 操作流程

### 步骤 1：检查设备与四路相机

确认 OAK 已连接、四路 socket 均可见：

```bash
uv run python scripts/verify_device.py
```

期望输出示例：

```
XLink devices: 1
  id=19443... name=... platform=X_LINK_MYRIAD_X state=X_LINK_BOOTED
DeviceId:   19443...
Name:       ...
Cameras:    [<CameraBoardSocket.CAM_A ...>, CAM_B, CAM_C, CAM_D]
Board:      ...
OK: all 4 camera sockets detected
Platform:   RVC2 (Myriad X) — e.g. OAK-FFC-4P
Done.
```

常见问题：

| 现象 | 处理 |
|------|------|
| `No device found` | 检查 USB 线/供电；`lsusb \| grep 03e7`；确认 udev 规则 |
| `BOOTLOADER state` | 等待启动完成，或拔插 USB |
| `missing sockets` | 检查 FFC 排线、模组供电；少一路则无法做四目标定验证 |

### 步骤 2：读取 EEPROM 内参并备份

```bash
uv run python scripts/read_calib.py -o data/calib
```

脚本会：

1. 对 CAM_A–D 打印畸变模型、默认分辨率、FOV、畸变系数 **K**
2. 将全部 EEPROM 标定导出为 `data/calib/calib_<DeviceId>.json`

若输出 `ERROR: no camera calibration in EEPROM`，说明设备**未标定或 EEPROM 损坏**，需执行「进阶：重新标定」。

> `data/calib/*.json` 为设备专属备份，已加入 `.gitignore`，请勿提交到 Git。

### 步骤 3：四路去畸变预览（验收）

需要本机图形界面（SSH 时加 `-X` 或直接在边缘机桌面运行）：

```bash
uv run python scripts/undistort_4cam.py
# 可选分辨率: --width 640 --height 400
```

- **OAK-FFC-4P（RVC2）**：主机侧 `cv2.fisheye.remap`
- **OAK-4P-New（RVC4）**：设备侧 `enableUndistortion`

按 `q` 退出。验收要点：

- 四窗口均有画面
- 直线（门框、标定板边缘）在去畸变后更接近直线
- 四路无明显「未标定」的严重桶形/枕形残留

### 步骤 4：与采集栈对齐（可选）

EGO MCAP 采集会在 `session_start` 上传 `meta/camera_intrinsics.json`。确认采集站能读到与步骤 2 一致的内参：

```bash
# 在 ego-stream-client 目录，设备空闲时
cd ../ego-stream-client
python3 -c "
import depthai as dai
from camera_intrinsics import read_calibration_from_device
with dai.Device() as dev:
    calib, src = read_calibration_from_device(dev)
    print('source:', src)
    print('board:', calib.getEepromData().boardName)
"
```

---

## 进阶：重新标定（Charuco → 写 EEPROM）

本目录**不包含** Charuco 标定 GUI。需要重新标定时：

1. 准备 Luxonis 支持的 Charuco 板（打印尺寸与 `depthai-calib` 配置一致）
2. 在边缘机克隆 [depthai](https://github.com/luxonis/depthai) 或使用已有的 `~/workspace/depthai-calib`
3. 将板卡配置指向 EGO 四目拓扑：
   - 复制 `../ego-stream-client/config/OAK-FFC-4P-ego.json` 到 `depthai-calib/boards/`
4. 运行标定（示例，路径因环境而异）：

```bash
cd ~/workspace/depthai-calib
bash scripts/run_calibrate.sh
```

5. 标定完成后重复**步骤 2–3**验证，再试开录

官方参考：[DepthAI Calibration](https://docs.luxonis.com/en/latest/pages/calibration/)、[Calibration Reader 示例](https://docs.oakchina.cn/projects/api/samples/calibration/calibration_reader.html)

---

## 目录结构

```
oak-4p-calib/
├── README.md                 # 本文档
├── pyproject.toml
├── scripts/
│   ├── verify_device.py      # 设备 / 四路 socket 检查
│   ├── read_calib.py         # 读 EEPROM → 终端摘要 + JSON 备份
│   └── undistort_4cam.py     # 四路去畸变实时预览
└── data/calib/               # 本地 EEPROM 备份目录（json 不入库）
```

---

## 从开发机同步到边缘机

```bash
# 在有 data-lab 仓库的机器
rsync -av data-lab-platform/oak-4p-calib/ server@10.10.10.130:~/workspace/oak-4p-calib/

# 边缘机上
ssh server@10.10.10.130
cd ~/workspace/oak-4p-calib && uv sync
uv run python scripts/verify_device.py
```

---

## 与 214 历史环境的关系

| 位置 | 说明 |
|------|------|
| `10.10.10.214:~/workspace/oak-4p-calib` | 原始开发副本（214 已退役采集站，工具仍可用） |
| 本目录 | 仓库权威副本 + 中文操作文档 |
| `214:~/workspace/depthai-calib` | Luxonis 全量标定工具链（未入库，体量大） |

---

## 故障排查

| 症状 | 可能原因 | 建议 |
|------|----------|------|
| `read_calib` 某路 `no calibration data` | 该路未写入 EEPROM | Charuco 重标定 |
| 去畸变后仍严重弯曲 | 标定质量差或分辨率与 K 不匹配 | 重标定；预览分辨率与 `getDefaultIntrinsics` 对齐 |
| 采集 `INTRINSICS_INVALID` | 严格模式 + EEPROM 无效 | 先跑 `read_calib.py` 确认四路有效 |
| `undistort_4cam` 无窗口 | 无 DISPLAY / SSH 未转发 X11 | 本地桌面运行或 `ssh -X` |

---

## 相关仓库路径

- 采集内参模块：`data-lab-platform/ego-stream-client/camera_intrinsics.py`
- 板卡 JSON：`data-lab-platform/ego-stream-client/config/OAK-FFC-4P-ego*.json`
- 采集站部署：`data-lab-platform/docs/gpd-micropc2-capture-station-setup.md`
