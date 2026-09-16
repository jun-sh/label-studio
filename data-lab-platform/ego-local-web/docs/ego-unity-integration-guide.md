# Ego 采集 App · Unity 集成指南（含 Demo 与状态判断）

> 版本：rev.20260916  
> 适用：现场采集 App（手机连设备热点，**仅使用 `192.168.8.1`**）  
> 仓库 Demo 代码：`data-lab-platform/ego-unity-demo/Assets/EgoDemo/Scripts/`

---

## 1. 你需要知道的最少信息

| 项目 | 值 | 说明 |
|------|-----|------|
| 控制通道 | **TCP `192.168.8.1:9100`** | App 作客户端，设备作服务端 |
| 分帧 | 4 字节 **大端 int32** + UTF-8 JSON | 必须处理半包/粘包 |
| 预览 | **HTTP**（非 UDP） | URL 由 `device_info` / `view_changed` 下发，不要自己拼 IP |
| 心跳 | 每 10 秒 `heartbeat` | 连接保活；**不代表业务成功** |

**不需要了解：** 网页 `8080`、平台 `34`、上传链路——与 Unity App 无关。

---

## 2. 整体调用流程

```
┌──────────────┐                      ┌─────────────────────┐
│  Unity App   │  TCP 192.168.8.1:9100 │  ego_tcp_bridge     │
│              │◄────────────────────►│  (设备端)            │
└──────┬───────┘                      └──────────┬──────────┘
       │                                         │
       │  HTTP GET preview_*_url                 │ 内部调用 ego_web
       ▼                                         ▼ 采集栈 / 预览服务
┌──────────────┐                      ┌─────────────────────┐
│ 预览 RawImage │                      │ preview_server :8765 │
└──────────────┘                      └─────────────────────┘
```

### 2.1 推荐时序（首次连接）

```
App                          设备
 │ Connect TCP :9100           │
 │────────────────────────────►│
 │◄────────────────────────────│ device_info（主动推送）
 │                             │
 │ heartbeat（每 10s）          │
 │────────────────────────────►│
 │◄────────────────────────────│ heartbeat_ack
 │                             │
 │ start_recording             │
 │────────────────────────────►│  （可能阻塞 15~90s）
 │◄────────────────────────────│ capture_result
 │◄────────────────────────────│ device_info（成功时）
 │                             │
 │ HTTP GET preview_mjpeg_url  │  （并行，非 TCP）
 │────────────────────────────►│ :8765
```

### 2.2 消息一览

| 方向 | type | 何时发 | 设备回复 |
|------|------|--------|----------|
| App→设备 | `heartbeat` | 每 10s | `heartbeat_ack` |
| App→设备 | `get_device_status` | 重连后/需要刷新 | `device_info` |
| App→设备 | `start_recording` | 用户点开始 | `capture_result` + `device_info` |
| App→设备 | `stop_and_save_recording` | 用户点停止 | `capture_result` + `device_info` |
| App→设备 | `change_view` | 用户切视角 | `view_changed` |
| 设备→App | `device_info` | 连接时主动推 | — |
| 设备→App | `error` | 参数错误等 | — |

---

## 3. TCP 分帧（C# 必读）

TCP 是字节流，**没有天然消息边界**。每条消息格式：

```
[ Length: 4 字节大端 int32 ][ Body: Length 字节的 UTF-8 JSON ]
```

### 3.1 发送

```csharp
/// <summary>
/// 将 JSON 打包为设备可识别的 TCP 帧。
/// Length = Body 的 UTF-8 字节数（不含 4 字节包头）。
/// </summary>
public static byte[] EncodeFrame(string json)
{
    byte[] body = System.Text.Encoding.UTF8.GetBytes(json);
    byte[] frame = new byte[4 + body.Length];
    int len = body.Length;
    frame[0] = (byte)((len >> 24) & 0xFF); // 大端：最高字节在前
    frame[1] = (byte)((len >> 16) & 0xFF);
    frame[2] = (byte)((len >> 8) & 0xFF);
    frame[3] = (byte)(len & 0xFF);
    Buffer.BlockCopy(body, 0, frame, 4, body.Length);
    return frame;
}

/// <summary>无 data 字段的消息，例如 heartbeat。</summary>
public static string BuildJson(string type, string dataJson = null)
{
    if (string.IsNullOrEmpty(dataJson))
        return $"{{\"type\":\"{type}\"}}";
    return $"{{\"type\":\"{type}\",\"data\":{dataJson}}}";
}

// 示例：发送心跳
// stream.Write(EncodeFrame(BuildJson("heartbeat")));
```

