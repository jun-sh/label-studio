# Unity 预览与热待机（Warm Idle）参考说明

> 面向 Unity App 同事，说明设备端预览逻辑、热待机行为，以及 **Unity 是否需要改协议**。  
> 对齐实现：`ego-local-web`（8080/8081）+ `ego_tcp_bridge`（TCP :9100），ego-001 生产机 `10.10.10.130`。

---

## 1. 架构速览

| 通道 | 端口 | 用途 |
|------|------|------|
| 网页采集 | HTTP **8080** | 启停录制 + 录制中单路预览 |
| 网页四路 | HTTP **8081** | 待机四路 2×2 预览（无采集按钮） |
| Unity 控制 | TCP **9100** | `start_recording` / `stop_recording` / `change_view` 等 |
| 预览数据面 | HTTP **8765** | 四路 JPEG/MJPEG（8080/8081/App 共用） |

- 8080、8081、TCP Bridge 是**独立进程**，共用 `ego_web.py` 里的采集/预览逻辑。
- 完整 TCP 帧格式与消息定义见：[ego-app-tcp-protocol.md](./ego-app-tcp-protocol.md)

---

## 2. 热待机（Warm Idle）是什么

录制结束后，设备可能进入 **warm idle**（热待机）：

- 8080 / `device_info` 显示 `state: "idle"`，但 `oak_warm_active: true`
- 采集进程仍占用 OAK（USB），为**下一段快速录制**保活（约 1–2 秒可 resume）
- 插电默认最长约 **300 秒**，电池约 **120 秒**
- 此期间 **standby 四路预览无法启动**，HTTP 预览通常只有 **`front_left` 一路 live**

这是设计行为，不是 bug。

---

## 3. Unity 要不要改协议 / 发新消息？

### 结论：**不用改协议，不用发新消息类型**

设备端已在收到现有 `change_view` 时，**自动判断**是否在 warm idle，并决定是否结束热待机。

Unity 继续按现有协议发即可：

```json
{ "type": "change_view", "data": { "view_num": 2 } }
```

设备仍**立即**回复 `view_changed_result`（含新的 `preview_jpg_url` / `preview_mjpeg_url`）。结束 warm idle 在**后台异步**执行，不阻塞 TCP 回复。

### `view_num` 说明

| view_num | view_id | 说明 |
|----------|---------|------|
| 1 | front_left | 前左 |
| 2 | front_right | 前右 |
| 3 | rear_left | 后左 |
| 4 | rear_right | 后右 |

**不是 0–3，是 1–4。** 随便发哪一路都可以触发设备端判断（包括 `view_num: 1`）。

### 设备端决策逻辑（`change_view` 触发）

```
收到 change_view
    ↓
EGO_PREVIEW_END_WARM_IDLE=1 ?
    ↓ 否 → 只更新 view_num，返回 view_changed_result
    ↓ 是
当前是否在 warm idle（oak_warm_active）?
    ↓ 否 → 不 kill warm，按需唤醒 standby
    ↓ 是
capture_state 是否为 recording / starting / warming / stopping ?
    ↓ 是 → 不 kill（采集中/落盘中）
    ↓ 否（idle 热待机）
8080 是否正在 start/stop/abandon ?
    ↓ 是 → 本次暂不 kill
    ↓ 否 → 后台 hard stop 采集 → 释放 OAK → 启动 standby 四路
```

### 几种状态对照

| 当前状态 | 发 `change_view` 后 |
|----------|---------------------|
| **热待机**（录完，`idle` + warm） | 自动结束 warm，约 **10–15 秒**后四路 URL 可 live |
| **纯待机**（无 warm） | 不 kill，正常切视角 |
| **正在录制** | **不 kill**，只改 URL；仍只有主预览路 live（默认前左） |
| **正在 stop 落盘** | 暂不 kill |

### Unity 侧建议（可选，非必须）

| 情况 | 是否需要改 Unity |
|------|------------------|
| 切视角时发 `change_view` | 否 |
| `device_info` / `heartbeat` | 否，也不用多发 |
| 录完进热待机后切到 2/3/4 路 | 建议保留现有 JPEG 拉流重试（约 10–15 秒后才全 live） |
| **采集中**四路同屏 | 设备端仍只有前左 live，**无法靠改协议解决** |

### 重要边界：四路同屏是否发了 `change_view`？

若 Unity「四路同屏」是**直接拉 4 个 `:8765` URL、从不发 `change_view`**，设备端无法感知用户要看四路，也就**不会**自动结束 warm idle。

**不改协议的解决办法：**

- 进入四路界面时**随便发一次** `change_view`（例如 `view_num: 1`），或
- 让用户先打开网页 `http://<设备>:8081`（也会触发结束 warm）

若切单路/进四路时**已经有** `change_view`，则 Unity **完全不用改**。

### 代价（与 8081 网页相同）

结束 warm idle 后，下一段 `start_recording` 变为**冷启动**（约 10–15 秒），失去「快速二次录制」优势。

---

