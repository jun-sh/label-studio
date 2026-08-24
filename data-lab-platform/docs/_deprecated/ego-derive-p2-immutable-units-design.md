# 派生管线 P2 改造设计：不可变派生单元 + Manifest 视图

> 状态：Phase D 已实现（默认 unit-only，legacy 增量路径已删除）（2026-08-20）
> 适用站点：ego-001（130 采集 + 34 平台），后续推广至全部 ego 站点
> 前置版本：v0.0.13-p1（fix2 + session.READY derive-status + P1.1 增量派生 + copy 拼接/4 路并行 mux）
> 关联文档：`ego-001-phase0-design-freeze.md`、`ego-001-v0.0.13-fix2-grayscale-runbook.md`

## 0. 目标与非目标

**目标**
1. 消灭"增量合并"整类 bug：派生过程不再修改任何历史产物
2. mux 降到秒级：分块并行编码，吃满 50–70% 的 128 核
3. 崩溃自愈：任何时点 kill -9 / 断电，重跑必收敛到 READY，无需人工清理
4. 保持 LeRobot v3 兼容：Viewer、Collection 页、训练导出（ego-deliver）不感知底层改动

**非目标**
- 不改采集端/上传协议（tar.zst 分段上传不变）
- 不引入 MCAP 格式（只借鉴其"不可变 + 追加式 + 自描述"哲学）
- 不做分布式（单机 128 核内的并行）

## 1. 现状问题（一句话诊断）

所有 session 被缝合进全局 `episode_000`：一份全局 frame_map、一份大 parquet、每相机一个大 MP4。**每次新采集都要原地改写这四类全局文件**，任何中断都会让它们互相不一致。

实际发生过的故障（2026-08 P0 灰度期间）：
- frame_map `length=4061` vs parquet 3329 行不一致（多次中断派生后全局文件脱钩）
- 增量 mux concat 失败，MP4 停在 3085 帧而 parquet 已到 3329
- 增量派生 v1 全局偏移算错，数据从 2826 行被覆写成 259 行
- 脏 `deriver.lock`、孤儿 `session.DERIVING` 标记，需要手动 rm
- 全局 `_staging` JPG 池常驻数千文件，占盘且易与 frame_map 脱钩

**根因**：可变的全局聚合状态。派生不是纯函数，中断即损坏不变量。

**佐证**：站点数据本就是 LeRobot v3.0 数据集（`info.json` 的 `data_path`/`video_path`
均为 `chunk-{chunk_index:03d}/file-{file_index:03d}` 模板），v3 原生支持多 episode、
每 episode 独立文件。"一个 session = 一个 episode = 独立 parquet + 独立 MP4"是
LeRobot 标准形态；现状把所有 session 缝进 `episode_000` 反而是非标准做法
（也是 Viewer imu 曲线跨 session 锯齿的来源）。

## 2. 目标架构：三层分离

```
L0 原始层（不可变，唯一事实源）   raw/segments/<sess>/*.tar.zst
L1 派生单元层（写完即不可变）     derived/<sess>/...     ← 派生 = 纯函数 f(L0)
L2 站点视图层（可整体重建）       meta/ data/ videos/    ← LeRobot v3 数据集，由 publisher 从 manifest 生成
```

核心原则：
- **L1 单元只增不改**：失败/中断的处理永远是"删掉该 session 的单元目录重跑"（几百帧，秒级）
- **全局帧偏移从派生逻辑中彻底消失**：单元内 frame_index 一律从 0 开始（episode 内局部索引，正是 LeRobot 语义）；偏移只在 publisher 写 episodes 元数据时按 manifest 顺序计算
- **L2 是缓存**：`ego-derive rebuild-view` 可随时从 L1 + manifest 全量重建

## 3. 目录结构

