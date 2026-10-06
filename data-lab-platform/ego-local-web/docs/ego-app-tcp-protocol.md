# Ego 设备与 App 通讯协议（修订版 · 与 130 实现对齐）

设备端作为 **TCP 服务端**，App 作为客户端。控制通道与现有网页 `ego-local-web`（HTTP `:8080`）**并行**，共用同一套采集逻辑，互不影响。

| 项目 | 值 |
|------|-----|
| 控制 TCP 端口 | **9100**（`EGO_TCP_PORT`） |
| 网页采集控制 | HTTP **8080**（启停录制 + 录制中单路预览） |
| 网页四路预览 | HTTP **8081**（待机四路同屏，**不含**采集控制） |
| 预览数据面 | HTTP MJPEG/JPEG **8765**（非 UDP；8080/8081/App 共用） |
| 130 有线地址 | `10.10.10.130` |
| 130 热点网关 | `192.168.8.1`（连热点后 App / 手机网页用此地址） |

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

### 4.2 device_info（查询设备状态）

连接成功后建议每 **1 秒** 轮询一次（或依赖连接时的主动推送）。

```json
{ "type": "device_info" }
```

设备回复 `device_info_result`（见 5.2）。兼容旧客户端：`get_device_status` 同等处理。

### 4.3 start_recording

```json
{ "type": "start_recording" }
```

设备回复：

1. `start_recording_result`（是否成功）
2. 成功时再推送 `device_info_result`

启动可能耗时 15–90 秒（与网页一致），此期间可继续发 `heartbeat`。App 侧建议超时 **60s**。

### 4.4 stop_recording

```json
{
  "type": "stop_recording",
  "data": { "is_save": true }
}
```

| `is_save` | 行为 | 等价网页 |
|-----------|------|----------|
| `true` | 停止并保存 | `POST /api/capture/stop` |
| `false` | 停止并放弃 | `POST /api/capture/abandon` |

设备回复 `stop_recording_result`，成功后再推送 `device_info_result`。停止/落盘可能耗时数十秒（建议超时 60s）。

兼容旧客户端：`stop_and_save_recording` 等价于 `is_save: true`。

### 4.5 change_view

切换 App **当前主预览画面**（客户端改拉哪一路 HTTP URL；**不切换相机硬件**）。

- 待机（`state=idle`）：四路 URL 均有 live 帧（standby 预览，约 8fps）。
- 采集中（`state=recording`）：**仅主预览路**有 live 帧（生产默认 `front_left`，约 4fps）；切到其他 `view_num` 可能 503 或停在旧帧，**不保证**四路 live。

```json
{
  "type": "change_view",
  "data": { "view_num": 2 }
}
```

设备回复 `view_changed_result`（含新的 `preview_mjpeg_url` / `preview_jpg_url`）。

---

## 5. 设备 → App（上行）

### 5.1 heartbeat_ack

```json
{ "type": "heartbeat_ack" }
```

### 5.2 device_info_result

**连接建立后设备主动发送一条**；收到 `device_info`、录制启停成功后也会推送。

```json
{
  "type": "device_info_result",
  "data": {
    "state": "idle",
    "duration": 0,
    "segment_count": 0,
    "storage_free_gb": 843.5,
    "frames_writing": false,
    "view_num": 1,
    "view_id": "front_left",
    "preview_mjpeg_url": "http://192.168.8.1:8765/preview/front_left/mjpeg",
    "preview_jpg_url": "http://192.168.8.1:8765/preview/front_left/jpg"
  }
}
```

`state` 取值：`idle` | `starting` | `warming` | `recording` | `stopping` | `error`（与 `GET /api/status` 一致）。

### 5.3 start_recording_result

```json
{
  "type": "start_recording_result",
  "data": { "success": true, "msg": "" }
}
```

### 5.4 stop_recording_result

```json
{
  "type": "stop_recording_result",
  "data": { "success": true, "is_save": true, "msg": "" }
}
```

### 5.5 view_changed_result

```json
{
  "type": "view_changed_result",
  "data": {
    "view_num": 2,
    "view_id": "front_right",
    "preview_mjpeg_url": "http://192.168.8.1:8765/preview/front_right/mjpeg",
    "preview_jpg_url": "http://192.168.8.1:8765/preview/front_right/jpg"
  }
}
```

### 5.6 error

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
| App→设备 | device_info | 无 | 查询状态（建议 1s 轮询） |
| App→设备 | start_recording | 无 | 开始录制 |
| App→设备 | stop_recording | is_save | 停止并保存/放弃 |
| App→设备 | change_view | view_num | 切换主预览 URL |
| 设备→App | heartbeat_ack | 无 | 心跳回复 |
| 设备→App | device_info_result | 见上 | 连接推送 + 状态查询/录制后 |
| 设备→App | start_recording_result | success, msg | 开始录制反馈 |
| 设备→App | stop_recording_result | success, is_save, msg | 停止录制反馈 |
| 设备→App | view_changed_result | 见上 | 视角切换确认 |
| 设备→App | error | 见上 | 协议或参数错误 |

