# ego-lan-214 数据平台架构路线图（P0+ 出口 → P1 三阶段）

**文档性质**：阶段一正式立项基线 · 架构演进与开发依据  
**推进原则**：**保留入口 · 解耦链路 · 异步转化 · 逐步降级**（对齐大厂商商用化思路，不做一刀切移除）  
**三大原则**：上传与派生拆开 · 浏览器入口阶段一保留 · 段级全生命周期状态机  
**不动基石**：214 边缘采集 + `ego-export` + 不可变 `tar.zst` 段包（格式与导出逻辑全程不改造）

相关文档：

- 现场操作：[ego-lan-214-pipeline-runbook.md](./ego-lan-214-pipeline-runbook.md)
- P0+ 部署与验收（历史）：[ego-lan-214-p0p-mux-deploy.md](./ego-lan-214-p0p-mux-deploy.md)
- **阶段一实施计划**：[ego-lan-214-p1-phase1-implementation-plan.md](./ego-lan-214-p1-phase1-implementation-plan.md)
- 段存储与上传：[ego-lan-214-segment-storage-and-upload.md](./ego-lan-214-segment-storage-and-upload.md)

---

## 一、终态目标（对标商用数据平台）

### 1.1 核心架构分层

```text
边缘层（214，零改造）
  采集 → 本地分段落盘 → ego-export 生成 tar.zst 段包 → 上传 Agent 后台自动同步

中心层（34，分层解耦）
  1. Raw 层      原始 tar.zst 持久化 + 校验和，唯一可信源，永久可重建
  2. 派生层      独立 Worker 队列：解压 → jsonl → mux → parquet
  3. 服务层      回放、质检、数据集导出，只读派生结果
  4. 状态层      段级全生命周期状态机，全链路可观测、可重试
```

### 1.2 终态核心特性

| 特性 | 说明 |
|------|------|
| 上传秒级确认 | 文件落盘 Raw 层并完成校验即上传成功，**不等待**转码 |
| 派生全可重建 | mp4、parquet 损坏或失败时，从 Raw `tar.zst` 一键重算，**无需重传** |
| 无人值守同步 | 214 导出后 Agent 自动上传：断点续传、失败重试、带宽限速 |
| 状态语义清晰 | 上传成功 ≠ 可回放 ≠ 可训练，每步有明确门禁 |
| 浏览器仅作补充 | 单段应急补传；**非**批量数据主入口（**W2 起 Agent 为唯一批量主通道**） |

### 1.3 与当前实现的关系（过渡态说明）

| 维度 | 当前（P0+ 旧耦合链路） | 阶段一（`DERIVE_ASYNC=1`） | 阶段二终态 |
|------|------------------------|---------------------------|------------|
| 上传入口 | 浏览器拖传（lab 历史） | **214 Agent 批量主通道**；浏览器单段应急保留 | Agent 为主，浏览器辅助 |
| 上传热路径 | 同步解压 + jsonl + mux + parquet | **仅 Raw 落盘 + SHA256 校验即 ACK** | 同阶段一 |
| 派生 | 与上传同进程阻塞 | **derive-worker 异步全量派生** | 同阶段一 |
| UI「成功」 | ≈ 段 commit（jsonl + `.done`） | **「已上传」**（Raw Verified）≠ **「已就绪」**（Derived） | 五态总览 + Agent 进度 |
| 可信源 | `tar.zst`（214 ready）+ 34 stream | Raw 层 `tar.zst` 为 34 侧唯一可信源 | 同阶段一 |

---

## 二、阶段一启动门禁（已调整）

> **2026-07-03 决策**：终止旧同步耦合链路的增量修补；**阶段一立即为最高优先级**。  
> P0+ 旧链路全绿 **不再**作为阶段一启动前置条件。  
> 21 段数据验收改由 **新架构 Raw 重派生** 完成（见 [阶段一实施计划](./ego-lan-214-p1-phase1-implementation-plan.md) M3）。