### 3.2 接收（必须处理半包/粘包）

```csharp
/// <summary>
/// 从接收缓冲区解析完整 JSON 消息。循环调用直到无新消息。
/// </summary>
public static List<string> FeedBuffer(List<byte> buffer)
{
    var messages = new List<string>();
    while (buffer.Count >= 4)
    {
        int length = (buffer[0] << 24) | (buffer[1] << 16) | (buffer[2] << 8) | buffer[3];
        if (length <= 0 || length > 1_048_576) throw new Exception("非法帧长度");
        if (buffer.Count < 4 + length) break; // 半包：继续读 socket

        string json = System.Text.Encoding.UTF8.GetString(buffer.GetRange(4, length).ToArray());
        buffer.RemoveRange(0, 4 + length);
        messages.Add(json);
    }
    return messages;
}
```

---

## 4. `device_info` 字段说明

连接成功或 `get_device_status` 后收到：

```json
{
  "type": "device_info",
  "data": {
    "state": "idle",
    "duration": 0,
    "segment_count": 0,
    "storage_free_gb": 843.5,
    "frames_writing": false,
    "view_num": 1,
    "view_id": "front_left",
    "preview_mjpeg_url": "http://192.168.8.1:8765/preview/front_left/mjpeg",
    "preview_jpg_url": "http://192.168.8.1:8765/preview/front_left/jpg",
    "msg": ""
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `state` | string | **主状态**，UI 首选依据（见第 5 节） |
| `duration` | int | 当前段已录制秒数；仅 `recording` 时递增 |
| `segment_count` | int | 已落盘段数 |
| `storage_free_gb` | float | 剩余空间（GB） |
| `frames_writing` | bool | **是否正在写帧到磁盘**（见第 5 节） |
| `view_num` | int | 当前视角 1~4 |
| `view_id` | string | `front_left` / `front_right` / `rear_left` / `rear_right` |
| `preview_mjpeg_url` | string | MJPEG 连续流（推荐预览） |
| `preview_jpg_url` | string | 单帧 JPEG（轮询简单） |
| `msg` | string | 提示/错误文案，可为空 |

---

## 5. 核心：`state` 与 `frames_writing` 如何判断「正在采集」

### 5.1 两个字段的区别

| 字段 | 含义 | 典型用途 |
|------|------|----------|
| **`state`** | 面向用户的**综合状态**（含过渡态） | **UI 显示、按钮启用/禁用** |
| **`frames_writing`** | 采集栈已就绪且**正在向磁盘写帧** | 精确判断「计时是否该走」 |

设备端逻辑（简化）：

```
frames_writing = true  当且仅当：
  采集进程在运行
  AND 相机已就绪（beep 已响，开始写帧）
  AND 不在 stopping/deactivating
```

### 5.2 `state` 取值与 UI 建议

| state | 含义 | UI 建议 | 能否点「开始」 | 能否点「停止」 |
|-------|------|---------|---------------|---------------|
| `idle` | 待机，未录制 | 灰/待机 | ✅ | ❌ |
| `starting` | 正在响应 start 命令 | 加载中 | ❌ | ❌ |
| `warming` | 相机预热，**还没写帧** | 「准备中…」 | ❌ | ✅（可结束） |
| **`recording`** | **正在录制** | 红/录制中 + 计时 | ❌ | ✅ |
| `stopping` | 正在停止/落盘 | 「保存中…」 | ❌ | ❌ |
| `error` | 上次操作失败 | 显示 `msg` | 视情况 | 视情况 |

### 5.3 判断「是否正在采集」——推荐写法

```csharp
/// <summary>
/// 是否应显示为「录制中」UI（含预热中、即将写帧）。
/// 用于：按钮状态、顶部状态栏。
/// </summary>
public static bool IsCaptureSessionActive(DeviceInfoData info)
{
    if (info == null) return false;
    switch (info.state)
    {
        case "warming":
        case "recording":
        case "stopping":   // 仍在收尾，勿重复 start
            return true;
        default:
            return false;
    }
}

/// <summary>
/// 是否正在真正写帧（计时器应递增）。
/// 仅 state==recording 时通常为 true；warming 时为 false。
/// </summary>
public static bool IsActuallyWritingFrames(DeviceInfoData info)
{
    return info != null && info.state == "recording" && info.frames_writing;
}

