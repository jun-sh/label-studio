# P1 — 商业 SLA：小 session derive 端到端 P95 < 30s（Phase 0 收紧，2026-09-01）

**目标：** 典型小 session（≤7 段、≤1500 帧）上传完成 → `session.READY` **P95 < 30s、P99 < 60s**；超大 session **不堵队列**。convert（WiLoR/depth）走异步后台，不计入主 SLA（SLO-A）。

---

## 1. 瓶颈定位（Q1-6 mega 实测 + 小 session SLA 自测）

| 阶段 | 小 session (~500 帧) | mega (31k 帧 / 18 段) | 瓶颈性质 |
|------|------------------------|------------------------|----------|
| **EXTRACT** | ~5–15s（串行） | ~6.5 min | **CPU/IO 串行** → P1 并行 |
| **TABLE** | ~3–8s | ~9 min | 帧数线性；小 session 可接受 |
| **MUX_ENCODE** | ~10–25s | ~6 min | P0 并行 chunk 已生效 |
| **MUX_MERGE oneshot** | ~2–5s | ~2 min/路 | M1 已修复 |
| **ffprobe 验帧** | ~1–3s | **5–10 min** | **同步无打点** → P1 heartbeat |

**UI 125/144 误导：** legacy 全站 markers，与 unit 派生无关（P2 UI 债）。

---

## 2. 策略分层

### 2.1 小 session 专属优化（P1 本包）

| 项 | 做法 | 预期 |
|----|------|------|
| **P1-A** | `DERIVE_EXTRACT_CONCURRENCY` 段并行解压 | EXTRACT 2–4× |
| **P1-B** | ffprobe async + `MUX_VALIDATE` heartbeat | 消除「黑盒卡住」感知 |
| **P1-C** | API 透出 `unitProgress.startedAt` | `--detail` 可算 E2E |
| **P1-D** | progress 写盘续 lock lease | 长 TABLE 不被误杀 |

**小 session 定义（SLA 自测集）：**

- 段数 ≤ 7
- 帧数 ≤ 1500（`frameMap.length`）
- raw 体积 ≤ 300MB

### 2.2 超大 session 隔离（P1 本包）

| 项 | 做法 |
|----|------|
| **P1-E** | `DERIVE_MAX_SEGMENTS_PER_SESSION` + `DERIVE_MAX_SEGMENTS_ACTION` |
| **defer**（默认） | 不 claim/derive；保留 `DONE_UPLOAD`；日志 `derive_watch_defer_oversized` |
| **fail** | `session.FAILED` code `SEGMENT_LIMIT_EXCEEDED` |
| **130 采集侧**（可选） | `EGO_MAX_SEGMENTS_PER_SESSION` 停录 |

**超大 session 定义：** 段数 > `DERIVE_MAX_SEGMENTS_PER_SESSION`（建议 **24** 软告警 / **130** 硬 defer）。

---

## 3. 环境变量与开关

| 变量 | 默认 | 说明 |
|------|------|------|
| `DERIVE_EXTRACT_CONCURRENCY` | `1` | 段并行度；生产建议 `4` |
| `DERIVE_EXTRACT_ENABLED` | `1` | `0` 回滚串行 |
| `DERIVE_MAX_SEGMENTS_PER_SESSION` | `0` | `0`=关闭；生产 `130` |
| `DERIVE_MAX_SEGMENTS_ACTION` | `defer` | `defer` \| `fail` |
| `DERIVE_FFPROBE_HEARTBEAT` | `1` | ffprobe 阶段 heartbeat |
| `DERIVE_PROGRESS_HEARTBEAT_MS` | `30000` | 已有 |
| `DERIVE_HEARTBEAT_TIMEOUT_S` | `900` | 已有 |

---

## 4. 验收标准

### 4.1 小 session E2E SLA（P1 上线后）

| 用例 | 条件 | 通过标准 |
|------|------|----------|
| **S1** | 3 个独立小 session 连续 derive | 每个 **wall < 30s** |
| **S2** | P95 over 10 次 | **< 30s** |
| **S3** | P99 over 10 次 | **< 60s** |
| **S4** | `--detail` 全程 | EXTRACT/TABLE/MUX/MUX_VALIDATE 均有 `updatedAt` 刷新 |
| **S5** | `unitProgress.startedAt` | API 与 `--detail`「已运行」非空 |

### 4.2 超大 session 隔离

| 用例 | 条件 | 通过标准 |
|------|------|----------|
| **L1** | 段数 > MAX，`action=defer` | 不进 derive；pending 跳过；日志含 `defer_oversized` |
| **L2** | 段数 > MAX，`action=fail` | `session.FAILED` + `SEGMENT_LIMIT_EXCEEDED` |
| **L3** | mega 已 defer 的 session | 小 session 队列不受影响 |

### 4.3 回归

- M1 oneshot merge 仍默认
- Q1 多 worker claim 测试仍 pass
- idempotent READY skip 仍 pass

---

## 5. 自测命令

```bash
# 小 session SLA（单条）
time bash data-lab-platform/scripts/ego-derive-status --detail \
  --session <small_session_id>

# 并行 extract 生效（日志）
docker logs data-lab-derive-worker-1 2>&1 | grep derive_unit_extract_parallel

# ffprobe heartbeat
curl -s 'http://127.0.0.1:8080/lerobot/api/collection/stations/ego-001/derive-status?session=SID' \
  | python3 -c "import json,sys; up=json.load(sys.stdin)['progress']['unitProgress']; print(up)"

# 超大 defer
DERIVE_MAX_SEGMENTS_PER_SESSION=5 DERIVE_MAX_SEGMENTS_ACTION=defer node --test derive/segment-limit.test.mjs
```

---

## 6. 后续（P2，不在 P1 范围）

- Collection UI 改读 `unitProgress`（消除 125/144）
- TABLE 对齐并行化 / 大 session 分片
- 结构化阶段耗时 metrics 落盘