### 2.1 P0+ 标准（历史参考，非启动门禁）

| 检查项 | 期望（seg_013–033，21 段） |
|--------|---------------------------|
| jsonl 行数 | **6300** |
| parquet 行数 | **6300**（= jsonl） |
| 四路 MP4 帧数 | 各 **6300** |
| 采集页回放 | 时间轴与四窗格画面同步 |
| 自动化脚本 | `ego-lan-214-reimport-verify.sh` 全 PASS |

### 2.2 新架构验收标准（M3，取代旧链闭环）

| 检查项 | 期望 |
|--------|------|
| Raw 层 | 21 段 `tar.zst` + SHA256 manifest |
| 上传 ACK | 30s 内全部 Verified（「已上传」） |
| 派生结果 | jsonl = parquet = 四路 MP4 = **6300** |
| 段状态 | 21 段 `Derived` |
| 回放 | 采集页同步无卡顿 |

### 2.3 衔接节奏

```text
① 阶段一 M1：Raw 层 + 段状态 API + DERIVE_ASYNC 门控
      ↓
② 阶段一 M2：derive-worker + derive-retry
      ↓
③ 阶段一 M3：21 段 Raw 上传/seed → 重派生 → 验收脚本 PASS
      ↓
④ 阶段一 M4–M5：五态 UI + ego-lan-214 灰度 DERIVE_ASYNC=1
      ↓
⑤ 阶段二：214 Agent 主通道
```

---

## 三、段级状态机（立项基线）

### 3.1 用户侧五态（简洁，不增加前端复杂度）

| 用户可见文案 | 内部主状态 | 含义 |
|--------------|------------|------|
| 上传中 | `Uploading` | 传输进行中 |
| 已上传 | `Uploaded` | Raw 层落盘且校验通过（见 3.2） |
| 处理中 | `Deriving` | derive-worker 解压 / jsonl / mux / parquet |
| 已就绪 | `Derived` | 派生产物验收通过，**可回放** |
| 失败 | `Failed` | 上传或派生失败，可定位、可重试 |

**不**在 UI 单独展示「已校验」等子态；审计与排障走 API / 日志。

### 3.2 内部子状态（实现层，用于审计与重试）

`Uploaded` 对内拆分为：

| 子状态 | 条件 | 用途 |
|--------|------|------|
| `Stored` | 字节已写入 Raw 路径 | 断点续传、磁盘审计 |
| `Verified` | SHA256 + manifest 段 ID 校验通过 | 对外映射为用户侧「**已上传**」 |

仅当 `Verified` 成功后，才进入 `Deriving` 队列。`Stored` 但校验失败 → `Failed`（可重传同文件）。

阶段二起 API 可返回 `stored_at` / `verified_at` / `sha256` 等字段；前端仍保持五态。

### 3.3 语义切割：阶段一 vs P0+「导入成功」

| | P0+（当前） | 阶段一上线后 |
|---|-------------|--------------|
| 浏览器操作 | 批量拖传（历史） | **单段应急**；批量引导 Agent |
| 弹窗「成功」 | 段 commit（jsonl + `.done`） | **「已上传，后台处理中」** = Raw `Verified` |
| 派生进度 | 隐含在后台 mux，无独立展示 | **「处理中」→「已就绪」** 独立展示 |
| 可回放时机 | commit 后等待 mux（易误解） | 仅 `Derived` |

**立项 Requirement**：阶段一 PRD 必须写明「操作路径不变，成功语义变更」，并提供迁移说明与 UI 文案对照表。

---

## 四、设计红线（阶段一 Definition of Done 否决项）

以下任一条被突破，**不得**合并阶段一主分支：

1. **边缘侧零侵入**  
   不要求 214 改采集栈、`ego-export`、`tar.zst` 包内结构或导出流程。

2. **下游零改造**  
   LeRobot v3 热层目录布局（`meta/`、`data/`、`videos/`）与 `ego-run-pipeline` 消费契约保持兼容；演进仅改变**如何产出**，不改变**产出格式契约**。