```
data-storage/stream/ego-001/
├── raw/segments/<sess>/seg_000001.tar.zst        # L0（现状不变）
│
├── derived/                                       # L1 派生单元（新增）
│   ├── _tmp.<attempt-uuid>/                       # 进行中的尝试，可随意 GC
│   └── sess_ba147.../                             # 提交后不可变
│       ├── unit.json                              # 单元清单（见 §4.1）
│       ├── data.parquet                           # 本 session 行，frame_index 0..N-1
│       ├── data.jsonl
│       ├── imu.parquet
│       └── videos/
│           ├── observation.images.camera_front_left.mp4
│           ├── observation.images.camera_front_right.mp4
│           ├── observation.images.camera_rear_left.mp4
│           └── observation.images.camera_rear_right.mp4
│
├── manifest/                                      # 站点清单（新增）
│   ├── journal.jsonl                              # 追加式事件日志（唯一可信状态源，见 §4.2）
│   └── manifest.json                              # 物化视图，可由 journal 重建（见 §4.3）
│
├── meta/  data/  videos/                          # L2 LeRobot v3 视图（publisher 维护）
│   ├── meta/info.json                             # total_episodes=N（不再恒为 1）
│   ├── meta/episodes/chunk-000/file-000.parquet   # 标准 v3 episodes 元数据
│   ├── data/chunk-000/file-000.parquet            # episode 0 = sess A（hardlink 自 L1）
│   ├── data/chunk-000/file-001.parquet            # episode 1 = sess B
│   └── videos/<key>/chunk-000/file-XXX.mp4        # 每 episode 一个（hardlink 自 L1）
│
└── state/
    ├── derive.lock                                # 带租约（见 §7）
    └── sessions/<sess>/session.READY              # 兼容保留，PUBLISHED 时写
```

要点：
- **L2 数据文件是 L1 的 hardlink**（同文件系统零拷贝），publish 不产生 I/O 大头
- episode i → `chunk_index = i // 1000`，`file_index = i % 1000`，与 `info.json` 的 `chunks_size: 1000` 一致
- 旧的 `meta/episodes/episode_000.json`（自定义全局 frame_map）废弃，信息由 manifest + 标准 episodes parquet 承载
- 全局 `_staging/` JPG 池废弃 → 帧解压到 `_tmp.<uuid>/frames/<key>/`，单元提交后即删（磁盘占用大降）

## 4. Manifest 格式

### 4.1 `derived/<sess>/unit.json`（单元清单，提交时原子写入）

```json
{
  "version": 1,
  "session_id": "sess_ba147cebf1304a6d95148ba7d0e3e383",
  "station_id": "ego-001",
  "fps": 30,
  "frames": 244,
  "source_segments": [
    { "segment_id": "seg_000001", "sha256": "ab12...", "frame_count": 244 }
  ],
  "artifacts": {
    "data.parquet":  { "rows": 244, "bytes": 183220, "sha256": "cd34..." },
    "data.jsonl":    { "rows": 244, "bytes": 402113, "sha256": "ef56..." },
    "imu.parquet":   { "rows": 976, "bytes": 88121,  "sha256": "0a1b..." },
    "videos/observation.images.camera_front_left.mp4":  { "frames": 244, "bytes": 9120344, "sha256": "2c3d..." },
    "videos/observation.images.camera_front_right.mp4": { "frames": 244, "bytes": 9011200, "sha256": "..." },
    "videos/observation.images.camera_rear_left.mp4":   { "frames": 244, "bytes": 8899221, "sha256": "..." },
    "videos/observation.images.camera_rear_right.mp4":  { "frames": 244, "bytes": 9077452, "sha256": "..." }
  },
  "gate": { "version": 2, "checks_passed": 6, "checks_total": 6 },
  "derive": {
    "attempt": 1,
    "pipeline_version": "p2.0",
    "started_at": "2026-08-20T03:10:00Z",
    "elapsed_ms": 6120,
    "encode": { "preset": "veryfast", "chunk_frames": 256, "parallel_jobs": 4 }
  }
}
```

语义：`unit.json` 存在且校验和匹配 ⇔ 单元完整。它就是单元级 READY 判据，
不再依赖散落的 marker 文件推断。

### 4.2 `manifest/journal.jsonl`（追加式事件日志，唯一状态源）

```json
{"at":"...","event":"derive_started","session_id":"sess_ba147...","attempt":1}
{"at":"...","event":"unit_ready","session_id":"sess_ba147...","frames":244,"unit_sha256":"..."}
{"at":"...","event":"published","session_id":"sess_ba147...","episode_index":13,"from_index":3085,"to_index":3329}
{"at":"...","event":"derive_failed","session_id":"sess_x...","attempt":2,"reason":{"code":"MUX_ENCODE_FAILED","message":"..."}}
{"at":"...","event":"quarantined","session_id":"sess_x...","after_attempts":5}
```

只追加、永不改写。崩溃后 reconciler 读 journal + 扫磁盘即可恢复全部状态。

