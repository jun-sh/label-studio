# ego-lan-214 采集启动 P0 上线验收报告

**日期**：2026-07-05  
**机器**：10.10.10.214 · `ecs-ego-web` + `ecs-oak-capture-stack`  
**对比基线**：[ego-lan-214-capture-startup-baseline-2026-07-05.md](./ego-lan-214-capture-startup-baseline-2026-07-05.md)

---

## 1. 部署摘要

| 项 | 状态 |
|----|------|
| P0 代码 rsync 至 214 | ✅ |
| `ecs-ego-web.service` 重启 | ✅ |
| 采集栈下次录制加载新代码 | ✅ |
| 热修 `stream_upload.py`：`async=1` → `async_mode=1`（Python 保留字 SyntaxError） | ✅ |

同步文件：

- `ego_capture_studio/capture/oak_4p_capture.py`（`record_episode_probe`）
- `ego_capture_studio/capture/stream_upload.py`（`heartbeat_async`）
- `ego_capture_studio/capture/record_oak_stream.py`
- `ego_capture_studio/cli/record_oak_stream.py`
- `/home/server/ego-web/ego_web.py`（300ms 轮询、journal 缓存、shm 优先）

---

## 2. 场景 A 复测（34 连通 · 5 轮）

**脚本**：`capture-startup-benchmark.py --cycles 5`  
**报告路径（214）**：`/tmp/ego-capture-benchmark-p0/report.md`

### 2.1 总启动时长（点击开始 → `recording`）

| 指标 | 基线（P0 前） | P0 后 | 变化 |
|------|---------------|-------|------|
| **均值** | 9.12 s | **6.98 s** | **−2.14 s（−23.5%）** |
| **P95** | 9.19 s | **7.12 s** | −2.07 s |
| **极大值** | 9.19 s | 7.12 s | −2.07 s |
| **极小值** | 9.07 s | 6.88 s | −2.19 s |

5 轮全部成功，无超时、无采集进程崩溃。

### 2.2 `probe_duration_s` 分布

| 轮次 | probe_duration_s |
|------|------------------|
| 1 | 0.835 |
| 2 | 0.835 |
| 3 | 0.804 |
| 4 | 0.801 |
| 5 | 0.802 |

| 统计 | 基线 | P0 后 |
|------|------|-------|
| 均值 | **3.000 s**（固定） | **0.815 s** |
| P95 | 3.001 s | 0.835 s |
| 范围 | 恒 3.0 s | 0.801 ~ 0.835 s |

**探针单项节省 ≈ 2.19 s**，达到 P0-1 目标（≥1 s）。

### 2.3 其他段

| 段 | P0 后均值 | 说明 |
|----|-----------|------|
| UI 滞后 | 0 s | P0-3 边际收益小（基线已为 0） |
| heartbeat retry | 0 次 | 34 连通时无阻塞 |
| OAK 冷启 | ~6 s | 未变，仍为 TOP1（需 P1 常驻相机） |

---

## 3. 场景 B 断网验收（P0-2）

**方法**：`sudo iptables -A OUTPUT -d 10.10.10.34 -j DROP`，经网页 API 启停一轮。

| 指标 | 结果 | 通过标准 |
|------|------|----------|
| 总启动 → recording | **7.75 s** | warming 无分钟级卡顿 ✅ |
| `probe_duration_s` | **0.815 s** | 探针正常 ✅ |
| `heartbeat_retry` 阻塞启动 | **未出现** | 异步心跳不挡采集 ✅ |
| 采集进程 | 正常 `capture-only` | 无 SyntaxError / 无 crash loop ✅ |

测试后已执行 `iptables -D` 恢复 34 连通。

---

## 4. P0 分项验收

| 项 | 目标 | 结果 |
|----|------|------|
| **P0-1 自适应探针** | 平均缩短 ≥1 s，四路校验不退化 | ✅ 探针 3.0→0.82 s；5 轮均成功写帧 |
| **P0-2 异步心跳** | 34 离线不阻塞 warming | ✅ 断网 7.75 s 进入 recording |
| **P0-3 轮询轻量化** | UI 滞后 −0.4~0.7 s | ⚪ 基线已为 0 s，无额外可测收益；300ms 轮询已上线 |

---

## 5. 回退开关（现场一键切旧逻辑）

```bash
# 214 systemd drop-in 或 export 后 restart 采集栈
EGO_PROBE_ADAPTIVE=0          # 恢复固定 3s 探针
EGO_PROBE_FIXED_S=3
EGO_HEARTBEAT_ASYNC=0         # 恢复同步心跳
EGO_STATUS_POLL_MS=800        # 恢复 800ms 轮询
```

---

## 6. 结论与 P1 建议

### 验收结论：**P0 达标，可视为生产上线完成。**

- 启动总时长 **9.1 s → 7.0 s**（约 **23%** 提升）
- 可压缩的 **3 s 固定探针已消除**（现 **~0.8 s**）
- **34 断网不再卡死** warming 链路

### 是否进入 P1？

| 因素 | 建议 |
|------|------|
| 剩余 ~6 s 主要为 OAK 冷启 | **建议启动 P1-1 相机常驻预热**（预期再省 3~6 s） |
| P0 已稳定 5 轮 + 断网 1 轮 | 可并行做 **24h soak** 观察 USB 稳定性 |
| P1 风险 | 需 `EGO_OAK_STAY_ALIVE` 开关 + 功耗评估 |

**推荐排期**：P0 观测 **3 个工作日**无现场投诉后，立项 P1-1；P1-2 Python 预加载可作为 P1-1 的补充或次优先。

---

## 7. 附录：故障记录（已解决）

| 时间 | 现象 | 根因 | 处理 |
|------|------|------|------|
| 16:43~16:49 | benchmark 卡 warming、采集 crash loop | `stream_upload.py` 使用 `async=1` 关键字参数 → **SyntaxError** | 改为 `async_mode=1` 并重新 scp |

---

## 8. 复测命令

```bash
# 214 上
python3 ~/ego-local-web-src/scripts/capture-startup-benchmark.py --cycles 5 \
  --out-dir /tmp/ego-capture-benchmark-p0

# 断网单轮（需 sudo）
sudo iptables -A OUTPUT -d 10.10.10.34 -j DROP
# 手机/网页点「开始录制」，应在 ~8s 内变绿
sudo iptables -D OUTPUT -d 10.10.10.34 -j DROP
```