3. **平滑可回退**  
   每阶段具备功能开关（如 `DERIVE_ASYNC`）；支持灰度与回退，禁止断崖式一次性切换。

4. **原始包不可变**  
   Raw 层 `tar.zst` 为全链路唯一可信源；派生产物可丢弃、可重建；derive 不得修改或覆盖 Raw 对象。

---

## 五、阶段一：核心解耦 — Raw 层 + derive-worker（正式立项 PRD）

**优先级**：**当前最高（立即执行）** · **周期 4–6 周**  
**实施详情**：[ego-lan-214-p1-phase1-implementation-plan.md](./ego-lan-214-p1-phase1-implementation-plan.md)（M1–M5 里程碑与验收用例）

**阶段一落地策略（立项锁定）**：

- 开发全程默认 **`DERIVE_ASYNC=0`**，兼容现有 P0+ 同步链路，保障业务连续
- 阶段一代码合并后，先在 `ego-lan-214` **单站灰度** `DERIVE_ASYNC=1` 验证稳定
- 灰度验收通过后再逐步扩大异步模式；禁止断崖切换

### 5.0 商用化推进原则（否决一刀切）

| 原则 | 阶段一落地 |
|------|------------|
| **保留入口** | 采集页「导入」保留，标注单段应急；批量引导 Agent |
| **解耦链路** | 上传热路径与派生热路径代码级分离 |
| **异步转化** | jsonl / mux / parquet 下沉 derive 队列（进程内，W1） |
| **通道收敛** | **W2 起**：批量验收与生产同步 **仅** 214 Agent；浏览器批量体验冻结 |

### 5.1 核心要求（六项，立项锁定）

1. **保留网页单段应急入口**  
   不删除采集页「导入」；UI 标注单段应急、批量引导 214 Agent（见 [upload-channel-policy](./ego-lan-214-upload-channel-policy.md)）。

2. **上传链路仅 Raw + 校验**  
   `DERIVE_ASYNC=1` 时，`stream-ingest` 上传热路径**仅**执行：
   - 写入 `raw/segments/{session}/{segment}.tar.zst`
   - SHA256 + manifest / segmentId 校验
   - 校验通过即 HTTP 200 / 用户侧「**已上传**」  
   **必须从上传热路径彻底移除**：同步解压、同步 jsonl 写入、同步 mux、同步 parquet。

3. **全量派生异步下沉 derive-worker**  
   独立 worker 消费队列，顺序执行：解压 → jsonl → staging → 四路串行 mux → parquet sync → 写入现有 LeRobot v3 热层。  
   - 失败可 **单段 `derive-retry`**，从 Raw 重建，**无需重传** `tar.zst`  
   - worker 崩溃重启后从队列断点续跑

4. **网页端文案（W2 最小集，五态后置）**  
   异步模式下完成文案为 **「已上传，后台处理中」**；批量场景展示 Agent 引导。五态总览、前端 derive-retry **后置**。

5. **`DERIVE_ASYNC` 回退开关**  
   | 值 | 行为 |
   |----|------|
   | `0`（默认） | 完全复用 P0+ 同步 ingest 链路，保障业务连续 |
   | `1` | Raw ACK + 异步派生；支持 per-station 灰度 |

   关闭开关即回退，禁止断崖切换。

6. **全程遵守四条红线（§四）**  
   214 零侵入 · LeRobot v3 产出格式不变 · `tar.zst` 唯一可信源 · 可灰度可回退。

### 5.2 架构切面（`DERIVE_ASYNC=1`）

```text
浏览器 / Agent / CLI
        │
        ▼
  stream-ingest（上传热路径，轻量）
        │  仅 Raw 落盘 + SHA256
        │  状态 → Uploaded (Verified)
        ▼
  raw/segments/{session}/{seg}.tar.zst   ← 唯一可信源，不可覆盖
        │
        │  入队
        ▼
  derive-worker（派生冷路径，异步）
        │  解压 → jsonl → staging → mux → parquet
        │  状态 → Deriving → Derived | Failed
        ▼
  stream/{station}/data|videos|meta/     ← LeRobot v3 热层（格式契约不变）
```

