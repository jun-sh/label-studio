# EGO 边缘站 · 极简本地采集控制页

为 **10.10.10.214**（`ego-lan-214`）提供手机端网页，一键启停 `ecs-oak-capture-stack.target`，对标 [DAS Ego](https://docs.genrobot.ai/zh/products/das-ego) 近场控制范式。**不修改**现有采集栈代码与 systemd 单元。

源码目录：`data-lab-platform/ego-local-web/`

---

## 交付物

| 文件 | 说明 |
|------|------|
| `ego_web.py` | 单文件 Web 服务（标准库）+ 内嵌移动端页面 |
| `polkit/50-ego-hotspot.rules` | 授权 `server` 用户操作 NM 热点 |
| `systemd/ecs-ego-web.service` | user 级 systemd 单元 |
| `deploy.sh` / `rollback.sh` | 一行部署 / 回滚 |
| `scripts/hotspot-setup.sh` | 一次性热点 + Polkit（需 sudo） |

---

## 快速部署（214 上）

### 1. 拷贝仓库文件到边缘机

```bash
# 在开发机（有 data-lab 仓库）
rsync -av data-lab-platform/ego-local-web/ server@10.10.10.214:~/ego-local-web-src/
```

### 2. 一次性：热点 + Polkit（需 sudo）

```bash
ssh server@10.10.10.214
sudo bash ~/ego-local-web-src/scripts/hotspot-setup.sh
```

验证（开机后热点应自动广播，也可手动）：

```bash
systemctl status ecs-ego-hotspot
# 手机连 SSID: EGO-214-COLLECT / 密码: ego12345（WPA2 最少 8 位）
```

### 3. 部署 Web 服务（server 用户）

```bash
bash ~/ego-local-web-src/deploy.sh
```

访问：

- 实验室有线网：`http://10.10.10.214:8080`
- 热点网关：`http://192.168.4.1:8080`（热点开启后）

### 4. 回滚

```bash
bash ~/ego-local-web-src/rollback.sh
sudo rm -f /etc/polkit-1/rules.d/50-ego-hotspot.rules
nmcli connection delete EGO-214-COLLECT 2>/dev/null || true
```

---

## API 契约

| 路径 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 移动端控制页 |
| `/api/status` | GET | `{"state":"idle\|recording\|starting\|stopping\|error","duration":0,"storage_free":"12.3 GB","storage_warn":false,"segment_count":0,"msg":"","capture_active":false}` |
| `/api/capture/start` | POST | `{"success":true,"msg":""}` |
| `/api/capture/stop` | POST | `{"success":true,"msg":""}` |
| `/api/preview/main.jpg` | GET | JPEG（反代 `127.0.0.1:8765` 主路预览） |

`state` 说明：

- `idle` — 采集栈未运行
- `recording` — 采集中，`duration` 为秒
- `starting` / `stopping` — 过渡态，按钮锁定
- `error` — 最近一次操作失败，`msg` 为白话说明

---

## 验收标准

| # | 步骤 | 通过条件 |
|---|------|----------|
| 1 | 手机连热点或实验室 WiFi，打开 `http://192.168.4.1:8080` 或 `http://10.10.10.214:8080` | 页面显示灰色「设备待机中」 |
| 2 | 点击「开始录制」 | **30s 内**状态变绿「正在录制中」，预览区出现画面 |
| 3 | 点击「结束录制」 | 出现「正在保存数据」，完成后回到待机；`segments/sessions/.../seg_*` 下有关闭段 |
| 4 | 断开 34 网络（或阻断心跳 URL） | 启停与落盘仍正常 |
| 5 | 执行 `rollback.sh` + 删除 polkit/热点 | 无 `ecs-ego-web` 服务、`/home/server/ego-web` 目录 |

辅助检查：

```bash
systemctl --user is-active ecs-oak-capture-stack.target
ls /home/server/cache/ego-lan-214/segments/sessions/*/segments/
journalctl --user -u ecs-ego-web -n 30
curl -s http://127.0.0.1:8080/api/status | python3 -m json.tool
```

---

## 大按钮 UI 设计示例

当前页面采用 **「状态条 + 单主按钮 + 单路预览」** 范式，与 DAS Ego「灯语 + 单键启停」对齐。以下为可替换样式参考（均在 `ego_web.py` 内嵌 CSS 中实现或微调）。

### 示例 A：胶囊全宽按钮（当前默认）

```
┌─────────────────────────────┐
│     ● 设备待机中  (灰条)      │
├─────────────────────────────┤
│      [ 预览区域 4:3 ]        │
├─────────────────────────────┤
│  ╭───────────────────────╮  │
│  │     开始录制  (蓝)     │  │  ← min-height 64px, border-radius 999px
│  ╰───────────────────────╯  │
└─────────────────────────────┘
```

录制中按钮变为红色「结束录制」。

### 示例 B：底部固定大按钮（适合超长屏）

```css
.btn-main {
  position: fixed; left: 16px; right: 16px; bottom: calc(16px + env(safe-area-inset-bottom));
  z-index: 10;
}
```

拇指单手更易触达，预览区需加 `padding-bottom` 防遮挡。

### 示例 C：圆形 FAB（极简）

```
        ┌───────┐
        │  ● REC │  直径 72px 圆形，录制中脉冲动画
        └───────┘
```

适合只保留启停、隐藏预览的「阿姨模式」；可在设置开关中切换。

### 示例 D：双色分栏（误触更低）

```
┌──────────────┬──────────────┐
│  开始 (绿)    │  结束 (灰/dis) │  仅录制中启用结束侧
└──────────────┴──────────────┘
```

比单按钮切换更不易误触结束，但占用横向空间。

**推荐**：现场默认用 **示例 A**；若反馈误触多，再切 **示例 D**。

---

## 安全性

| 风险 | 缓解 |
|------|------|
| 无登录，局域网内任何人可启停 | 热点使用 WPA2 密码；仅开放采集 VLAN / 孤立 AP；不暴露到公网 |
| CSRF | 接口仅接受同源 POST；无 Cookie 会话 |
| 重复点击导致异常 | 服务端状态锁 + 最小操作间隔 3s；前端按钮禁用 |
| systemctl 注入 | 固定 target 名，不接收用户输入 |
| Polkit 过宽 | 规则仅放行 NM 相关 action + `subject.user == "server"` |
| 8080 端口扫描 | 可配合 `ufw allow from 192.168.4.0/24 to any port 8080` 限制来源网段 |
| 敏感数据经网页 | 仅反代预览 JPEG，不列目录、不提供段文件下载 |

进阶（可选二期）：4 位 PIN 存于 `/home/server/ego-web/.pin`（不在仓库内），`POST` 需带 `X-Pin` 头。

---

## 稳定性

| 机制 | 说明 |
|------|------|
| 采集栈独立进程 | Web 崩溃不影响已运行的 `ecs-oak-capture-stack` |
| `Restart=on-failure` | Web 服务自动拉起 |
| 启停超时 | 启动 45s / 停止 120s，避免无限等待 |
| 预览失败 | 返回 503，前端继续轮询状态，不阻塞启停 |
| 双网监听 | `0.0.0.0:8080` 同时服务热点与 `10.10.10.214` |
| 零 pip 依赖 | 仅 `python3` 标准库，减少环境漂移 |
| 非侵入部署 | 不 patch `ego-studio` / 采集单元；`rollback.sh` 可完全移除 |

**注意**：开启热点会断开 `wlp2s0` 上原有 WiFi 客户端连接；SSH 走 USB 网卡 `10.10.10.214` 不受影响。现场采集前再 `nmcli connection up EGO-214-COLLECT`。

---

## 环境变量（`ecs-ego-web.service`）

| 变量 | 默认 |
|------|------|
| `EGO_WEB_HOST` | `0.0.0.0` |
| `EGO_WEB_PORT` | `8080` |
| `EGO_SEGMENT_ROOT` | `/home/server/cache/ego-lan-214/segments` |
| `EGO_CAPTURE_TARGET` | `ecs-oak-capture-stack.target` |
| `EGO_PREVIEW_URL` | `http://127.0.0.1:8765/preview/front_left/jpg` |
| `EGO_STORAGE_WARN_GB` | `2` |

---

## 与 34 采集页的关系

| | 214 本地页 | 34 `/collection?station=ego-lan-214` |
|--|-----------|--------------------------------------|
| 用户 | 现场操作员 | 办公室质检 / 导入 |
| 网络 | 热点 / 局域网直连 214 | 需访问 34 |
| 功能 | 启停 + 单路预览 | LeRobot 回放、拖传导入 |

---

## 相关文档

- [ego-edge-offline-upload-and-deployment.md](../../docs/ego-edge-offline-upload-and-deployment.md)
- [ego-lan-214-segment-storage-and-upload.md](../../docs/ego-lan-214-segment-storage-and-upload.md)
