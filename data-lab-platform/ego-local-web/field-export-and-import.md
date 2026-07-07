# ego-lan-214 现场采集：数据存哪、怎么打包、怎么导入 34

适用设备：**10.10.10.214**（站点 `ego-lan-214`）  
手机控制页：`http://192.168.4.1:8080/`（热点）或 `http://10.10.10.214:8080/`（实验室网）  
平台导入页：[http://10.10.10.34:8080/collection?station=ego-lan-214](http://10.10.10.34:8080/collection?station=ego-lan-214)

本文说明：**按下「开始录制」后原始数据落在哪**、**离线打包从哪读、写到哪**、**U 盘与 34 导入**的完整步骤。

技术细节见：[ego-edge-offline-upload-and-deployment.md](../../docs/ego-edge-offline-upload-and-deployment.md)、[ego-lan-214-segment-storage-and-upload.md](../../docs/ego-lan-214-segment-storage-and-upload.md)。

---

## 一、先记住三张「地图」

| 阶段 | 路径 | 里面是什么 | 谁写入 |
|------|------|------------|--------|
| **A. 录制中（内存热写）** | `/dev/shm/ego-capture-active/...` | 正在写的 open 段 | 采集栈 `ecs-oak-capture-stack` |
| **B. 原始段（本机硬盘）** | `/home/server/cache/ego-lan-214/segments/` | 已关闭的段：`manifest.json`、`rows.jsonl`、`frames/*.bin` | 采集栈（关段时从 A 搬到 B） |
| **C. 导出成品（可拷 U 盘）** | `/home/server/export/ego-lan-214/ready/YYYYMMDD/` | `seg_xxx.tar.zst` 单段包 | 离线脚本 `export-offline.sh` |

**8080 网页不存数据**，只通过 `systemctl` 启停采集栈。数据始终在 214 本机路径 A → B → C。

---

## 二、按下「开始录制」之后，数据存在哪里？

### 2.1 手机点了什么、底层做了什么

```text
采集员点「开始录制」
    → ego_web.py 写入全新 sessionId 到 checkpoint.json（每次启停独立 episode）
    → ego_web.py 执行：systemctl --user start ecs-oak-capture-stack.target
    → 启动 OAK 四路相机 + IMU 采集进程（ego-stream-client，未改动的采集栈）
    → 创建或续写 session，在内存盘开始写第一个 open 段
```

此时 **Web 服务本身不写任何采集文件**。

### 2.2 录制过程中：先写内存，关段后落硬盘

采集栈采用 **tmpfs 热写 + 关段落盘** 策略：

```text
正在写的段（open）
  路径：/dev/shm/ego-capture-active/sessions/sess_<uuid>/segments/seg_00000N/
  内容：manifest.json（closed=false）、rows.jsonl、frames/00000000.bin …
        ↑ 每帧相机 JPEG 打在 .bin 里，IMU/位姿写在 rows.jsonl

约每 **45 秒** 或 **300 帧**（先到先关）在**持续录制过程**中自动「关段」
    → closed=true
    → 整目录原子搬到本机硬盘：

  路径：/home/server/cache/ego-lan-214/segments/sessions/sess_<uuid>/segments/seg_00000N/
  内容：同上，但已是持久化副本
```

> **重要：45 秒 / 300 帧只用于「录制中自动切段」，不是最短录制时长。**  
> 不足 45 秒就点「结束录制」时，**当前这一段**应在收尾时关段并落盘，**不必等满 45 秒**。

因此：

- **录制 ≥ 45s（或 ≥ 300 帧）**：录制过程中就会有关段陆续出现在硬盘 `segments/`。
- **录制 < 45s**：录制过程中硬盘上可能**还没有新段**；**点「结束录制」后**最后一段才应从内存搬到 `segments/`。

### 2.2.1 相机初始化阶段：网页显示什么？会存哪些数据？

点「开始录制」后，采集进程会经历 **暖机（warming）** 再进入 **真正写帧（recording）**。网页状态与底层一致：

| 网页状态 | 含义 | 是否计时 |
|----------|------|----------|
| 正在启动采集服务（starting） | 等待 `systemctl start` 完成 | 否 |
| **正在准备录制（warming）** | 采集栈已运行，相机连接 / 3s 探针 / 四路预热中，**尚未** `writer.append_frame` | **否** |
| **正在录制中（recording）** | 日志出现 `capture-only session=` 或内存段里已有 `frames/*.bin`，**开始同步写视觉+IMU** | **是**（从此时起） |

> 修复说明：旧版网页在采集栈一 `active` 就显示绿条计时，会把 5～8 秒初始化算进「录制时长」，造成误解。现版仅在 **真正写帧后** 才绿条计时。

#### 初始化阶段（warming）会写入 segments 吗？

**不会写入段数据（无 rows.jsonl / frames/*.bin）。** 此阶段可能发生：

| 动作 | 写入位置 | 是否算「采集数据」 |
|------|----------|-------------------|
| 连接 OAK、四路预热 | 无段文件 | 否 |
| 3 秒 `record_episode` 探针 | **仅进程内存**，不落 segment | 否 |
| 写相机内参 | `segments/sessions/sess_xxx/meta/camera_intrinsics.json` | 元数据，非帧数据 |
| 可选心跳 | 发往 34 的网络请求 | 非本地段 |

#### 真正开始存储是什么时候？

以日志 **`capture-only session=...`** 为准（约暖机结束后）。此后每一帧调用 `writer.append_frame`，**同一帧内** 同时写入：

- 四路相机 → `frames/XXXXXXXX.bin`
- IMU / 位姿 / 手部位姿等 → `rows.jsonl`

**不存在「先写 IMU、后补视觉」的半成品段**；视觉与表格行在同一写帧路径里提交。

#### 214 上没有 parquet

边缘站只存 **`rows.jsonl` + `frames/*.bin`**。**parquet** 是数据导入 **34 平台** 后由 ingest 后台从 jsonl 生成的 LeRobot v3 成品，不在 214 本地产生。

### 2.3 本机原始段的完整目录结构

```text
/home/server/cache/ego-lan-214/segments/
├── checkpoint.json                 # 当前 session 断点（正在录哪一段）
├── registry.json                   # 各 session 登记
└── sessions/
    └── sess_<uuid>/
        ├── meta/
        │   └── camera_intrinsics.json
        └── segments/
            ├── seg_000001/
            │   ├── manifest.json   # closed、uploaded 等状态
            │   ├── rows.jsonl      # 每帧元数据与观测
            │   └── frames/
            │       ├── 00000000.bin
            │       └── ...
            ├── seg_000002/
            └── ...
```

单段 `manifest.json` 关键字段：

| 字段 | 含义 |
|------|------|
| `closed` | `true` = 本段已录完，可打包 |
| `uploaded` | `false` = 尚未导出/上传；`true` = 已交接，可删源段 |

网页底部 **「已生成段数」** 只统计 **硬盘** `segments/` 下的 `seg_*` 目录，**不包含**仍在内存 `/dev/shm/` 里、尚未落盘的段。短录制后段数不变，**不代表没录到**，需按下面 2.6 节排查。

### 2.4 按下「结束录制」之后

```text
采集员点「结束录制」
    → ego_web.py 执行：systemctl --user stop ecs-oak-capture-stack.target
    → 采集进程退出前调用 writer.close()：
        ① 将当前 open 段 manifest 标为 closed=true（无论本段只有 3 秒还是 40 秒）
        ② 刷完 persist 队列中的帧
        ③ 从 /dev/shm/ego-capture-active/... 搬到 segments/...（路径 B）
    → 页面显示「正在保存数据」→「设备待机中」
```

**结束录制只做落盘与关段，不做打包。** 正常收尾后，本段应在 **路径 B** 且 `manifest.json` 里 **`closed: true`**。

若收尾不完整（见 2.6 节），数据可能仍留在 **路径 A**（`/dev/shm/`），`segments/` 里找不到。

### 2.5 录制阶段小结（采集员视角）

| 动作 | 数据在哪 | 要不要打包 |
|------|----------|------------|
| 开始录制 | 先在 `/dev/shm/ego-capture-active/...` 写 | 否 |
| 录制中（自动关段） | 已关闭段陆续进 `segments/.../seg_*` | 否 |
| 结束录制（正常） | 最后一段进 `segments/`，`closed=true` | **否** |
| 结束录制（异常） | 可能仍在 `/dev/shm/...`，`closed=false` | 否，需排障 |
| 班次末 | `segments/` 里 `closed=true, uploaded=false` | **由运维跑脚本** |

### 2.6 短录制（不足 45 秒）为什么硬盘上可能找不到？

这是现场最常见困惑，分三种情况说明。

#### 情况 A：正常（设计预期）

- 录制 10～40 秒，期间**不会**因 45 秒规则自动关段。
- 点「结束录制」且**收尾成功**后，应出现**一个**新目录，例如：

```text
/home/server/cache/ego-lan-214/segments/sessions/sess_<uuid>/segments/seg_000026/
├── manifest.json    ← "closed": true, "frame_count": 约 300（10s×30fps）
├── rows.jsonl
└── frames/*.bin
```

#### 情况 B：数据还在内存里（你当前很可能属于这种）

关段落盘是**异步**的：帧先写入 `/dev/shm/ego-capture-active/`，**只有 `closed=true` 后**才搬到硬盘。

若结束录制时进程过早退出、收尾未完成，会出现：

```text
/dev/shm/ego-capture-active/sessions/sess_<uuid>/segments/seg_000026/
├── manifest.json    ← "closed": false  ← 尚未关段
├── rows.jsonl       ← 可能仍为空（帧在队列里未刷完）
└── frames/*.bin     ← 可能已有部分帧文件
```

此时 **`segments/` 下没有对应目录**，`export-offline.sh` 也**不会**处理（只认 `closed=true`）。

**在 214 上排查：**

```bash
# 1. 当前 session（与 checkpoint 一致）
cat /home/server/cache/ego-lan-214/segments/checkpoint.json

# 2. 硬盘上最新段
find /home/server/cache/ego-lan-214/segments/sessions -type d -name 'seg_*' | sort | tail -3

# 3. 内存里未落盘的段（重点！）
find /dev/shm/ego-capture-active -name manifest.json 2>/dev/null | while read m; do
  grep -q '"closed":false' "$m" && echo "STUCK: $m" && cat "$m"
done

# 4. 最近一次采集日志（看实际录了多少帧）
journalctl --user -u ecs-record-oak-stream -n 30 --no-pager
```

2026-07-01 现场实测示例：短按停止后 `seg_000024`～`seg_000026` 留在 `/dev/shm/`，`closed=false`，而硬盘最新只到 `seg_000023`。

#### 情况 C：录制太短或停在「正在保存」前

- 实际写帧仅 **2～3 秒**（含在「开始录制」里等相机的时间，体感像 10 秒）。
- 在「正在保存数据」未完成时断电或再次点「开始录制」。

**采集员侧建议：**

1. 预览有画面后再计时，**至少录 15～20 秒**。
2. 点「结束录制」后，**等状态回到「设备待机中」**（不要立刻再点开始）。
3. 若需确认落盘，请运维 SSH 执行上面排查命令。

> 彻底修复「短停收尾不完整」需调整采集栈 `TimeoutStopSec` / `writer.close()` 时序（属 `ego-stream-client`，非 Web 层）。在此之前按上述流程操作可最大限度避免丢段。

---

## 三、离线打包：从哪打包、逻辑是什么、成品存哪？

### 3.1 何时执行、谁执行

- **时机**：当天或本批次采集全部结束后（确认手机页已是「设备待机中」）。
- **方式**：SSH 登录 214，执行一键脚本（与 Web 无关）：

```bash
bash /home/server/ego-web/export-offline.sh
```

### 3.2 打包「从哪里读」

脚本 **只读路径 B**，且 **只处理满足条件的段**：

```text
扫描根目录：/home/server/cache/ego-lan-214/segments/
遍历：sessions/*/segments/seg_*/
筛选：manifest.json 中 closed=true 且 uploaded=false
```

对每个命中段，读取其目录内的：

| 文件 | 作用 |
|------|------|
| `manifest.json` | 段元数据 |
| `rows.jsonl` | 逐帧记录 |
| `frames/*.bin` | 四路相机帧 |

通过 `ego-studio` 的 `pack_segment_tar_zst` 打成 **一个** `.tar.zst` 文件（与 34 网络上传用的格式相同）。

**不会**从 `/dev/shm/` 打包（录制结束后 shm 段已搬走或清空）。  
**不会**重复打包 `uploaded=true` 的段（幂等）。

### 3.3 打包「写到哪里」

采用 **staging → 校验 → ready** 两阶段，防止半截文件：

```text
① 写入中间态（打包中）
   /home/server/export/ego-lan-214/staging/seg_000012.tar.zst.part

② SHA256 校验通过后，原子移动到成品目录
   /home/server/export/ego-lan-214/ready/20250701/seg_000012.tar.zst

③ 同目录追加审计日志
   /home/server/export/ego-lan-214/ready/20250701/export-manifest.jsonl
```

`YYYYMMDD` 默认当天，可用 `--date` 指定。

### 3.4 打包成功后对原始段的处理

校验通过且落入 `ready/` 之后：

```text
1. 将该段 manifest.uploaded 设为 true
2. 删除路径 B 下对应源目录（默认开启）：
   rm -rf .../segments/sessions/sess_xxx/segments/seg_000012/
3. 释放段缓存配额（默认 256GB 待导出队列），避免重复打包
```

若 `ready/` 里 **已存在** 同名 `seg_xxx.tar.zst`，脚本视为已成功，仅补标 `uploaded` 并删源段。

### 3.5 数据流总图

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ 手机 http://192.168.4.1:8080/                                           │
│   开始录制 / 结束录制  →  仅 systemctl 启停 ecs-oak-capture-stack        │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ A. /dev/shm/ego-capture-active/.../seg_*     （录制中 open 段，内存）    │
│         │ 自动关段 或 结束录制关段                                         │
│         ▼                                                               │
│ B. /home/server/cache/ego-lan-214/segments/.../seg_*                     │
│         closed=true, uploaded=false  ← 离线脚本只处理这类                 │
│         │ export-offline.sh（班次末手动）                                │
│         ▼                                                               │
│ C. .../ready/YYYYMMDD/seg_*.tar.zst                                     │
│         │ 有网：214 Agent 批量上传（主通道）                              │
│         │ 无网：U 盘 → 34 单段应急「导入」补传                           │
│         ▼                                                               │
│ 34 ingest → LeRobot 可回放                                              │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.6 脚本文件位置

| 文件 | 路径（214 部署后） |
|------|-------------------|
| 一键入口 | `/home/server/ego-web/export-offline.sh` |
| Python 逻辑 | `/home/server/ego-web/export_offline.py` |
| 仓库源码 | `data-lab-platform/ego-local-web/scripts/export-offline.sh` |

### 3.7 磁盘配额与 `ego-export` 删除行为（必读）

#### `ego-export` 默认会删什么、不删什么

| 数据 | 路径 | 导出成功后 |
|------|------|-----------|
| **原始段（B 层）** | `segments/.../seg_*` | **会删除**（默认 `EGO_EXPORT_DELETE_AFTER=1`） |
| **导出成品（C 层）** | `export/ready/YYYYMMDD/*.tar.zst` | **会保留**（须人工在 34 确认导入后清理） |
| **正在录制的段（A 层）** | `/dev/shm/ego-capture-active/...` | 不关段则不碰 |

命令行说的「默认安全」指：**不加 `--reset` 不会误清全库**、可重复执行、校验失败不删源段——**不是**「导出后原始段永远留在本地」。

#### `EGO_SEGMENT_QUOTA_GB=256` 是什么意思？

**不是「一辈子只能录 256GB」**，而是本机 `segments/` 里 **尚未导出**（`closed=true, uploaded=false`）的段，合计最多占 **256GB** 磁盘。

- 可把它当作边缘机上的 **「待发货缓冲区」**，权威库在 34 平台。
- **`ego-export` 成功后源段删除 → 配额释放 → 可继续录**，没有生涯总量上限。
- **长期不导出**：队列堆满 256GB 后，系统会 **删掉最老的未导出段**（至少保留 1 段），保证录制不中断——**未交付数据会丢**。

配置位置：`ecs-record-oak-stream.service` drop-in（`EGO_SEGMENT_QUOTA_GB=256`）。

**214 生效方式**（更新仓库后）：

```bash
# 同步 systemd drop-in 后
systemctl --user daemon-reload
systemctl --user restart ecs-record-oak-stream.service  # 或重启 ecs-oak-capture-stack.target
```

#### 214 上硬盘不够时怎么办（行业惯例）

```text
/dev/shm/（录制中）     → 关段后搬进 segments/
segments/（256GB 队列） → ego-export 成功后删源段
export/ready/（成品）   → 拷走 + 34 确认导入后可删
34 平台                 → 长期保留（热层 100GB / 7 天 + 冷归档）
```

| 做法 | 说明 |
|------|------|
| **每班次 `ego-export`** | 释放 `segments/` 配额（推荐） |
| **34 导入成功后删 `ready/`** | 释放成品占盘：`rm -f export/.../ready/YYYYMMDD/*.tar.zst` |
| **排障保留源段** | `EGO_EXPORT_DELETE_AFTER=0`（不推荐生产） |

`export/ready/` **不计入** 256GB 配额，但若长期不清理，仍可能把 214 硬盘撑满。

### 3.8 `ego-export` 幂等判定：B（原料）与 C（成品）四种组合

每一段导出涉及两层数据（与 §3.5 总图一致）：

```text
B. 原始段（原料）                         C. 导出成品（交付物）
segments/.../seg_000013/                 export/ready/YYYYMMDD/seg_000013.tar.zst
  ├── manifest.json（状态单）                 └── 拷 U 盘 / 上传 34 的包
  ├── rows.jsonl
  └── frames/
```

正常顺序：**B 还在 → 打包 → 生成 C → 删掉 B（释放 segments/ 配额）**。  
你要的交付物是 **C**，B 只是临时原料。

#### 为什么需要「先看 C、再看 B」

`ego-export` 启动时会扫描待导出列表，再逐段处理。两段之间可能有时间差：

- 源段 B 已被异步删除（`EGO_EXPORT_DELETE_AFTER=1`、磁盘配额淘汰等）
- 但成品 C 早已生成（上次导出成功、或同段重复执行）

**旧逻辑**对每个段一律先读 `manifest.json`；若 B 已消失则报错，**一段失败会导致整批标红**——即使 C 已经存在（典型：`seg_000013` 源目录没了，但 `seg_000013.tar.zst` 已在 `ready/`）。

**现行逻辑**（`export_offline.py`）对每段 **先查 C，再查 B**：

| B（`segments/.../seg_*`） | C（`ready/.../*.tar.zst`） | 处理 | 结果 |
|---------------------------|----------------------------|------|------|
| **有** | **无** | 正常打包 → 校验 → 写入 C → 删 B | 最常见的新导出 |
| **有** | **有** | 视为已交付；补标 `uploaded` 并删 B（若还在） | 幂等，可重复执行 |
| **无** | **有** | 视为已成功，不报错 | 修复竞态；多跑无害 |
| **无** | **无** | 记 `skipped stale`，**跳过**，继续后面的段 | 列表过期项，不拖垮整批 |

#### 三种说法与上表对应

1. **「ready/ 里已有 tar → 视为成功（幂等）」**  
   对应上表第 2、3 行：只要 C 存在，就不再要求 B 必须在；重复 `ego-export` 不会重复打包，也不会因 B 已删而失败。

2. **「源目录已消失 → 不再整批失败」**  
   对应第 3 行：B 没了但 C 在（你遇到的 `seg_000013` 情况）→ 单段成功，其余段继续。

3. **「源和成品都没有 → 跳过，不阻断其他段」**  
   对应第 4 行：扫描列表里的「幽灵项」只打日志跳过，exit code 仍为 0（若无其他真失败）。

#### 判定流程（实现顺序）

```text
对每个待处理 seg_xxx：
  1. ready/seg_xxx.tar.zst 已存在？
       是 → 成功（若 B 还在则补删源段）
       否 ↓
  2. segments/.../seg_xxx/ 或 manifest.json 还在？
       是 → 打包 → 校验 → 写 C → 删 B
       否 → skipped stale（跳过）
```

#### 与「导出成功与否」的准则

**以 C（`ready/` 里的 `.tar.zst`）为准**，不以 B 是否仍在为准。  
B 被删只表示原料已清理；**C 在就不应算失败**。

#### 排障

| 现象 | 含义 | 处理 |
|------|------|------|
| 某段 `skipped stale` | 列表里有名无实，且 `ready/` 也无对应 tar | 一般可忽略；若该段本应存在，查是否被配额淘汰且从未导出 |
| 显示失败但 `ready/` 里已有该段 tar | 214 上 `export_offline.py` 过旧 | 同步仓库最新版到 `/home/server/ego-web/export_offline.py` 后重跑 |
| `summary ok=N failed=0` 但段数少于预期 | 部分段此前已导出（幂等跳过） | 正常；用 `ls ready/YYYYMMDD/*.tar.zst` 核对成品总数 |

---

## 四、离线一键导出：使用步骤

```bash
# 1. 确认采集已停（必须 inactive）
systemctl --user is-active ecs-oak-capture-stack.target

# 2. 执行导出
bash /home/server/ego-web/export-offline.sh

# 3. 查看成品
ls -lh /home/server/export/ego-lan-214/ready/$(date +%Y%m%d)/
cat /home/server/export/ego-lan-214/ready/$(date +%Y%m%d)/export-manifest.jsonl
```

### 4.1 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `EGO_SEGMENT_ROOT` | `/home/server/cache/ego-lan-214/segments` | **打包源**：原始段根目录 |
| `EGO_EXPORT_ROOT` | `/home/server/export/ego-lan-214` | **打包目标**根目录 |
| `EGO_EXPORT_DELETE_AFTER` | `1` | 成功后删 `segments/` 源段 |
| `EGO_CAPTURE_TARGET` | `ecs-oak-capture-stack.target` | 导出前检查采集已停 |
| `EGO_STUDIO_ROOT` | `/home/server/workspace/ego-studio` | 打包 Python 环境 |

### 4.2 可选参数

```bash
bash /home/server/ego-web/export-offline.sh --dry-run      # 只列出待导出段，不打包
bash /home/server/ego-web/export-offline.sh --date 20250701 # 指定 ready 子目录日期
EGO_EXPORT_DELETE_AFTER=0 bash .../export-offline.sh       # 打包但不删源段（排障）
```

### 4.3 打包 / 校验失败时怎么办？

| 情况 | 系统行为 | 运维处理 |
|------|----------|----------|
| 单段打包异常 | 该段保持 `uploaded=false`，清理 `staging/*.part` | 看终端报错，**重跑脚本** |
| SHA256 校验失败 | 不写入 `ready/`，不标记 uploaded | 查磁盘 / zstd，重跑 |
| 采集栈仍在跑 | 退出码 `2`，不处理任何段 | 手机先「结束录制」，再重跑 |
| `ready/` 已有同名 tar.zst | 补标 uploaded 并删源段 | 一般无需干预 |
| 部分成功部分失败 | 退出码 `1`，`summary ok=N failed=M` | 成功段已在 C；失败段仍在 B，修好后重跑 |

**原则：只有打包 + 校验 + 落入 `ready/` 全部成功，才 `uploaded=true` 并删除 B 中源段。**

---

## 五、上传到 34

**批量（主通道，有网）**：在 214 上导出到 `ready/` 后：

```bash
systemctl --user start ecs-oak-upload-stack.target
journalctl --user -u ecs-upload-segments-loop -f   # pending=0 后 stop
```

**单段应急（U 盘 / 无 Agent）**：见 5.2。

### 5.1 从成品目录拷到 U 盘（无网搬运）

**只拷路径 C**，不要拷 `segments/` 原始目录：

```bash
sudo mkdir -p /mnt/ego-export/ego-lan-214
sudo mount /dev/sdX1 /mnt/ego-export   # 以 lsblk 为准

cp -v /home/server/export/ego-lan-214/ready/$(date +%Y%m%d)/*.tar.zst \
      /mnt/ego-export/ego-lan-214/
sync
```

U 盘内结构：

```text
<U盘>/ego-lan-214/
├── seg_000001.tar.zst
├── seg_000002.tar.zst
└── ...
```

### 5.2 34 采集页单段应急补传

> **批量请勿使用浏览器多文件拖传**；W2 及生产批量验收均走 214 Agent。详见 [ego-lan-214-upload-channel-policy.md](../../docs/ego-lan-214-upload-channel-policy.md)。

1. 打开 [http://10.10.10.34:8080/collection?station=ego-lan-214](http://10.10.10.34:8080/collection?station=ego-lan-214)
2. Episodes → **「导入」** → 拖入 **单个** `.tar.zst`
3. 异步模式下显示 **「已上传，后台处理中」**（非「可回放」）；派生完成后刷新 Episodes 回放质检

同一段重复导入会提示「已存在，跳过」。

---

## 六、Web 控制页职责边界（与采集栈的关系）

| 组件 | 职责 | 不做什么 |
|------|------|----------|
| `ego_web.py`（8080） | 启停 `ecs-oak-capture-stack`、状态、预览、磁盘/段数 | **不**打包、**不**写 `segments/`、**不**写 `export/` |
| `ecs-oak-capture-stack` | 写 A/B 路径、关段、`manifest.closed` | **不**打 tar.zst（除非另开上传栈） |
| `export-offline.sh` | 读 B → 写 C → 标 uploaded、删 B | **不**启停采集 |

回滚 Web 服务不影响已落在 B 或 C 的数据；详见 `rollback.sh`。

---

## 七、角色分工

| 角色 | 做什么 | 数据触及路径 |
|------|--------|--------------|
| 采集员 | 开始/结束录制 | 间接产生 B（通过采集栈） |
| 运维 | 班次末 `export-offline.sh`、拷 U 盘 | B → C → U 盘 |
| 办公室数据员 | 34 回放、触发 pipeline | **Agent 上传** 或 U 盘单段补传 |

---

## 八、路径与源码索引

| 说明 | 路径 |
|------|------|
| 录制中（内存，可能未落盘） | `/dev/shm/ego-capture-active/` |
| **原始段（打包源，须 closed=true）** | `/home/server/cache/ego-lan-214/segments/` |
| 打包中间态 | `/home/server/export/ego-lan-214/staging/` |
| **导出成品（拷 U 盘）** | `/home/server/export/ego-lan-214/ready/YYYYMMDD/` |
| Web 控制页 | `data-lab-platform/ego-local-web/ego_web.py` |
| 离线导出脚本 | `data-lab-platform/ego-local-web/export_offline.py` |
| 一键入口 | `data-lab-platform/ego-local-web/scripts/export-offline.sh` |
| 段状态机（uploaded 等） | `data-lab-platform/ego-stream-client/segment_store.py` |
| tar.zst 打包实现 | `data-lab-platform/ego-stream-client/segment_tar_zst.py` |
| 34 流式数据 | `data-lab-platform/data-storage/stream/ego-lan-214/` |

---

## 九、常见问题

| 问题 | 答案 |
|------|------|
| 点开始录制后数据立刻在硬盘吗？ | **否**。先在内存 A 写；只有关段后（自动 ≥45s/300 帧，或手动结束录制收尾成功）才进硬盘 B |
| 不足 45 秒就结束录制，数据在哪？ | **应收尾进 B**；若 B 没有，先查 **A：`/dev/shm/ego-capture-active/`**（见 2.6 节） |
| 为什么录了约 10 秒 segments 没新目录？ | ① 相机初始化占去数秒，实际写帧可能只有 2～3 秒；② 收尾未完成，段卡在 shm；③ 「已生成段数」不统计 shm |
| 结束录制后数据在哪？ | 正常：B 下 `segments/.../seg_*` 且 `closed=true` |
| 打包从哪读？ | 只从 B，且只要 `closed=true && uploaded=false` |
| 打包后文件在哪？ | C：`export/ego-lan-214/ready/YYYYMMDD/*.tar.zst` |
| 打包后原始段还在吗？ | 默认**删除**（`EGO_EXPORT_DELETE_AFTER=1`） |
| 为什么看不到琥珀色「正在准备录制」？ | 旧版会把 `/dev/shm/` 里**上次失败遗留**的 open 段误判为已写帧，直接变绿。现版已按**本次采集启动时间**过滤；轮询间隔 0.8s |
| 能直接把 `segments/` 拷到 34 吗？ | **不能**，须 tar.zst 经 Agent 或单段浏览器导入 |