/// <summary>
/// 是否允许发起 start_recording。
/// </summary>
public static bool CanStartRecording(DeviceInfoData info)
{
    return info != null && info.state == "idle" && !info.frames_writing;
}

/// <summary>
/// 是否允许发起 stop_and_save_recording。
/// </summary>
public static bool CanStopRecording(DeviceInfoData info)
{
    if (info == null) return false;
    return info.state == "warming" || info.state == "recording";
}
```

### 5.4 状态迁移示意

```
idle ──start_recording──► starting ──► warming ──► recording ──stop──► stopping ──► idle
  ▲                                                                              │
  └──────────────────────────────────────────────────────────────────────────────┘
```

- **`warming`**：`frames_writing == false`，`duration == 0`——相机在初始化，**还不算正式录到数据**
- **`recording`**：`frames_writing == true`，`duration` 递增——**正式采集**
- 不要仅凭 `frames_writing` 判断能否 stop：`warming` 时也应允许用户点停止

### 5.5 App 断线重连（重要）

**录制在设备端进行，与 TCP 连接无关。** App 崩溃或断网不会自动停止录制。

重连流程：

```
1. TCP Connect(192.168.8.1, 9100)
2. 等待设备主动推送 device_info（连接即发）
3. 若无推送或需刷新：Send get_device_status
4. 根据 device_info.state 恢复 UI：
   - recording / warming → 显示「录制中」，禁止再次 start
   - idle → 显示待机
5. 恢复预览：用 device_info.preview_mjpeg_url 重新拉流
6. 恢复心跳定时器（每 10s）
```

```csharp
/// <summary>重连后根据 device_info 恢复 UI，避免重复 start 导致冲突。</summary>
public void OnReconnected(DeviceInfoData info)
{
    if (IsCaptureSessionActive(info))
    {
        ShowRecordingUI(info);
        if (IsActuallyWritingFrames(info))
            StartDurationTimer(info.duration); // 用设备返回的 duration 续计时
        else
            ShowWarmingHint(info.msg);
        DisableStartButton();
        EnableStopButton();
    }
    else
    {
        ShowIdleUI(info);
        EnableStartButton();
        DisableStopButton();
    }

    // 预览 URL 以设备返回为准（含 192.168.8.1）
    ReloadPreview(info.preview_mjpeg_url);
}
```

---

## 6. 各接口调用 Demo（带注释的 C# 伪代码）

以下整合为单一流程类，可直接对照仓库 `EgoTcpClient.cs` + `EgoDemoApp.cs`。

```csharp
using System;
using System.Collections.Generic;
using System.Net.Sockets;
using System.Threading;
using UnityEngine;

/// <summary>
/// Ego 设备 TCP 客户端 — Unity 集成参考实现。
/// 热点场景：Host = "192.168.8.1", Port = 9100
/// </summary>
public class EgoDeviceClient : MonoBehaviour
{
    const string Host = "192.168.8.1";
    const int Port = 9100;
    const float HeartbeatInterval = 10f;

    TcpClient _tcp;
    NetworkStream _stream;
    Thread _readThread;
    readonly List<byte> _rxBuffer = new List<byte>();
    DeviceInfoData _lastInfo;
    float _heartbeatTimer;

    // ── 1. 连接 ──────────────────────────────────────────────

    public void Connect()
    {
        _tcp = new TcpClient();
        _tcp.NoDelay = true;
        _tcp.Connect(Host, Port);
        _stream = _tcp.GetStream();

        // 后台线程只负责读 socket + 解析帧；UI 更新放主线程
        _readThread = new Thread(ReadLoop) { IsBackground = true };
        _readThread.Start();

        // 连接成功后设备会主动发 device_info，无需立刻 get_device_status
        _heartbeatTimer = 0f;
    }

    // ── 2. 发送消息 ────────────────────────────────────────────

    void Send(string type, string dataJson = null)
    {
        string json = string.IsNullOrEmpty(dataJson)
            ? $"{{\"type\":\"{type}\"}}"
            : $"{{\"type\":\"{type}\",\"data\":{dataJson}}}";
        byte[] frame = EncodeFrame(json);
        _stream.Write(frame, 0, frame.Length);
        Debug.Log($"→ {json}");
    }