### 4.3 `manifest/manifest.json`（物化视图，供 API/Viewer 快速读）

```json
{
  "version": 1,
  "station_id": "ego-001",
  "updated_at": "2026-08-20T03:11:02Z",
  "total_frames": 3329,
  "episodes": [
    { "episode_index": 0,  "session_id": "sess_aaa...",   "frames": 259, "from_index": 0,    "to_index": 259 },
    { "episode_index": 13, "session_id": "sess_ba147...", "frames": 244, "from_index": 3085, "to_index": 3329 }
  ],
  "pending":     [ { "session_id": "sess_new...", "state": "DERIVING", "attempt": 1 } ],
  "quarantined": [ ]
}
```

`derive-status` API 直接从这里出数（`markers/total/parquetRows` 等字段形状保持不变，
Collection 页零改动）。

## 5. 派生单元流程（单 session，纯函数）

```
输入: raw/segments/<sess>/*.tar.zst
1. journal ← derive_started;  创建 derived/_tmp.<uuid>/
2. 校验归档 (sha256 + tar 结构)
3. zstd -T0 解压 → _tmp/frames/<key>/frame_000000.jpg ... (局部索引，无全局偏移!)
4. 并行执行(互不依赖):
   ├── parquet + jsonl 写入 (episode 内 index 0..N-1)
   ├── IMU ingest → imu.parquet
   └── 4 路相机 × 分块并行编码 (§6) → videos/<key>.mp4
5. 单元级 ready-gate: G1 索引连续 / G2 四路 MP4 帧数==rows / G3 IMU 覆盖 / ...
6. 写 unit.json (含全部 sha256) → fsync → rename _tmp.<uuid> → derived/<sess>/
7. journal ← unit_ready
失败任意步 → journal ← derive_failed; _tmp 留给 reconciler GC; 重试 = 从第 1 步重来
```

**Publisher**（unit_ready 之后，幂等）：

```
1. hardlink data.parquet → data/chunk-XXX/file-YYY.parquet   (tmp+rename)
2. hardlink 4 路 mp4    → videos/<key>/chunk-XXX/file-YYY.mp4
3. 重写 meta/episodes parquet + info.json (小文件, tmp+rename, 可从 manifest 重建)
4. journal ← published; 刷新 manifest.json; 写 session.READY (兼容)
任意步崩溃 → reconciler 重跑 publisher, 每步 check-then-write, 天然幂等
```

## 6. 分块并行编码（吃满 128 核）

背景：x264 单进程 `-threads 0` 有效扩展上限约 16–32 线程，128 核机器上正确的
扩展轴是**进程级并行**（Netflix/YouTube shot-based parallel encoding 思路）。

- 每路相机把 N 帧切成 **256 帧/块**，每块一个独立 ffmpeg
  （`libx264 -preset veryfast -threads 2`，闭合 GOP），全部丢进
  **全局信号量调度的 worker pool**
- 块产物用 ffconcat `-c copy` 拼成该相机的单元 MP4（纯 I/O，毫秒级）
- 并发预算：`DERIVE_CPU_BUDGET=0.6`（默认）→ 信号量 = `floor(128 × 0.6 / 2) ≈ 38`
  个并发 ffmpeg；跨相机、跨 session 共享同一预算
- JPEG 解码（ffmpeg mjpeg 单线程）同样被分块并行摊平
- 预留 `DERIVE_ENCODER=x264|nvenc`：有 GPU 时切 `h264_nvenc`，编码彻底移出 CPU

**预估耗时**

| 场景 | 现状（v0.0.13-p1） | P2 分块并行 |
|------|-------------------|-------------|
| 常规 trip（~244 帧 × 4 路） | 60–90s+（串行全量重编码） | **2–5s**（4 块并行） |
| 全站重建（3329 帧 × 4 路 = 52 块） | 数分钟 | **<15s**（两波排完） |

## 7. 鲁棒性机制

