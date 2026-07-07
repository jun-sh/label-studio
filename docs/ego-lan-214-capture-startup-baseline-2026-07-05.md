# ego-lan-214 采集启动基线报告（2026-07-05）

**环境**：10.10.10.214 · 34 连通（场景 A）· `http://127.0.0.1:8080`  
**方法**：`capture-startup-benchmark.py --cycles 5`（0 代码修改，生产版本）  
**原始数据**：214 上 `/tmp/ego-capture-benchmark-1783240470/`

---

## 1. 总耗时基线（点击开始 → 页面 `recording`）

| 指标 | 值 |
|------|-----|
| **均值（有效 4 轮）** | **9.12 s** |
| P95 | 9.19 s |
| 极大值 | 9.19 s |
| 极小值 | 9.07 s |

> 第 1 轮超时（采集栈可能未完全 idle / 上一轮残留），未计入均值。

**结论**：当前生产环境启动耗时 **稳定偏慢**（四轮方差 <0.12s），无 `heartbeat_retry`，属可预期慢而非偶发卡死。

---

## 2. 五段耗时拆分

| 段 | 均值 | P95 | 说明 |
|----|------|-----|------|
| starting | ~0 s | 0 s | POST 返回时往往已越过 starting |
| warming_pre（OAK 连接+校验） | **~6.0 s** | — | journal：`Started` → `oak_output_resolution ok` |
| heartbeat（34） | **<0.5 s** | 0 次 retry | 场景 A 无 `heartbeat_retry` 日志 |
| **探针 `record_episode(3.0)`** | **3.000 s** | **3.000 s** | journal：`oak_output_resolution ok` → `capture-only` 恒 3s |
| UI 感知延迟 | **0 s** | 0 s | `capture-only` 与 API `recording` 同轮询内对齐 |

### 耗时占比（估算）

```text
探针固定 3s     ████████████████░░░░░░░░░░░░░░  ~33%
OAK 冷启+连接   ██████████████████████████████  ~67%  (~6s)
34 心跳         ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  <5%
UI 轮询         ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  ~0%（800ms 轮询下未检出滞后）
```

### TOP1 瓶颈

**固定 3 秒探针** — 在可量化段落中占 **~33%** 总时长，且为 **完全可压缩** 的硬编码等待。

次要瓶颈：**每次录制 OAK 冷启动**（USB 连接 + pipeline + 四路校验），约占剩余 **~6s** → 对应 P1「相机常驻」。

---

## 3. 场景特征关键词

| 类型 | 特征 |
|------|------|
| **稳定慢** | 总时长 9±0.1s；`probe` 段恒为 3.0s；无 `heartbeat_retry` |
| **偶发超长** | 本次未复现；若出现查 `heartbeat_retry`、`resolution check` 超时、cycle 1 类 `timeout waiting for recording` |

---

## 4. 场景 B（34 断网）— 待测

生产机未执行 iptables 断网。预期旧版：**同步 heartbeat 可阻塞数分钟**；P0-2 上线后应 **warming ≤10s** 且仅后台 `heartbeat_async_fail`。

操作见 [ego-lan-214-capture-startup-ops.md](./ego-lan-214-capture-startup-ops.md) 第五节。

---

## 5. P0 优化预期（基于本基线）

| 项 | 预期收益 | 依据 |
|----|----------|------|
| P0-1 自适应探针 | **−1~2 s**（总时长 → **~7~8s**） | 探针现恒 3.0s，四路通常 <1s 就绪 |
| P0-2 异步心跳 | 连通时 **<0.5s**；断网时 **避免分钟级卡死** | 本次 0 retry |
| P0-3 轮询 300ms + journal 缓存 | UI 滞后 **0.4~0.7s**（本次基线为 0，边际收益小） | 当前 800ms 下 ui_lag=0 |

**P0 全部上线后保守估计**：总启动 **7~8s**（仍含 ~6s OAK 冷启）。

---

## 6. 复测命令

```bash
# 214 上（优化前/后对比）
python3 /tmp/capture-startup-benchmark.py --cycles 5 --scenario connected

# 优化后需出现
# probe_duration_s=0.8~2.0
# heartbeat_ok ... async=1
```

---

## 7. 排期对照

| 阶段 | 状态 |
|------|------|
| 当日基线 | ✅ 本报告 |
| P0 开发 | ✅ 代码已入仓，**待 rsync 214 + restart** |
| P0 灰度 3 日 | 待执行 |
| P1 常驻相机 | 待 P0 观测后决策 |