## 4. 8081 网页四路 vs Unity：不是同一套逻辑

**8081 不使用 `change_view`，也不走 TCP :9100。**

### 8081 访问流程

```
浏览器打开 http://10.10.10.130:8081/
        ↓
ecs-ego-web-staging（EGO_WEB_UI_MODE=quad）
        ↓
① GET /api/status
② GET /api/preview/cameras  → 若热待机：后台结束 warm + 启动 standby
        ↓
standby（ecs-preview-standby）在 :8765 同时输出四路 JPEG
        ↓
③ 四路并行轮询（默认每 200ms 各拉一次）：
   GET /api/preview/front_left.jpg
   GET /api/preview/front_right.jpg
   GET /api/preview/rear_left.jpg
   GET /api/preview/rear_right.jpg
        ↓
ego_web 代理到 127.0.0.1:8765/preview/{cam}/jpg
        ↓
2×2 显示，x/4 就绪
```

**核心：四路同时 live，并行拉 4 个 HTTP URL，不是单路轮询切换。**

### 对比表

| | **8081 网页** | **Unity App** |
|--|--------------|---------------|
| 通道 | HTTP `:8081` | TCP `:9100` + HTTP `:8765` |
| 切视角 | 无 `change_view` | `change_view`（view_num 1–4） |
| 四路显示 | 同时拉 4 个 `/api/preview/{cam}.jpg` | 四路同屏时拉 4 个 `:8765` URL |
| 结束 warm 触发 | 打开页面 / 调 cameras API | 发 `change_view`（或进四路前发一次） |

---

## 5. 采集中四路：协议限制（与 warm 无关）

生产配置（ego-001）为保证 30fps H.264 写盘：

```
OAK_HW_PREVIEW_H264_CAMS=front_left
```

含义：

- **四路数据都在写盘**
- **HTTP 预览采集中只有 `front_left` 持续更新**（约 4fps）
- 其余三路 `GET /preview/{cam}/jpg` 可能 **503** 或停在旧帧

`change_view` 只让 App **改拉哪条 URL**，**不会**在采集中打开其他相机的硬件预览旁路。

因此：**录制中切四路同屏再切回**，属于客户端预览逻辑与设备能力不匹配，不是 warm idle 问题。  
正确 SOP：**录制前先四路检查**（8081 或待机拉四路 URL）。

---

## 6. 设备端代码位置（给需要联调的同事）

文件：`data-lab-platform/ego-local-web/ego_web.py`

### Unity `change_view` 入口

`ego_tcp_bridge.py` → `_handle_change_view()` → `ego_web._prepare_view_switch_preview_access()`

### 8081 入口

- `GET /`、`GET /api/status` → `_prepare_quad_preview_access()`
- `GET /api/preview/cameras` → `_build_preview_cameras_payload()` 内调 `_prepare_quad_preview_access()`

### 调用链（结束 warm + 启动 standby）

```
_prepare_quad_preview_access() / _prepare_view_switch_preview_access()
    ↓
_maybe_end_warm_idle_for_multi_preview_async()
    ↓ 若 _oak_warm_idle_active()
Thread → _end_warm_idle_for_quad_preview_worker()
    ↓
_stop_capture_hard()          # systemctl stop 采集栈，删 oak_warm_state.json
    ↓
_start_standby_preview()
    ↓
_wake_standby_preview_if_idle()  # systemctl start ecs-preview-standby.service
```

### 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `EGO_PREVIEW_END_WARM_IDLE` | `1` | 为 1 时，8081 / change_view 可自动结束 warm |
| `EGO_OAK_WARM_IDLE` | `1` | 为 1 时，stop 后进入热待机 |
| `EGO_WEB_UI_MODE` | `capture` / `quad` | 8081 服务设为 `quad` |

130 日志关键字：

```bash
journalctl --user -u ecs-ego-web-staging | grep quad_end_warm_idle
journalctl --user -u ecs-ego-tcp-bridge | grep quad_end_warm_idle
```

---

## 7. 推荐联调检查清单

1. **录制前**：`change_view` 或拉四路 URL，确认四路均有新帧。
2. **录制中**：仅依赖 `front_left`（或 `device_info` 给出的主预览路）live；勿做四路同屏强依赖。
3. **录完 stop 后**：若切到 2/3/4 路无图，发一次 `change_view` 或等待设备结束 warm；预览 URL **重试 10–15 秒**。
4. **`device_info` 轮询**：不必为 warm 额外发消息；看 `state` + `frames_writing` 即可区分录制 / 热待机 / 纯待机。
5. **四路同屏入口**：若仅拉 HTTP 不发 `change_view`，进入四路页时**补发一次** `change_view`。

---

## 8. 相关文档

- [ego-app-tcp-protocol.md](./ego-app-tcp-protocol.md) — TCP 协议全文
- 网页四路：`http://10.10.10.130:8081`（实验室有线）/ `http://192.168.8.1:8081`（连热点）

---

*文档生成自设备端联调说明（2026-09-23），与 130 当前部署行为一致。*