### 5.3 改动范围（仅 34）

| 组件 | 变更 |
|------|------|
| `stream-ingest` | 新增 Raw 写路径；`DERIVE_ASYNC=1` 时上传返回后不触发 sync derive |
| `derive-worker`（新） | 队列消费、段级派生、重试、`derive-retry` API/CLI |
| 段状态存储 | `segment-state.json` 或 DB；`GET /api/.../segments` |
| 采集页 UI | Agent 引导 + 异步完成文案；**五态 / 批量 UX 后置** |
| 214 | **无变更** |

### 5.4 交付物

- [ ] Raw 目录规范与保留策略（演进合并 `.upload/incoming`）
- [ ] `stream-ingest` Raw-only 热路径（`DERIVE_ASYNC=1`）
- [ ] `derive-worker` 服务 + 队列 + 单段重试
- [ ] 段状态 API（含内部 Stored / Verified）
- [x] 采集页 Agent 引导 + 「已上传，后台处理中」文案（五态 UI **后置**）
- [ ] `DERIVE_ASYNC` 环境变量 / per-station 配置与运行手册
- [ ] `derive-retry` CLI 或管理 API
- [ ] `DERIVE_ASYNC=0` 回归用例：与 P0+ 验收等价

### 5.5 风险与收益

| 风险 | 缓解 |
|------|------|
| 双路径维护（sync / async） | 默认 `DERIVE_ASYNC=0`；单站灰度 `ego-lan-214` |
| Raw + derive 占盘 | 保留策略（如 7d）；Derived 后可选归档 Raw |
| 上传后暂不可回放 | UI 明确「处理中」；仅 `Derived` 开放回放 |

| 收益 |
|------|
| 上传 ACK 秒级/段，批量 21 段不阻塞 ffmpeg |
| mux 失败从 Raw 重建，无需 214 重传 |
| 语义与 Tesla 类「blob 签收 ≠ 转码」对齐 |

### 5.6 阶段一里程碑 / 验收

- [ ] 21 段 **214 Agent** 上传：30s 内全部 Raw `Verified`（上传热路径无 ffmpeg）
- [ ] derive 完成后：jsonl = parquet = 四路 MP4 = 6300
- [ ] stream-ingest 重启 → `resumeDeriveQueues` 从未完成段继续
- [ ] 人为损坏 mp4 → `derive-retry` 单段重建成功
- [ ] 浏览器异步文案「已上传，后台处理中」；五态 UI **后置**
- [ ] 四条红线逐项审计通过
- [ ] `DERIVE_ASYNC=0` 回归：与 P0+ 验收标准等价

---

## 六、阶段二：体验与运维深化（五态 UI + Agent 自动化）

**优先级**：次高 · 阶段一 W2 全绿后 · **周期 4–6 周**

> **注**：批量主通道已在 W2 切换为 214 Agent（见 [upload-channel-policy](./ego-lan-214-upload-channel-policy.md)）。阶段二聚焦五态 UI、Agent 默认策略与运维自动化。

### 6.1 核心改动

1. **Agent 默认策略与无人值守**  
   `ego-export` 出新包后自动上传；断点续传、带宽限速、失败重试；浏览器保持单段应急。

2. **五态 UI 与 Agent 进度统一**  
   采集页导入区五态总览与 Agent 进度统一状态源（W2 已做 Agent 引导 + 异步文案最小集）。

3. **体验项**  
   批量上传期间 **不** 每段 reload LeRobot iframe；`Derived` 批量完成后再刷新 viewer。

### 6.2 交付物

- [ ] 214 Agent 部署文档与 systemd 默认策略
- [ ] 采集页状态总览组件 + 与 import 弹窗统一状态源
- [ ] Agent 与浏览器上传幂等（同 `segmentId`）

### 6.3 里程碑 / 验收

