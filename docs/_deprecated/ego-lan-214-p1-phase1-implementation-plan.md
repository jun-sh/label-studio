# ego-lan-214 阶段一实施计划（精简版 · W1–W2）

**原则**：最小侵入 · 快速落地 · 只解耦「34 签收 vs 派生」  
**214**：`ecs-oak-upload-stack` 为批量主通道，**零改造**  
**浏览器**：单段应急补传，**非**批量推荐路径 · 策略见 [ego-lan-214-upload-channel-policy.md](./ego-lan-214-upload-channel-policy.md)  

完整历史版本见 git 历史；本文档为当前执行基线。

---

## 一、范围

### 必做（W1–W2）

| 项 | 说明 |
|----|------|
| Raw 落盘 | `raw/segments/{session}/{seg}.tar.zst` + manifest + SHA256 |
| 进程内派生队列 | `derive-async.mjs`，复用 `processTarZstFromFile` |
| `DERIVE_ASYNC` | 默认 `0`；单站 `DERIVE_ASYNC_EGO_LAN_214=1` 灰度 |
| 双入口 | Agent `POST .../upload` + 浏览器 `.../import` |
| 段状态 API | `GET .../segments`、`POST .../derive-retry` |
| W2 验收 | 214 upload stack 传 21 段 → 6300 帧全绿 |

### 后置（非阶段一必做）

- 五态 UI 改造
- 独立 derive-worker 容器
- Grafana / 全链路监控面板

---

## 二、架构（一行）

```text
214 Agent / 浏览器 → 34 Raw 签收（秒级）→ stream-ingest 内队列 → 异步派生（现有代码）
```

---

## 三、里程碑

### W1 收尾清单（当前）

- [x] `derive-async.mjs` + 双入口接入
- [x] `DERIVE_ASYNC` / `DERIVE_ASYNC_EGO_LAN_214` docker-compose 配置
- [x] `ego-lan-214-sync-regression-smoke.sh`（A1）
- [x] `ego-lan-214-reimport-verify.sh` 扩展（`CHECK_DERIVE_ASYNC=1` → A2–A8）
- [x] 运行 sync regression smoke PASS
- [ ] W2 灰度 + 214 upload stack 21 段

### W2（灰度验收）

- [ ] `DERIVE_ASYNC_EGO_LAN_214=1` + 重启 stream-ingest
- [ ] 214：`systemctl --user start ecs-oak-upload-stack.target`
- [ ] 21 段 pending=0
- [ ] `ego-lan-214-reimport-verify.sh` 全 PASS + 回放目视

---

## 四、阶段一验收标准

### 4.1 功能验收（W2 出口）

| ID | 标准 |
|----|------|
| **A1** | `DERIVE_ASYNC=0`：Agent/浏览器行为与改造前一致（同步链） |
| **A2** | `DERIVE_ASYNC=1`：单段 POST 后 **5s 内** `raw/segments/...` 存在且 SHA256 正确 |
| **A3** | `DERIVE_ASYNC=1`：上传响应 **不阻塞** ffmpeg（`deriveAsync: true`，`framesCommitted: 0`） |
| **A4** | 21 段经 **214 upload stack** 上传后，全部 `state/segments.json` → `derived` |
| **A5** | `jsonl = parquet = 四路 MP4 = 6300` |
| **A6** | 采集页回放时间轴与四窗格同步 |
| **A7** | 人为损坏 mp4 后 `derive-retry` 成功，**无需 214 重传** |
| **A8** | `DERIVE_ASYNC=0` 一键回退，业务不中断 |

### 4.2 DoD 否决项（四条红线）

1. 214 零侵入  
2. LeRobot v3 热层格式契约不变  
3. `tar.zst` Raw 为唯一可信源，派生可重建  
4. 可灰度、可回退  

---

## 五、如何保证执行效果

| 手段 | 做法 |
|------|------|
| **开关隔离** | 默认 `DERIVE_ASYNC=0`，仅 `ego-lan-214` 灰度 `=1` |
| **代码复用** | 派生 = 现有 `processTarZstFromFile`，不重写 mux/jsonl |
| **自动化验收** | `ego-lan-214-reimport-verify.sh` + Raw/状态检查 |
| **主通道统一** | W2 **只用 214 upload stack**，不用浏览器批量 |
| **可恢复** | 启动时 `resumeDeriveQueuesForAllStations()` |
| **失败可修** | `POST .../derive-retry` 从 Raw 重跑 |
| **回退演练** | W2 前演练 `DERIVE_ASYNC=0` + restart |

### 建议 W2 操作清单

```bash
# ① A1 同步回归（灰度前必跑）
bash data-lab-platform/scripts/ego-lan-214-sync-regression-smoke.sh

# ② 开启单站灰度（.env 或 export 后 recreate）
DERIVE_ASYNC_EGO_LAN_214=1 docker-compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml up -d stream-ingest

# ③ 214 批量上传（主通道）
systemctl --user start ecs-oak-upload-stack.target
journalctl --user -u ecs-upload-segments-loop -f   # pending=0 后 stop

# ④ W2 全量验收
CHECK_DERIVE_ASYNC=1 bash data-lab-platform/scripts/ego-lan-214-reimport-verify.sh
```

---

## 六、执行风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| **双路径维护** | sync/async 行为漂移 | 派生单函数；A1 回归必跑 |
| **214 误判上传成功** | Agent 收到 202 但派生未完成 | W2 前对齐：202=Raw 签收；214 仍 mark uploaded（阶段二再拆） |
| **磁盘占用** | Raw + 热层双倍 | 7 天保留；Derived 后可选清 Raw |
| **进程内队列** | stream-ingest 重启丢 in-memory 排队 | `state/segments.json` + 启动 resume |
| **manifest peek 失败** | 浏览器无段名时无法 Raw 落盘 | 文件名须 `seg_XXXXXX.tar.zst` |
| **mux 历史问题** | 派生仍可能 mux_fail | derive-retry；Raw 不重传 |
| **灰度遗漏回退** | 生产站误开 async | 仅 per-station env；文档写明 |
| **并发上传** | 多段同时 POST | 单站 derive 串行；Raw 写入按段隔离 |

---

## 七、环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `DERIVE_ASYNC` | `0` | 全局 async 派生 |
| `DERIVE_ASYNC_EGO_LAN_214` | 未设 | 单站覆盖（优先于全局） |

---

## 八、代码落点

| 文件 | 职责 |
|------|------|
| `derive-async.mjs` | Raw、状态、队列、derive-retry |
| `stream-ingest.mjs` | Agent 上传 `DERIVE_ASYNC` 分支 |
| `import-handlers.mjs` | 浏览器单段应急 `DERIVE_ASYNC` 分支；`deriveStatus` 透出 poll API |
| `import-core.js` | Agent 引导、批量警告、异步完成文案 |
| `ingest-server.mjs` | segments / derive-retry 路由 |

---

## 九、版本记录

| 日期 | 说明 |
|------|------|
| 2026-07-03 | 精简为 W1–W2；214 Agent 主通道；进程内队列 |
| 2026-07-03 | W2：浏览器通道剥离降级；upload-channel-policy；验收改 Agent |
