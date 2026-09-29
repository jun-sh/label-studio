# 腾讯文档最简修改稿 — `prepare_multi_preview`（2026-09-23）

> 在线文档：[Ego设备与App通讯接口文档](https://docs.qq.com/aio/DUnpWeXdIa2NGRWZp?p=tqgnPGuz0dX1AjnOT3CaVz)  
> 仓库同步：`ego-app-tcp-protocol.md` §4.6 / §5.6

---

## 改哪里（3 处，约 5 分钟）

### ① §4 App→设备：在 `change_view` 后面加一小节

**标题：** `4.6 prepare_multi_preview`（若原文无 4.6 编号，紧接 4.5 插入即可）

**粘贴正文：**

> App **进入四路同屏、并行拉 4 路 HTTP 预览前**调用（与 `change_view` 无关）。  
> 设备结束热待机并启动 standby 四路；立即回复，后台约 10–15 秒就绪。  
> 下一段录制变为冷启动（约 10–15 秒）。
>
> 下行：
> ```json
> { "type": "prepare_multi_preview" }
> ```
>
> 上行见 §5.6 `prepare_multi_preview_result`。  
> **四路推荐流程：** `device_info`（idle）→ `prepare_multi_preview` → 并行 GET 四路 `http://{host}:8765/preview/{view_id}/jpg`；`phase=ending_warm_idle` 时轮询重试 10–15s。

### ② §5 设备→App：在 `view_changed_result` 后加回复示例

**标题：** `5.6 prepare_multi_preview_result`

```json
{
  "type": "prepare_multi_preview_result",
  "data": {
    "success": true,
    "phase": "ending_warm_idle",
    "msg": "正在结束热待机并启动四路预览，约需 10–15 秒"
  }
}
```

`phase`：`ready` | `ending_warm_idle` | `blocked_capture` | `blocked_busy`

### ③ 消息速查表（§4 末尾或文首表格）加一行

| App→设备 | prepare_multi_preview | 无 | 四路预览前准备 |

---

## §7 视频预览（若有）补一句

在「App 四路同屏」或「待机 vs 采集」段末尾加：

> **四路同屏：** 并行拉 4 路 `:8765` URL 之前先发 TCP `prepare_multi_preview`；单路切换仍用 `change_view`。

---

## Unity 同事代码改动（1 处）

进入四路界面时、开始拉 4 个 HTTP 之前：

```json
{ "type": "prepare_multi_preview" }
```

收到 `prepare_multi_preview_result` 后：

- `phase == "ready"` → 立即并行拉四路
- `phase == "ending_warm_idle"` → 等 10–15 秒后轮询四路 JPEG
- `success == false` → 提示用户（采集中/设备忙）

`change_view` **保留原用法**（单路主预览）；**不要**用 `change_view` 代替本接口做四路准备。