    // ── 3. 业务接口 ────────────────────────────────────────────

    /// <summary>每 10 秒调用一次，在 Update 里累加计时。</summary>
    public void SendHeartbeat() => Send("heartbeat");

    /// <summary>主动刷新状态（重连后可选）。</summary>
    public void QueryStatus() => Send("get_device_status");

    /// <summary>
    /// 开始录制。阻塞等待 capture_result 可达 90s。
    /// 发送前务必检查 CanStartRecording(_lastInfo)。
    /// </summary>
    public void StartRecording()
    {
        if (!EgoCaptureState.CanStartRecording(_lastInfo))
        {
            Debug.LogWarning($"拒绝 start：当前 state={_lastInfo?.state}");
            return;
        }
        Send("start_recording");
    }

    /// <summary>
    /// 停止并保存。可能阻塞 30~120s（落盘）。
    /// warming / recording 时可调用。
    /// </summary>
    public void StopRecording()
    {
        if (!EgoCaptureState.CanStopRecording(_lastInfo))
            return;
        Send("stop_and_save_recording");
    }

    /// <summary>切换预览视角。viewNum: 1~4。</summary>
    public void ChangeView(int viewNum)
    {
        Send("change_view", $"{{\"view_num\":{viewNum}}}");
    }

    // ── 4. 收包与分发 ──────────────────────────────────────────

    void ReadLoop()
    {
        var chunk = new byte[8192];
        try
        {
            while (true)
            {
                int n = _stream.Read(chunk, 0, chunk.Length);
                if (n <= 0) break;
                lock (_rxBuffer)
                {
                    for (int i = 0; i < n; i++) _rxBuffer.Add(chunk[i]);
                    foreach (string json in FeedBuffer(_rxBuffer))
                        EnqueueMainThread(() => HandleMessage(json));
                }
            }
        }
        catch (Exception ex)
        {
            EnqueueMainThread(() => OnDisconnected(ex.Message));
        }
    }

    void HandleMessage(string json)
    {
        Debug.Log($"← {json}");
        var type = ExtractType(json); // 简易解析或 JsonUtility

        switch (type)
        {
            case "device_info":
                _lastInfo = ParseDeviceInfo(json);
                OnDeviceInfo(_lastInfo);
                break;

            case "heartbeat_ack":
                // 仅表示收到心跳，忽略即可
                break;

            case "capture_result":
                var result = ParseCaptureResult(json);
                if (!result.success)
                    ShowError(result.msg);
                // 成功时设备会再发 device_info，以 device_info 更新 UI
                break;

            case "view_changed":
                var view = ParseViewChanged(json);
                ReloadPreview(view.preview_mjpeg_url);
                break;

            case "error":
                ShowError(ParseError(json).message);
                break;
        }
    }

    // ── 5. Update：心跳 + 主线程队列 ─────────────────────────────

    void Update()
    {
        DrainMainThreadQueue();

        if (_tcp == null || !_tcp.Connected) return;

        _heartbeatTimer += Time.deltaTime;
        if (_heartbeatTimer >= HeartbeatInterval)
        {
            _heartbeatTimer = 0f;
            try { SendHeartbeat(); }
            catch { OnDisconnected("heartbeat failed"); }
        }
    }

    void OnDeviceInfo(DeviceInfoData info)
    {
        // 统一入口：首次连接、get_device_status、capture 完成后都会进这里
        UpdateStatusUI(info);
        UpdateButtons(info);
        ReloadPreview(info.preview_mjpeg_url);

        // 录制计时：仅 frames_writing 为 true 时用 duration
        if (EgoCaptureState.IsActuallyWritingFrames(info))
            SetRecordingDuration(info.duration);
    }

    void UpdateButtons(DeviceInfoData info)
    {
        startButton.interactable = EgoCaptureState.CanStartRecording(info);
        stopButton.interactable = EgoCaptureState.CanStopRecording(info);
    }
}

/// <summary>状态判断工具类 — 建议原样复制到工程。</summary>
public static class EgoCaptureState
{
    public static bool IsCaptureSessionActive(DeviceInfoData info) =>
        info != null && (info.state == "warming" || info.state == "recording" || info.state == "stopping");

    public static bool IsActuallyWritingFrames(DeviceInfoData info) =>
        info != null && info.state == "recording" && info.frames_writing;

    public static bool CanStartRecording(DeviceInfoData info) =>
        info != null && info.state == "idle" && !info.frames_writing;

