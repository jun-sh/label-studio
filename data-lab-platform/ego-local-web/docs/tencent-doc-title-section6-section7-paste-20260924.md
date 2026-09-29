# 腾讯文档粘贴稿 — 改标题 + §6 补行 + 合并 §7.4/7.5（2026-09-24）

> 在线文档：[Ego设备与App通讯接口文档](https://docs.qq.com/aio/DUnpWeXdIa2NGRWZp?p=tqgnPGuz0dX1AjnOT3CaVz)  
> 仓库同步：`ego-app-tcp-protocol.md`  
> 预计耗时：约 5 分钟

---

## ① 改标题（页面顶部大标题）

**删除：** `Ego设备与App通讯接口文档（初版）`

**替换为：**

```text
Ego设备与App通讯接口文档（修订版 · 与 130 实现对齐）
```

（可选副标题一行，放在标题下方正文区：）

```text
设备端 TCP :9100 + HTTP 预览 :8765；与 ego-local-web 网页采集逻辑并行，互不影响。
```

---

## ② §6 消息方向汇总表 — 补一行

在 **「设备端 → EgoApp」** 区块、`view_changed_result` 那一行**下面**，插入：

| 方向 | 消息类型 | 有无业务数据 | 说明 |
|------|----------|--------------|------|
| 设备端 → EgoApp | prepare_multi_preview_result | 有 | 四路预览准备反馈（phase / success / msg） |

**若 §6 是四列表格**（方向 / 消息类型 / 有无业务数据 / 说明），整表替换为下方完整版，避免漏行：

| 方向 | 消息类型 | 有无业务数据 | 说明 |
|------|----------|--------------|------|
| EgoApp → 设备端 | heartbeat | 无 | 心跳检测（每 10 秒一次） |
| EgoApp → 设备端 | device_info | 无 | 获取设备状态（连接成功后 1s 获取一次） |
| EgoApp → 设备端 | start_recording | 无 | 开始录制 |
| EgoApp → 设备端 | stop_recording | 有 | 停止录制（is_save：true 保存，false 放弃） |
| EgoApp → 设备端 | change_view | 有 | 切换主预览视角（view_num） |
| EgoApp → 设备端 | prepare_multi_preview | 无 | 四路预览前准备（结束 warm / 启 standby） |
| 设备端 → EgoApp | heartbeat_ack | 无 | 心跳回复 |
| 设备端 → EgoApp | device_info_result | 有 | 设备状态、存储、预览 URL 等 |
| 设备端 → EgoApp | start_recording_result | 有 | 开始录制消息反馈 |
| 设备端 → EgoApp | stop_recording_result | 有 | 停止录制消息反馈 |
| 设备端 → EgoApp | view_changed_result | 有 | 切换视角消息反馈 |
| 设备端 → EgoApp | prepare_multi_preview_result | 有 | 四路预览准备反馈（phase / success / msg） |

---

## ③ §7.4 / §7.5 去重 — 合并为一节

**操作：**

1. **删除**整节 **`7.5 生命周期与冲突`**（与 7.4 重复）。
2. **替换** **`7.4 生命周期与冲突`** 全文为下方合并版（保留原 7.5 第 5 点关于四路同屏）。

**粘贴正文（§7.4 合并版）：**

```text
7.4 生命周期与冲突

1. OAK 相机与 8765 端口同一时刻只有一个生产者（standby 或 采集栈）。
2. 开始录制时会先停止 standby，再启动采集；8081 四路预览可能出现 1–3 秒断帧。
3. 预览 HTTP 为只读；多客户端（8080 / 8081 / App）同时 GET 不干扰写盘。
4. 采集中若 TCP 断开，录制在设备侧继续；重连后根据 device_info_result.state 恢复 UI，禁止在 recording / warming 时重复 start_recording。
5. 四路同屏：并行拉 4 路 http://{host}:8765/preview/{view_id}/jpg 之前，先发 TCP prepare_multi_preview；单路主预览切换仍用 change_view。
```

**说明：** 合并后 §7 结构为 7.1 URL 模板 → 7.2 待机 vs 采集 → 7.3 change_view 语义 → **7.4 生命周期与冲突**（结束，无 7.5）。

---

## ④ 可选小修（同一轮粘贴时可顺手改）

| 位置 | 改什么 |
|------|--------|
| §2 帧上限 | 「上限值待补充」→ **单帧 Body 上限 1 MiB**（`EGO_TCP_MAX_FRAME_BYTES`） |
| §5.5 切换视角 | 删除「App 等待 *_result 超时：change_view 建议 60s」——设备**立即**回复 `view_changed_result`；60s 超时仅适用于 start/stop |

---

## 粘贴后自检（30 秒）

- [ ] 标题已无「初版」字样  
- [ ] §6 上行含 `prepare_multi_preview_result`  
- [ ] §7 无重复的小节，仅一节「7.4 生命周期与冲突」，且含第 5 点四路同屏说明  
- [ ] 目录/侧边栏无孤立的「7.5」条目
