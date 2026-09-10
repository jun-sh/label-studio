# Q1 + P1 完整验收汇总报告

**日期：** 2026-08-27  
**站点：** ego-001  
**镜像：** `data-lab-lerobot-studio:v0.1.3` (`e16be22c94dc`)  
**结论：** ✅ **Q1 全项通过 · P1 已上线 · 小 session SLA 实跑通过**

---

## 1. 总览

| 里程碑 | 内容 | 结果 |
|--------|------|------|
| **M1** | oneshot MP4 merge 默认 | ✅ |
| **R1** | 多 worker 并行 derive | ✅ |
| **Q1-1** | session claim（wx 互斥） | ✅ 6/6 单测 |
| **Q1-2** | per-session deriver.lock + lease | ✅ 7/7 单测 |
| **Q1-3** | watch kick claim→lock→pipeline | ✅ 3/3 单测 |
| **Q1-4** | CPU budget 拆分 + scale 3 worker | ✅ 已部署 |
| **Q1-5** | derive-status 多 worker 可观测 | ✅ API + `--detail` |
| **Q1-6** | mega session 31k 帧复测 | ✅ READY（1948s） |
| **P1** | 小 session <90s + mega 隔离 | ✅ 已上线 + SLA 实跑 |

---

## 2. Q1 分项验收

### Q1-1 Session Claim

- `claimSession` wx 创建互斥；过期 reclaim；owner-only release
- 单测：`derive/session-claim.test.mjs` **6/6 pass**
- 并发 claim 竞态：恰好一个 worker 胜出

### Q1-2 Per-Session Deriver Lock

- 从全局 `state/deriver.lock` 升级为 `state/sessions/{sid}/deriver.lock`
- 支持 `leaseExpiresAt`、`renewDeriverLock`、boot 清 stale lock
- 单测：`derive/derive-lock.test.mjs` **7/7 pass**

### Q1-3 Watch Kick 流程

- `kickDeriveSession`: claim → lock → DERIVING → pipeline → release
- 单测：`derive/watch-kick.test.mjs` **3/3 pass**

### Q1-4 Multi-Worker Scale + CPU Budget

- `DERIVE_WORKER_COUNT=3`，每 worker budget ≈ `0.85/3 ≈ 0.283`
- `encodeConcurrency=18` / worker，`extractConcurrency=4`（P1）
- 部署：`bash data-lab-platform/scripts/ego-derive-scale.sh 3`
- 单测：`derive/cpu-budget.test.mjs` + `derive/multi-worker-integration.test.mjs` **7/7 pass**
- 运行态：3× `derive-worker` healthy，`deriveQueue.mode=multi-worker`

### Q1-5 可观测性

- API `deriveQueue.concurrency`、`activeWorkers[]`
- CLI：`ego-derive-status --watch --detail` 显示 phase/camera/CPU/子进程
- P1 增补：`unitProgress.startedAt` 已透出（实跑非空）

### Q1-6 Mega Session 复测

**Session：** `sess_6e852e2c117c4bc2930b9171ffda5fac`（18 段 / ~31k 帧）

| 阶段 | 耗时（约） | 备注 |
|------|-----------|------|
| EXTRACT | ~6.5 min | 18/18 段串行（P1 前） |
| TABLE | ~9 min | 帧数线性 |
| MUX_ENCODE | ~6 min | 4 路 × chunk 并行 |
| MUX_MERGE | ~2 min/路 | oneshot |
| ffprobe 验帧 | ~12 min | 同步黑盒（P1 前） |
| **E2E** | **1948s (~32 min)** | ✅ READY |

监控日志：`data-storage/logs/q1-6-mega-retest.jsonl`  
**注：** mega 不在小 session SLA 范围；P1 `DERIVE_MAX_SEGMENTS_PER_SESSION=130` defer 隔离已启用。

---

## 3. P1 部署验收

### 3.1 构建与上线

```bash
docker build -t data-lab-lerobot-studio:v0.1.3 data-lab-platform/lerobot-studio
bash data-lab-platform/scripts/ego-derive-scale.sh 3
```