    public static bool CanStopRecording(DeviceInfoData info) =>
        info != null && (info.state == "warming" || info.state == "recording");
}
```

---

## 7. 视频预览（HTTP，非 UDP）

预览**不走 TCP 9100**，使用 `device_info` 返回的 URL：

| 方式 | URL 字段 | Unity 实现 |
|------|----------|------------|
| 连续画面（推荐） | `preview_mjpeg_url` | `UnityWebRequest` 读流，或第三方 MJPEG 插件 |
| 单帧轮询（简单） | `preview_jpg_url` | 每 0.5s `UnityWebRequestTexture.GetTexture(url + "?t=" + tick)` |

```csharp
// 单帧轮询示例（Demo 采用此方式）
IEnumerator PollPreview(string jpgUrl)
{
    while (connected)
    {
        using var req = UnityEngine.Networking.UnityWebRequestTexture.GetTexture(
            jpgUrl + "?t=" + DateTime.UtcNow.Ticks);
        yield return req.SendWebRequest();
        if (req.result == UnityEngine.Networking.UnityWebRequest.Result.Success)
            previewImage.texture = UnityEngine.Networking.DownloadHandlerTexture.GetContent(req);
        yield return new WaitForSeconds(0.5f);
    }
}
```

**注意：**

- URL 以 `device_info` / `view_changed` 为准，**不要写死 IP**
- 热点场景设备返回 `http://192.168.8.1:8765/...`
- 预览是设备自研轻量 HTTP 服务（`preview_server.py`），非第三方 RTSP/WebRTC 平台

---

## 8. 常见错误与处理

| 现象 | 原因 | 处理 |
|------|------|------|
| 连不上 9100 | 未连热点 / 桥接未启动 | 先连 `EGO-xxx-COLLECT`，检查 `ecs-ego-tcp-bridge` |
| 预览黑屏 | URL 用了 10.10.10.130 | 必须用 `device_info` 返回的 192.168.8.1 URL |
| 重复 start 报错 | 已在 recording | 重连后先 `get_device_status`，看 `state` |
| start 很久无响应 | 相机冷启动 15~90s | UI 显示 loading，继续发 heartbeat |
| `warming` 很久 | 相机初始化 | 正常，可显示「准备中」，允许 stop |
| `frames_writing` false 但 state recording | 极少见边界 | 以 `state` 为准显示录制中 |

---

## 9. 联调检查清单

- [ ] 手机连热点，能 ping / 访问 `192.168.8.1`
- [ ] TCP 连接 `192.168.8.1:9100` 成功
- [ ] 收到首条 `device_info`
- [ ] 心跳 `heartbeat` → `heartbeat_ack`
- [ ] `change_view` → `view_changed`，预览切换
- [ ] `start_recording` → `capture_result` + `state` 变为 `warming` → `recording`
- [ ] `recording` 时 `frames_writing == true`，`duration` 递增
- [ ] 断开 TCP 再连，`get_device_status` 仍能读到 `recording`
- [ ] `stop_and_save_recording` → `state` 回 `idle`

---

## 10. 仓库参考文件

| 文件 | 说明 |
|------|------|
| `ego-unity-demo/Assets/EgoDemo/Scripts/EgoTcpClient.cs` | TCP 分帧 + 后台收包 |
| `ego-unity-demo/Assets/EgoDemo/Scripts/EgoDemoApp.cs` | UI + 心跳 + 预览轮询 |
| `ego-unity-demo/Assets/EgoDemo/Scripts/EgoMessageParser.cs` | JSON 数据类 |
| `ego-local-web/tools/ego_tcp_client_test.py` | Python 联调脚本（无 Unity 时对照） |
| `ego-local-web/docs/ego-app-tcp-protocol.md` | 协议字段速查 |

---

## 附录：`view_changed` 字段

```json
{
  "type": "view_changed",
  "data": {
    "view_num": 2,
    "view_id": "front_right",
    "preview_mjpeg_url": "http://192.168.8.1:8765/preview/front_right/mjpeg",
    "preview_jpg_url": "http://192.168.8.1:8765/preview/front_right/jpg"
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `view_num` | int32 | 视角编号 1~4 |
| `view_id` | string | 视角 ID |
| `preview_mjpeg_url` | string | 该视角 MJPEG 地址 |
| `preview_jpg_url` | string | 该视角 JPEG 地址 |
