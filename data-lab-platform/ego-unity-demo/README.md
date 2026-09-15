# Ego Unity 上位机 Demo

模拟手机 App 通过 **TCP JSON（:9100）** 控制 130 采集站，并拉取 **HTTP JPEG 预览**。

与协议文档对齐：`../ego-local-web/docs/ego-app-tcp-protocol.md`

## 环境要求

- Unity **2021.3 LTS** 或更高（已测 2022.3）
- 无需第三方包（仅用 UGUI + 标准库）

## 快速开始

### 无 Unity 环境（本机自动联调）

```bash
cd data-lab-platform/ego-unity-demo
bash run-demo.sh
```

等价于 Unity 里点 **Connect**（走 TCP 9100，不启停录制）。

### Unity GUI（2022.3 LTS）

1. Unity Hub → Open → `data-lab-platform/ego-unity-demo`
2. 打开 `Assets/EgoDemo/Scenes/EgoDemo.unity`（已预生成）
3. 点 **Play** — UI 运行时自动创建，**无需**菜单 Create Demo Scene
4. 点 **Connect**（默认 `10.10.10.130:9100`）

也可将 `Assets/EgoDemo` 复制到现有工程；任意场景 Play 也会自动启动 Demo。

### 方式 C：导入到现有 Unity 工程

复制 `Assets/EgoDemo` → 你的工程 `Assets/` → Play。

## 界面功能

| 控件 | 对应协议 |
|------|----------|
| Connect / Disconnect | TCP 连接 |
| 自动心跳 | 每 10s `heartbeat` |
| Refresh Status | `get_device_status` |
| Start / Stop Recording | `start_recording` / `stop_and_save_recording` |
| View 1–4 | `change_view` |
| Preview | 轮询 `preview_jpg_url`（`device_info` 内） |
| Log | 收发消息日志 |

## 联调地址

| 网络 | Host | 控制端口 | 预览 |
|------|------|----------|------|
| 实验室有线 | `10.10.10.130` | 9100 | `:8765` |
| 130 热点 | `192.168.8.1` | 9100 | `:8765` |

确保 130 已部署 `ecs-ego-tcp-bridge`：

```bash
systemctl --user status ecs-ego-tcp-bridge
python3 tools/ego_tcp_client_test.py --host 10.10.10.130 --skip-capture
```

## 脚本结构

```
Assets/EgoDemo/Scripts/
  EgoProtocol.cs          # 消息名、视角映射
  EgoFrameCodec.cs        # 4 字节大端分帧
  EgoTcpClient.cs         # 后台线程收包 + 主线程事件
  EgoDemoApp.cs           # UI + 心跳 + 预览
  EgoMessageParser.cs     # JSON 解析
Assets/EgoDemo/Editor/
  EgoDemoSceneSetup.cs    # 一键建场景
```

## 给 Unity 工程师的集成提示

- 生产 App 可将 `EgoTcpClient` 挂到单例，`EgoDemoApp` 仅作 UI 参考
- 录制命令可能阻塞 15–120s，UI 应禁用按钮并继续心跳（客户端已支持）
- 预览当前为 HTTP JPEG 轮询；若需 MJPEG 可换 `UnityWebRequest` 流式解析或原生插件