- [ ] 214 录完 → export → 24h 内 Agent 传完，0 人工拖传
- [ ] 浏览器补传 1 段与 Agent 行为一致（同状态机）
- [ ] 运维文档标明：批量同步 **禁止** 依赖浏览器

---

## 七、阶段三：商用增强 — 可观测 + 质量门禁 + 规模化

**优先级**：中长期 · 多站点扩张前 · **周期 6–8 周**

### 7.1 核心改动

1. **全链路可观测**  
   上传成功率、derive 失败率、阶段耗时、站点数据量；异常告警。

2. **训练质量门禁**  
   `ego-run-pipeline` 仅消费 `Derived` 且通过自动质检（帧数、相机路数、jsonl/parquet/mp4 一致性）的段。

3. **规模化**  
   多站点统一调度；derive 任务优先级；全链路审计（上传 → 派生 → 质检 → 训练引用）。

### 7.2 里程碑 / 验收

- [ ] 单站日处理 100+ 段，mux_fail_rate < 1%，无需人工清 stream
- [ ] pipeline 自动跳过 `Failed`，重试后自动纳入
- [ ] Dashboard 结论与 `ego-lan-214-reimport-verify.sh` 等价

---

## 八、三阶段总览

```text
                    ┌─────────────────────────────────────┐
                    │  P0+ 旧链路闭环（当前）               │
                    │  mux/scaffold 修复 · 6300 帧验收      │
                    └─────────────────┬───────────────────┘
                                      ↓
┌──────────────────────────────────────────────────────────────────────────┐
│ 阶段一 W1–W2   Raw + derive 队列 + DERIVE_ASYNC + **214 Agent 批量主通道**   │
└─────────────────────────────────────┬────────────────────────────────────┘
                                      ↓
┌──────────────────────────────────────────────────────────────────────────┐
│ 阶段二 4–6 周   五态 UI + Agent 无人值守默认 + 运维自动化                  │
└─────────────────────────────────────┬────────────────────────────────────┘
                                      ↓
┌──────────────────────────────────────────────────────────────────────────┐
│ 阶段三 6–8 周   监控面板 + Derived 训练门禁 + 多站点调度                   │
└──────────────────────────────────────────────────────────────────────────┘

全程：214 tar.zst 不变 · LeRobot v3 产出契约不变 · 每阶段可回退
```

---

## 九、立项引用清单（阶段一 Kickoff）

| 类别 | 内容 |
|------|------|
| 推进原则 | 保留入口 · 解耦链路 · 异步转化 · 逐步降级 |
| 六项核心要求 | §5.1（Raw-only 上传 / derive-worker / 五态 UI / DERIVE_ASYNC / 四条红线） |
| 用户五态 | 上传中 / 已上传 / 处理中 / 已就绪 / 失败 |
| 内部子态 | Stored、Verified（不增加 UI） |
| 语义变更 | 「已上传」= Raw Verified；与 P0+「导入成功」切割 |
| P0+ 出口 | 历史参考；21 段验收改 M3 Raw 重派生 |
| 实施计划 | [ego-lan-214-p1-phase1-implementation-plan.md](./ego-lan-214-p1-phase1-implementation-plan.md) |
| DoD 否决项 | 四条设计红线（§四） |
| 开关 | `DERIVE_ASYNC` 默认 0；阶段一开发全程默认 0，单站灰度验证后再切 1 |
| 边缘 | **零改造** |

---

## 十、版本记录

| 日期 | 说明 |
|------|------|
| 2026-07-03 | 扩写为正式架构路线图；纳入五态/子态细化、P0+ 出口、DoD 否决项、阶段一语义切割 |
| 2026-07-03 | 锁定阶段一六项 PRD：保留入口、上传热路径 Raw-only、derive-worker、五态 UI、DERIVE_ASYNC 回退 |
| 2026-07-03 | 锁定落地策略：阶段一开发默认 DERIVE_ASYNC=0，单站灰度稳定后再切异步 |