---

## 7. 视频预览（HTTP，非 UDP）

### 7.1 端口与职责（热点 `192.168.8.1`）

| 端口 | 服务 | 用途 | 与采集关系 |
|------|------|------|------------|
| **9100** | `ego_tcp_bridge` | App TCP 控制 + 下发预览 URL | 与 8080 共用采集逻辑 |
| **8080** | `ecs-ego-web` | 手机网页：**启停录制**；录制中**单路**预览（`main.jpg`） | 生产采集页 |
| **8081** | `ecs-ego-web-staging`（`EGO_WEB_UI_MODE=quad`） | 手机网页：**待机四路 2×2 预览**；无启停按钮 | 仅预览，不控采集 |
| **8765** | `preview_server` | 四路 MJPEG/JPEG **数据源**（standby 或采集中继） | OAK 独占，单进程监听 |

**已废弃：** ~~8082 开发页（采集中四按钮切视角）~~，不再部署。

App 与网页**不要**在采集时依赖「四路同时 live」；四路同屏仅用于**待机检查**（8081 或 App 自行拉四路 URL）。

### 7.2 预览 URL（数据面 `:8765`）

四路 `view_id`：`front_left` | `front_right` | `rear_left` | `rear_right`。

| 格式 | URL 模板 |
|------|----------|
| MJPEG 流 | `http://{host}:8765/preview/{view_id}/mjpeg` |
| 单帧 JPEG | `http://{host}:8765/preview/{view_id}/jpg` |
| 状态 | `GET http://{host}:8765/preview/status` → `{"online":true,"fps":…}` |

`{host}`：热点 **`192.168.8.1`**；实验室有线 **`10.10.10.130`**。  
App **优先**使用 `device_info` / `view_changed` 中的 `preview_mjpeg_url`，勿硬编码 IP（除热点固定网关场景）。

8081 网页四路通过代理访问（等价于直连 8765）：

| 接口 | 说明 |
|------|------|
| `GET /api/preview/cameras` | `layout: "quad"`，四路元数据 |
| `GET /api/preview/{view_id}.jpg` | 单路 JPEG（四路独立轮询，**无需帧同步**） |

**`view_id` 与物理朝向：** URL/格子名（`front_left` 等）固定；画面来自哪颗 OAK 插座由边缘 **`EGO_TOPOLOGY_FILE`**（`camera_topology_<station>.yaml` 的 `socket_to_role`）决定。每站头盔布线不同（ego-001 ≠ ego-002 v2）；新机须标定拓扑，见 `data-lab-platform/docs/ego-gpd-station-migration-checklist.md` §2.13 与 `ego-002-edge-deploy.md` §3。

### 7.3 待机 vs 采集：谁能看到几路 live？

| 设备状态 | 预览生产者 | 四路 live？ | 典型帧率 / 分辨率 |
|----------|------------|------------|-------------------|
| **idle**（未录制） | `preview_standby` | **是**（四路） | ~8fps；边长 ≤640 |
| **warming / recording** | 采集栈 `record_oak_mcap` | **否**（仅主预览路） | 主路 ~4fps；边长 384（生产默认 `front_left`） |

生产环境（ego-001）采集侧配置要点：

- `OAK_HW_PREVIEW_H264_CAMS=front_left` — 采集中**只开一路**低分辨率 MJPEG 旁路，避免拖慢 30fps H.264 主采集。
- `PREVIEW_FPS=4`、`PREVIEW_MAX_EDGE=384` — 预览与写盘解耦。

因此：

- **8081 四路页**：适合连接热点后**开始录制前**检查四相机；采集中可能仅一路更新、其余冻结或 503。
- **App `change_view`**：采集中仅保证切换到**已配置旁路**的视角（默认前左）；**不承诺**四路任意切换均有 live 画面。

### 7.4 生命周期与冲突（工程约束）

1. **OAK 与 :8765 互斥**：同一时刻只有 standby **或** 采集栈之一占用相机并监听 8765。
2. **开始录制**：8080 / TCP `start_recording` 会先停 standby，再启采集；8081 四路预览可能有 1–3s 断帧。
3. **预览只读**：8080、8081、App 可同时 GET JPEG，不干扰写盘。
4. **采集中勿重复 start**：以 `device_info.state` 为准；`recording` / `warming` 时禁止再发 `start_recording`。

**说明：** 初版 Unity 文档中的 UDP 视频未实现；以 `preview_server` HTTP 为准。

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
| 采集中四路切换预览 | **不支持**；待机四路见 **8081** / 8765；采集仅主预览路 live |
| 网页仅 8080 | 增加 **8081** 四路待机预览页 |