| 环境变量 | 生产值 |
|----------|--------|
| `DERIVE_EXTRACT_CONCURRENCY` | `4` |
| `DERIVE_EXTRACT_ENABLED` | `1` |
| `DERIVE_MAX_SEGMENTS_PER_SESSION` | `130` |
| `DERIVE_MAX_SEGMENTS_ACTION` | `defer` |
| `DERIVE_FFPROBE_HEARTBEAT` | `1` |
| `DERIVE_PROGRESS_HEARTBEAT_MS` | `30000` |
| `DERIVE_MUX_MERGE_MODE` | `oneshot` |

Worker 启动快照：`extractConcurrency=4, encodeConcurrency=18, workerCount=3`

### 3.2 P1 能力清单

| ID | 能力 | 状态 |
|----|------|------|
| P1-A | 段并行 EXTRACT | ✅ |
| P1-B | ffprobe async + MUX_VALIDATE heartbeat | ✅ |
| P1-C | API `startedAt` | ✅ 实跑验证 |
| P1-D | progress 写盘续 lock lease | ✅ |
| P1-E | 超大 session defer（>130 段） | ✅ |

单测：`derive/segment-limit.test.mjs` **4/4 pass**；derive 相关合计 **30/30 pass**

---

## 4. 小 Session SLA 实跑（P1 上线后）

**条件：** ≤7 段、≤1500 帧（本轮均为 1 段 / 257–496 帧）  
**方法：** 10 个独立 session 顺序 re-derive（worker 容器 + P1 env）  
**日志：** `data-storage/logs/p1-sla-results.jsonl`

| 指标 | 结果 | 目标 | 判定 |
|------|------|------|------|
| 成功率 | 10/10 READY | 100% | ✅ |
| E2E min | 19.79s | — | — |
| E2E max | 30.57s | — | — |
| **E2E P95** | **30.57s** | **< 90s** | ✅ |
| Wall P95 | 31.14s | — | — |
| `startedAt` | 10/10 非空 | S4 | ✅ |

### 明细（E2E 秒）

| Session | 帧数 | E2E(s) |
|---------|------|--------|
| sess_4253306d… | 257 | 19.79 |
| sess_6252a79a… | 335 | 23.73 |
| sess_52c28c8a… | 345 | 24.80 |
| sess_55c6c17d… | 380 | 25.54 |
| sess_180f148e… | 396 | 26.88 |
| sess_10eb84ac… | 443 | 28.43 |
| sess_07309696… | 438 | 28.19 |
| sess_4d7726bb… | 462 | 30.54 |
| sess_6593e8d0… | 480 | 29.70 |
| sess_2111db59… | 496 | 30.57 |

**对比 P1 前预估：** 小 session 串行 EXTRACT 约 5–15s + TABLE 3–8s + MUX 10–25s + ffprobe 1–3s → 理论 20–50s；实跑 P95 **30.57s** 符合预期。

---

## 5. 已知遗留（P2，不阻塞 Q1/P1）

| 项 | 说明 |
|----|------|
| UI 125/144 误导 | legacy 全站 markers，应改读 `unitProgress` |
| TABLE 大 session 线性 | mega 仍 ~9 min；已 defer 隔离 |
| `mp4Ok` legacy 字段 | derive-status 中仍为 false（unit gate 已通过） |

---

## 6. 监控命令

```bash
bash data-lab-platform/scripts/ego-derive-status --watch --detail

curl -s 'http://127.0.0.1:8080/lerobot/api/collection/stations/ego-001/derive-status' \
  | python3 -m json.tool
```

---

## 7. 签收

| 角色 | 项 | 签字 |
|------|-----|------|
| 工程 | Q1-1..Q1-6 + P1 代码/单测/部署 | ✅ |
| SLA | 小 session P95 E2E 30.57s < 90s | ✅ |
| 运维 | 3 worker healthy + env 正确 | ✅ |
| 产品 | mega defer 不堵队列 | ✅（130 段阈值） |

**综合结论：Q1 + P1 Commercial SLA 验收通过，可进入 P2（UI unitProgress 绑定）。**