| 机制 | 设计 |
|------|------|
| 租约锁 | `derive.lock` 含 `{owner, pid, heartbeat_at, ttl_s:60}`，每 10s 心跳；过期即可被抢占。彻底告别手动 rm 脏锁 |
| Reconcile 循环 | derive-worker 每 10s：扫 journal + 磁盘 → 派生 pending、补发 unpublished、GC 超时 `_tmp.*`、指数退避重试 failed（最多 5 次后 quarantine 并在 derive-status 报警） |
| 原子写 | 一切落盘 = tmp + rename (+ fsync)；已提交文件零改写 |
| fsck | `ego-derive fsck [--repair]`：单元级（校验和、MP4 帧数==rows）+ 视图级（hardlink 存在、episodes 元数据==manifest、info 总数）。`--repair` = 重派生受损单元；`rebuild-view` 重建 L2 |
| 故障注入测试 | 在每个步骤边界 kill -9、模拟磁盘满、截断归档；断言性质：**任何中断后 reconciler 收敛到 PUBLISHED**（crash-only 设计的可验证定义） |

状态机（journal 驱动）：

```
UPLOADED → DERIVING(attempt n) → UNIT_READY → PUBLISHED
              ↓ 失败                                ↑
         DERIVE_FAILED —(退避重试≤5)——————————————————┘
              ↓ 超限
         QUARANTINED（人工介入，derive-status 显式报警）
```

## 8. 迁移步骤（四阶段，可回滚）

**Phase A — 并行实现（不动现网行为）**
- 新增 `derive/unit.mjs`、`derive/publisher.mjs`、`derive/manifest.mjs`、`derive/encode-pool.mjs`
- Feature flag `DERIVE_LAYOUT=legacy|unit`，默认 legacy
- 单测 + 故障注入测试全绿后进入 Phase B

**Phase B — 存量迁移（现有 13 个 session，一次性脚本）**
1. 停 derive-worker；备份 L2：`mv data data.bak-20260820`（videos/meta 同理）
2. 迁移器按现有 frame_map 顺序，对每个 session 从 L0 原始归档**并行**派生单元
   （13 单元 × 秒级 ≈ 2 分钟内）
3. publisher 顺序发布 0..12 号 episode → `fsck` 全绿
4. Viewer/Collection 人工验收（多 episode 切换、每 trip 独立播放、
   imu 曲线不再有跨 session 锯齿）
5. 回滚预案：`DERIVE_LAYOUT=legacy` + 恢复 `.bak` 目录，5 分钟内可回

**Phase C — 新数据切换**
- `DERIVE_LAYOUT=unit` 上线（v0.0.14-p2 镜像）；后续采集 trip 直接在新路径上跑，
  作为灰度验收

**Phase D — 清理**
- 删除 legacy 增量合并代码（`mergeFrameMapIncremental`、`muxOneCameraIncremental`、
  全局 `_staging` 池等）；`.bak` 保留 7 天后删

## 9. 兼容性影响清单

| 消费方 | 影响 | 处理 |
|--------|------|------|
| LeRobot Studio Viewer | 从 1 个 episode 变 N 个 | v3 原生支持，episode 选择器直接可用；跨 session 锯齿问题自然消失 |
| Collection 页 (`collection_viz.html`) | 无 | derive-status API 字段形状不变，改由 manifest 出数 |
| `derive-status` API (`stream-ingest.mjs`) | 内部改造 | 读 manifest.json 代替扫 marker/jsonl，更快更一致 |
| ego-deliver / 训练导出 | 简化 | 标准多 episode v3 数据集，`STREAM_SESSION_SINGLE_EPISODE=0`（一次采集=一个样本）的语义与存储结构终于一致 |
| P0 灰度脚本 | 无 | 成功判据仍是 session.READY |

## 10. 风险与权衡

- **episodes 元数据是唯一"重写型"文件**：每次 publish 重写 `meta/episodes/*.parquet`
  与 `info.json`。缓解：文件小（KB 级）、tmp+rename 原子、可从 manifest 无损重建，
  fsck 覆盖。
- **hardlink 要求 L1/L2 同文件系统**：当前同盘成立；跨盘部署时 publisher 自动退化为 copy。
- **Viewer 展示从"整站连续时间轴"变为 N 个独立 episode**：这是语义修正而非退化
  （训练消费本来就按 episode），但需向操作员说明。
- **分块编码的块边界 GOP**：每块独立 IDR 起始，copy 拼接安全；代价是每 256 帧
  多一个 I 帧，码率增幅 <2%，可接受。

## 11. 实施顺序建议

1. Phase A 核心模块 + 测试（工作量最大头）
2. Phase B 存量迁移脚本 + ego-001 实盘迁移验收
3. Phase C 切换 + 若干新 trip 灰度
4. Phase D 清理 legacy

改造期间现网 P0 采集不受影响（legacy 路径继续工作）。
