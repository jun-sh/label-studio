# EGO 边缘站：离线上传、存储路径与部署说明

本文档说明 **214 等 EGO 采集边缘机** 的默认工作模式（**本地录制 + U 盘/浏览器离线上传**）、存储路径约定、工程仓库与 systemd 部署方式。

与数据流、ingest 细节互补的文档：[ego-lan-214-segment-storage-and-upload.md](./ego-lan-214-segment-storage-and-upload.md)。

适用站点示例：`ego-lan-214`（`10.10.10.214`）。

---

## 一、产品默认模式

| 能力 | 默认状态 | 说明 |
|------|----------|------|
| OAK 采集（`ecs-oak-capture-stack`） | **开启** | 段落本地 `segments/`，不关网也可录 |
| 自动网络上传（`ecs-oak-upload-stack`） | **关闭（inactive）** | 不自动 POST 到 34 |
| 批量/补传 | **U 盘 + 浏览器拖传** | 在 34 采集页「导入」拖入 `.tar.zst` |
| 去重 | 服务端 `(sessionId, segmentId)` | 重复导入显示「已存在，跳过」 |

采集栈与上传栈在 systemd 中**独立**：只 enable 采集栈即可实现「只录不传」。

---

## 二、路径约定（不要用 `$HOME` 临时文件）

### 2.1 为什么曾出现 `~/manual_drag_test_10s.tar.zst`？

该文件是**一次性手工测试**时，从段目录复制到 `$HOME` 方便在文件管理器里拖拽的**临时路径**，**不是**产品设计路径。生产环境不应依赖 `$HOME/manual_*.tar.zst`。

### 2.2 规范路径分层

| 层级 | 路径 | 用途 |
|------|------|------|
| **段缓存（权威本地库）** | `/home/server/cache/ego-lan-214/segments/` | `manifest.json`、`rows.jsonl`、`frames/*.bin` |
| **热写 tmpfs** | `/dev/shm/ego-capture-active/sessions/{sessionId}/segments/` | 正在录的 open 段；关段后原子搬到段缓存 |
| **段内 tar.zst** | `.../seg_xxx/.upload/seg_xxx.tar.zst` | 标准单段包（与网络上传、浏览器导入同格式） |
| **导出暂存（建议）** | `/home/server/export/ego-lan-214/` | 批量拷 U 盘前的集合目录（运维可选创建） |
| **U 盘挂载** | `/media/usb/` 或 `/mnt/ego-export/` | 物理搬运介质 |

目录结构示例：

```text
/home/server/cache/ego-lan-214/segments/
├── checkpoint.json
├── registry.json
└── sessions/
    └── sess_<uuid>/
        ├── meta/camera_intrinsics.json
        └── segments/
            └── seg_000010/
                ├── manifest.json
                ├── rows.jsonl
                ├── frames/*.bin
                └── .upload/
                    └── seg_000010.tar.zst
```

浏览器拖传**不强制**文件在哪个目录，只要是合法 `.tar.zst` 即可（可来自 U 盘、`export/` 或段内 `.upload/`）。

**现场操作分步说明（手机录制 → U 盘 → 34 导入）**：见 [field-export-and-import.md](../data-lab-platform/ego-local-web/field-export-and-import.md)

---

## 三、离线上传 / U 盘工作流

```text
OAK 录制
  → segments/（仅采集栈，上传栈 inactive）
  → 关段（closed=true, uploaded=false）
  → 可选：pack 为 .upload/*.tar.zst
  → cp/rsync 到 export/ 或 U 盘
  → 有网：浏览器打开 34 采集页 → Episodes「导入」→ 拖入 .tar.zst
  → 34 ingest（与在线 tar.zst 同链路）→ LeRobot v3 数据集
```

### 3.1 34 侧导入入口

```text
http://10.10.10.34:8080/collection?station=ego-lan-214
```

Episodes 工具栏 **「导入」** → 拖入 `.tar.zst`。API 经 Django 代理到 `stream-ingest`，与边缘 `segment_upload.py` 网络上传共用 `processTarZstFromFile`。

### 3.2 重复导入

- 去重键：`sessionId` + `segmentId`（见 34 上 `live/sessions/{sess}/segments/{seg}.done` marker）
- 同一段再拖一次：**成功但 `duplicate: true`**，UI 显示「已存在，跳过」，`framesCommitted: 0`
- 已通过网络上传成功的段，214 本地通常已删除（见下节环境变量）；若 tar.zst 仍在，再导入也会走 duplicate 跳过

### 3.3 离线上传优先时的环境变量（214）

| 变量 | 网络自动上传模式 | **离线上传优先（推荐）** |
|------|------------------|--------------------------|
| `EGO_SEGMENT_DELETE_AFTER_UPLOAD` | `1`（传完删段） | `0`（误触发网络上传也不删本地） |
| `EGO_SEGMENT_QUOTA_GB` | `256` | 本机 `segments/` 待导出队列上限（GB）；导出成功后源段删除，配额循环使用 |
| `SEGMENT_AUTO_PURGE_PENDING` | `1` | `0` 或配合更大 `SEGMENT_MAX_PENDING` |
| `ecs-oak-upload-stack.target` | enabled | **disabled / inactive** |

