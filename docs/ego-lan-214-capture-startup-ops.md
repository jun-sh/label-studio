# ego-lan-214 采集启动耗时 · 运维排查手册

**适用**：214 边缘站 `ecs-oak-capture-stack` + `ecs-ego-web`  
**基线脚本**：`data-lab-platform/ego-local-web/scripts/capture-startup-benchmark.py`

---

## 一、现场监控命令（0 代码修改）

```bash
# 终端 1：采集日志（毫秒时间戳）
journalctl --user -u ecs-record-oak-stream -f --output=short-full

# 终端 2：状态轮询（默认 8080，非 800）
watch -n0.2 'curl -s http://127.0.0.1:8080/api/status | jq ".state,.msg,.frames_writing"'

# 自动化 5 轮基线（推荐）
python3 ~/ego-local-web-src/scripts/capture-startup-benchmark.py --cycles 5 --scenario connected
```

---

## 二、日志关键字 → 瓶颈对照

| 现象 | 日志关键字 | 可能原因 | 动作 |
|------|------------|----------|------|
| 启动前长时间无日志 | （空白） | Python 冷启动 / import | 正常 2~5s；考虑 P1 预加载 |
| USB / OAK 慢 | `oak_pipeline=` 出现晚 | 冷连接、USB 降速 | 查 USB3、供电、换线 |
| 四路校验卡住 | `oak_pipeline` 后 >5s 无 `oak_output_resolution ok` | 某路不出帧 | 重插 OAK；查 `resolution check` 报错 |
| 34 网络阻塞（旧版） | `heartbeat_retry` 密集 | 同步心跳重试 | 升级 P0-2；或断网验收应用已异步 |
| 探针固定等待 | `probe_duration_s` ≈ 3.0 | 固定探针 | 升级 P0-1 自适应探针 |
| 探针已缩短 | `probe_duration_s` 0.8~2.0 | 正常自适应 | — |
| 可录但网页慢变绿 | 已有 `capture-only session=`，state 仍 warming | UI 轮询 / journalctl | 升级 P0-3 |
| 稳定偏慢 | 每轮总时长接近、无 retry | 冷启+探针为主 | P0 后仍慢 → 评估 P1 常驻相机 |
| 偶发超长卡顿 | `heartbeat_retry` 或 resolution 超时 | 网络或 USB | 按上表分项排查 |

---

## 三、五段耗时定义（与基线脚本一致）

| # | 指标 | 起止 |
|---|------|------|
| 1 | starting | POST start → API `state=starting` |
| 2 | warming_pre | `oak_pipeline=` → `oak_output_resolution ok` |
| 3 | heartbeat | resolution ok → `heartbeat_ok`（或 capture-only） |
| 4 | probe | heartbeat 后 → `capture-only session=`（或 `probe_duration_s`） |
| 5 | ui_lag | `capture-only` 日志 → API `state=recording` |

---

## 四、环境变量开关（优化后回退）

| 变量 | 默认 | 说明 |
|------|------|------|
| `EGO_PROBE_ADAPTIVE` | `1` | `0` = 固定 3s 探针 |
| `EGO_PROBE_MIN_S` / `EGO_PROBE_MAX_S` | `0.8` / `3.0` | 自适应探针上下限 |
| `EGO_PROBE_FIXED_S` | （空） | 设为非空则强制固定秒数 |
| `EGO_HEARTBEAT_ASYNC` | `1` | `0` = 同步阻塞心跳（旧行为） |
| `EGO_STATUS_POLL_MS` | `300` | 网页轮询间隔 |
| `EGO_JOURNAL_CACHE_TTL_S` | `0.4` | status API journal 缓存 |

---

## 五、网络场景验收

**场景 A（34 连通）**：`heartbeat_ok` 通常 <1s，无 `heartbeat_retry`。

**场景 B（34 断网）**：P0-2 后 warming 不应分钟级卡顿；后台可出现 `heartbeat_async_fail` / `heartbeat_async_stall_alert`，采集仍应进入 `capture-only`。

临时模拟断网（需 sudo，测完恢复）：

```bash
sudo iptables -A OUTPUT -d 10.10.10.34 -j DROP
# 跑一轮 benchmark 或手动启停
sudo iptables -D OUTPUT -d 10.10.10.34 -j DROP
```

---

## 六、USB 现场检查

```bash
lsusb | grep -i 03e7
# 四路 OAK 应 USB 3.0；dmesg 无 continuous reset
dmesg | tail -30 | grep -i usb
```

---

## 七、相关路径

| 组件 | 路径 |
|------|------|
| Web 控制 | `/home/server/ego-web/ego_web.py` |
| 采集 CLI | `ego-studio` → `ego_capture_studio.cli.record_oak_stream` |
| 基线报告 | `/tmp/ego-capture-benchmark-*/report.md` |
