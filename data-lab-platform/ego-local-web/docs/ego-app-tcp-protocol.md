# Ego 设备与 App 通讯协议（修订版 · 与 130 实现对齐）

设备端作为 **TCP 服务端**，App 作为客户端。控制通道与现有网页 `ego-local-web`（HTTP `:8080`）**并行**，共用同一套采集逻辑，互不影响。

| 项目 | 值 |
|------|-----|
| 控制 TCP 端口 | **9100**（`EGO_TCP_PORT`） |
| 网页控制 | HTTP **8080**（不变） |
| 视频预览 | HTTP MJPEG/JPEG **8765**（非 UDP） |
| 130 有线地址 | `10.10.10.130` |
| 130 热点网关 | `192.168.8.1`（连热点后 App 用此地址） |

实现文件：`ego_tcp_bridge.py`（systemd：`ecs-ego-tcp-bridge.service`）

---

## 1. TCP 数据帧格式

TCP 为字节流，每条消息格式：

| 字段 | 类型 | 说明 |
|------|------|------|
| Length | uint32 BE | JSON UTF-8 字节长度（不含本 4 字节） |
| Body | bytes | UTF-8 JSON，无 BOM |

示例：正文 37 字节 → 包头 `00 00 00 25` + Body。

**收包流程（必须实现）：**

1. 缓冲收到的字节
2. 若 `len(buf) >= 4`，读取 `Length`
3. 若 `len(buf) >= 4 + Length`，取出一条 JSON 解析；否则等待（半包）
4. 循环处理，支持粘包、一次读到多条

单帧 Body 上限默认 **1 MiB**（`EGO_TCP_MAX_FRAME_BYTES`）。

---

## 2. JSON 公共结构

```json
{
  "type": "message_name",
  "data": { }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | 是 | 消息类型 |
| data | object | 否 | 业务字段；无数据时省略 |

---

## 3. 视角编号

| view_num | view_id | 说明 |
|----------|---------|------|
| 1 | front_left | 前左 |
| 2 | front_right | 前右 |
| 3 | rear_left | 后左 |
| 4 | rear_right | 后右 |

预览 URL 由设备在 `device_info` / `view_changed` 中给出（基于 `EGO_TCP_PREVIEW_HOST`，热点场景设为 `192.168.8.1`）。

---

## 4. App → 设备（下行）

### 4.1 heartbeat

每 **10 秒** 一次，连接保活。

```json
{ "type": "heartbeat" }
```

设备立即回复 `heartbeat_ack`（不表示业务成功）。

### 4.2 get_device_status

```json
{ "type": "get_device_status" }
```

设备回复 `device_info`（见 5.2）。

### 4.3 start_recording

```json
{ "type": "start_recording" }
```

设备回复：

1. `capture_result`（是否成功）
2. 成功时再发 `device_info`

启动可能耗时 15–90 秒（与网页一致），此期间可继续发 `heartbeat`。

### 4.4 stop_and_save_recording

```json
{ "type": "stop_and_save_recording" }
```

设备回复 `capture_result`，成功后再发 `device_info`。停止/落盘可能耗时数十秒。

### 4.5 change_view

切换 App 当前预览视角（服务端记录 `view_num`，并唤醒 standby 预览）。

```json
{
  "type": "change_view",
  "data": { "view_num": 2 }
}
```

设备回复 `view_changed`。

---

## 5. 设备 → App（上行）

### 5.1 heartbeat_ack

```json
{ "type": "heartbeat_ack" }
```

### 5.2 device_info

**连接建立后设备主动发送一条**；`get_device_status`、录制启停成功后也会发送。

```json
{
  "type": "device_info",
  "data": {
    "state": "idle",
    "duration": 0,
    "segment_count": 0,
    "storage_free": "843.5 GB",
    "storage_free_gb": 843.5,
    "storage_warn": false,
    "frames_writing": false,
    "view_num": 1,
    "view_id": "front_left",
    "preview_mjpeg_url": "http://10.10.10.130:8765/preview/front_left/mjpeg",
    "preview_jpg_url": "http://10.10.10.130:8765/preview/front_left/jpg",
    "msg": ""
  }
}
```

`state` 取值：`idle` | `starting` | `warming` | `recording` | `stopping` | `error`（与 `GET /api/status` 一致）。

### 5.3 view_changed

```json
{
  "type": "view_changed",
  "data": {
    "view_num": 2,
    "view_id": "front_right",
    "preview_mjpeg_url": "http://10.10.10.130:8765/preview/front_right/mjpeg",
    "preview_jpg_url": "http://10.10.10.130:8765/preview/front_right/jpg"
  }
}
```

### 5.4 capture_result

```json
{
  "type": "capture_result",
  "data": {
    "action": "start_recording",
    "success": true,
    "msg": ""
  }
}
```

`action`：`start_recording` | `stop_and_save_recording`。

### 5.5 error

```json
{
  "type": "error",
  "data": {
    "code": "invalid_view_num",
    "message": "view_num must be 1, 2, 3, or 4",
    "request_type": "change_view"
  }
}
```

---

## 6. 消息方向汇总

| 方向 | type | data | 说明 |
|------|------|------|------|
| App→设备 | heartbeat | 无 | 心跳 |
| App→设备 | get_device_status | 无 | 查询状态 |
| App→设备 | start_recording | 无 | 开始录制 |
| App→设备 | stop_and_save_recording | 无 | 停止并保存 |
| App→设备 | change_view | view_num | 切换预览视角 |
| 设备→App | heartbeat_ack | 无 | 心跳回复 |
| 设备→App | device_info | 见上 | 连接时主动推送 + 状态查询/录制后 |
| 设备→App | view_changed | 见上 | 视角切换确认 |
| 设备→App | capture_result | 见上 | 录制命令结果 |
| 设备→App | error | 见上 | 协议或参数错误 |

---

## 7. 视频预览（HTTP，非 UDP）

设备四路相机通过 **HTTP** 提供预览，与网页相同：

| 格式 | URL 模板 |
|------|----------|
| MJPEG 流 | `http://{host}:8765/preview/{view_id}/mjpeg` |
| 单帧 JPEG | `http://{host}:8765/preview/{view_id}/jpg` |
| 状态 | `GET http://{host}:8765/preview/status` → `{"online":true,"fps":8}` |

