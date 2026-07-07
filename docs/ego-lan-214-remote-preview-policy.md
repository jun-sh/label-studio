# ego-lan-214 远程实时预览策略（方案 B）

**原则**：采集链路优先级最高；34 远程「实时」为旁路浏览，**采集中全程禁用**，仅设备待机且预检服务运行时可查看。回放与采集状态完全解耦。

---

## 架构

| 组件 | 运行时机 | 职责 |
|------|----------|------|
| `ecs-preview-standby.service` | 待机（采集栈未运行） | 低帧率 OAK 预检，`:8765` MJPEG，不写盘 |
| `ecs-record-oak-stream.service` | 采集中 | 30Hz 写段 + 本机预览（214 WiFi） |
| `ecs-station-heartbeat.service` | 常开 | 向 34 上报 `captureState` |
| `ecs-oak-standby-stack.target` | 默认启用 | 聚合待机预检 |
| `ecs-oak-capture-stack.target` | 手动启停 | 与 standby 互斥 |

互斥：`capture-stack` ↔ `standby-stack`（systemd `Conflicts` + `ego_web` 显式 stop/start）。

---

## 34 侧

| 变量 | 默认 | 说明 |
|------|------|------|
| `COLLECTION_REMOTE_PREVIEW` | `0` | 设为 `1` 后暴露「实时」按钮 |
| `COLLECTION_REMOTE_PREVIEW_SNAPSHOT_MS` | `500` | JPG 轮询间隔 |

策略：`remotePreviewAllowed = remotePreview && online && captureState === idle`

预览代理：采集中 `403 preview_capture_active`（防绕过 UI）。

---

## 214 部署

```bash
# 同步 capture_state.py / preview_standby.py / tools/*.py 到 ego-studio
systemctl --user daemon-reload
systemctl --user enable --now ecs-station-heartbeat.service
systemctl --user enable --now ecs-oak-standby-stack.target
```

采集启停仍通过 `ego-web` 或：

```bash
systemctl --user start ecs-oak-capture-stack.target   # 自动停 standby
systemctl --user stop ecs-oak-capture-stack.target    # 自动启 standby
```

---

## API

`GET /lerobot/api/collection/stations/ego-lan-214/ping`：

```json
{
  "stationId": "ego-lan-214",
  "online": true,
  "captureState": "idle",
  "remotePreviewAllowed": true
}
```

`captureState`：`offline` | `idle` | `recording` | `unknown`

---

## 验收

| # | 步骤 | 期望 |
|---|------|------|
| 1 | 待机 + `COLLECTION_REMOTE_PREVIEW=1` | 采集页「实时」可点，四路有画面 |
| 2 | 214 开始录制 | `captureState=recording`，实时灰化，proxy 403 |
| 3 | 结束录制 | 回到 `idle`，实时恢复 |
| 4 | 采集中 | 「回放」仍可浏览已派生历史数据 |

---

## 相关文件

| 路径 | 说明 |
|------|------|
| `ego-stream-client/preview_standby.py` | 待机预检主逻辑 |
| `ego-stream-client/capture_state.py` | 边缘状态解析 |
| `ego-stream-client/systemd/ecs-preview-standby.service` | systemd 单元 |
| `lerobot-studio/preview-proxy.mjs` | 34 反代 + 403 |
| `ego-local-web/ego_web.py` | 启停互斥 |