---

## 四、214 上的工程与 data-lab 仓库关系

### 4.1 214 运行时工程（部署到新机器的安装目标）

```text
/home/server/workspace/ego-studio/          # Python 包名：ego-capture-studio
```

主要入口：

```bash
# 采集
python -m ego_capture_studio.cli.record_oak_stream --fps 20 --imu-hz 100

# 手动网络上传（可选，非默认）
python -m ego_capture_studio.cli.upload_segments \
  --upload-url http://10.10.10.34:8080/lerobot/api/collection/stations/ego-lan-214/upload \
  --ensure-session --limit N
```

### 4.2 data-lab 仓库中的对应代码（开发与 systemd 模板）

```text
data-lab-platform/ego-stream-client/
├── segment_store.py, segment_tar_zst.py, segment_upload.py
├── oak_4p_capture.py, record_oak_stream.py
├── systemd/ecs-*.service / ecs-oak-*.target
└── config/strict20hz_production.env
```

`ego-stream-client` 是 data-lab 侧维护的**边缘采集源码 + systemd 模板**；214 上的 `ego-studio`（`ego-capture-studio`）是**打包安装后的运行时**。新机器部署应以 `ego-studio` 安装包为准，单元文件从 `ego-stream-client/systemd/` 拷贝并按站点改环境变量。

### 4.3 34 侧（导入与 ingest）

| 组件 | 路径 |
|------|------|
| 浏览器导入 UI | `data-lab-platform/client/import-core.js`、`import-modal.css` |
| Django 代理 | `label_studio/core/collection_import.py` |
| Ingest | `data-lab-platform/lerobot-studio/stream-ingest.mjs`、`import-handlers.mjs` |

---

## 五、新机器 systemd 部署清单

### 5.1 默认只开采集栈

```bash
systemctl --user daemon-reload
systemctl --user enable --now ecs-oak-capture-stack.target
```

`ecs-oak-capture-stack.target` 包含：

- `ecs-record-oak-stream.service` — OAK 录制
- `ecs-station-heartbeat.service` — 34 采集页在线状态（可选）

### 5.2 不上传栈（默认）

**不要** enable `ecs-oak-upload-stack.target`。需要临时恢复网络自动上传时：

```bash
systemctl --user start ecs-oak-upload-stack.target
```

### 5.3 每站必改环境变量（示例 `ego-lan-214`）

| 变量 | 示例 |
|------|------|
| `EGO_SEGMENT_ROOT` | `/home/server/cache/ego-lan-214/segments` |
| `EGO_CAPTURE_CHECKPOINT` | `$EGO_SEGMENT_ROOT/checkpoint.json` |
| `DATALAB_HEARTBEAT_URL` | `http://10.10.10.34:8080/lerobot/api/collection/stations/ego-lan-214/upload` |
| `STATION_UPLOAD_TOKEN` | 与 34 `collection-station-tokens.json` 一致 |
| `DATALAB_CAPTURE_HOST` | 本机 IP，如 `10.10.10.214` |

单元模板：`data-lab-platform/ego-stream-client/systemd/`。

---

## 六、手工打包单段（测试或导出前）

在 214 上，对已关闭且 `uploaded: false` 的段：

```bash
SEG=/home/server/cache/ego-lan-214/segments/sessions/sess_xxx/segments/seg_yyy
OUT="$SEG/.upload/${SEG##*/}.tar.zst"
mkdir -p "$SEG/.upload"
cd /home/server/workspace/ego-studio
.venv/bin/python -c "
from pathlib import Path
from ego_capture_studio.capture.segment_tar_zst import pack_segment_tar_zst
pack_segment_tar_zst(Path('$SEG'), Path('$OUT'))
print('packed:', '$OUT')
"
```

拷到 U 盘或 `export/` 后，在 34 采集页拖传 `OUT` 即可。

---

## 七、与旧文档的差异说明

[ego-lan-214-segment-storage-and-upload.md](./ego-lan-214-segment-storage-and-upload.md) 第八节曾写「无开箱即用离线导入」——**现已支持** 34 采集页浏览器导入 `.tar.zst`（与 HTTP ingest 同链路）。U 盘场景仍为：**拷 tar.zst 到有浏览器的机器上拖传**，不能直接把原始 `segments/` 目录拷到 34 磁盘代替 ingest。

---

## 八、相关文件索引

| 主题 | 路径 |
|------|------|
| 段存储实现 | `data-lab-platform/ego-stream-client/segment_store.py` |
| tar.zst 打包 | `data-lab-platform/ego-stream-client/segment_tar_zst.py` |
| 网络上传客户端 | `data-lab-platform/ego-stream-client/segment_upload.py` |
| 采集 systemd | `data-lab-platform/ego-stream-client/systemd/ecs-record-oak-stream.service` |
| 上传 systemd（默认不开） | `data-lab-platform/ego-stream-client/systemd/ecs-upload-segments-loop.service` |
| 采集/上传 target | `ecs-oak-capture-stack.target` / `ecs-oak-upload-stack.target` |
| 浏览器导入前端 | `data-lab-platform/client/import-core.js` |
| 完整数据流文档 | `docs/ego-lan-214-segment-storage-and-upload.md` |