`{host}` 在热点下为 `192.168.8.1`，实验室有线为 `10.10.10.130`。优先使用 `device_info` 中的 `preview_mjpeg_url`。

**说明：** 初版 Unity 文档中的 UDP 视频尚未在 130 实现；本修订版以现有 `preview_server` 为准。

---

## 8. 部署与环境变量

```bash
# 边缘机 deploy 后
systemctl --user status ecs-ego-tcp-bridge
```

| 变量 | 默认 | 说明 |
|------|------|------|
| EGO_TCP_HOST | 0.0.0.0 | 监听地址 |
| EGO_TCP_PORT | 9100 | TCP 端口 |
| EGO_TCP_PREVIEW_HOST | 10.10.10.130 | 写入预览 URL 的主机名 |
| EGO_TCP_PREVIEW_HTTP_PORT | 8765 | 预览端口 |
| EGO_TCP_MAX_FRAME_BYTES | 1048576 | 单帧上限 |

热点场景在 `~/.config/ego-station.env` 增加：

```bash
EGO_TCP_PREVIEW_HOST=192.168.8.1
```

---

## 9. 联调

```bash
python3 tools/ego_tcp_client_test.py --host 10.10.10.130
# 热点：
python3 tools/ego_tcp_client_test.py --host 192.168.8.1
```

---

## 10. 与初版 Unity 文档的差异

| 初版 | 修订版 |
|------|--------|
| 未指定端口 | TCP **9100** |
| 上行 `switch_view` 字段整理 | 拆为 `device_info` + `view_changed` |
| UDP 视频 | **HTTP MJPEG** `:8765` |
| 无录制结果回包 | 增加 `capture_result` |
| 无错误格式 | 增加 `error` |
| `get_device_status` 未列入汇总 | 已列入 |
