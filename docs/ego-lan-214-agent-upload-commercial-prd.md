# ego-lan-214 Agent 上传商用体验 PRD

**版本**：P0/P1 · 2026-07-03  
**范围**：214 `ecs-upload-loop` 可观测性升级 · **不改动** tar.zst 协议 / 采集栈 / LeRobot 格式  
**主通道**：[ego-lan-214-upload-channel-policy.md](./ego-lan-214-upload-channel-policy.md)

---

## 1. 背景

底层上传栈（段级续传、判重、隔离、重试）已可靠，但仅输出 engineer 向 journalctl，**不满足商用交付**。本 PRD 定义最小改动、最高 ROI 的体验补齐路线。

---

## 2. 用户故事

| 角色 | 故事 | 验收 |
|------|------|------|
| 现场操作员 | 我想一眼看到传了几段、还要多久 | `ego-upload-status` 10 秒内读懂 |
| 数据员 | 我想知道能不能关 214 走人了 | 待传=0 即可走；回放另查 34 |
| 客户 | 断网后要不要重头传 | FAQ + 实测 restart 后续传 |
| 运维 | 重复跑 upload 会不会浪费带宽 | duplicate 跳过 + P1 零 body |

---

## 3. 能力基线（对外口径）

### 3.1 已具备（不变）

- 段级断点续传（`uploaded=false` 段自动续跑）
- `(sessionId, segmentId)` 判重
- `missing_manifest` 等异常段隔离
- 指数退避重试（默认 3 次）

### 3.2 本 PRD 补齐

| ID | 能力 | 阶段 |
|----|------|------|
| O1 | `upload-status.json` 结构化状态 | **P0** ✅ |
| O2 | `ego-upload-status` / `cli.upload_status` | **P0** ✅ |
| O3 | 日志人话化 | **P0** ✅ |
| O4 | 操作员手册 + FAQ | **P0** ✅ |
| O5 | 34 tar.zst 提前判重（不读 body） | **P1** ✅ |
| O6 | 214 Web 上传面板 | P1 待做 |
| O7 | 34 采集页端到端 segments 视图 | P2 |
| O8 | 字节级断点续传 | **不做**（P3 搁置） |

---

## 4. P0 详细需求

### 4.1 `upload-status.json`

**路径**：`$XDG_RUNTIME_DIR/ego-upload-status.json`（可 `EGO_UPLOAD_STATUS_PATH` 覆盖）

**字段**：

```json
{
  "version": 1,
  "updatedAt": "ISO-8601",
  "service": { "running": true, "phase": "idle|uploading|no_session" },
  "sessionId": "sess_…",
  "progress": {
    "total": 11,
    "completed": 3,
    "pending": 7,
    "skipped": 1,
    "failed": 0,
    "percent": 27
  },
  "current": { "segmentId": "seg_…", "bytes": 78000000, "startedAt": "…" },
  "throughputBytesPerSec": 1250000,
  "etaSeconds": 360,
  "skippedSegments": [{ "segmentId": "…", "reason": "…", "reasonLabel": "…" }],
  "recentEvents": [],
  "lastOk": {},
  "lastError": {}
}
```

**刷新**：upload loop 每轮 + 每段 ok/fail/skip。

### 4.2 CLI

```bash
ego-upload-status          # 人话
ego-upload-status --json   # 机器可读
ego-upload-status --lang en
```

**P0 验收**：

- [ ] 上传进行中执行 CLI，显示进度/速度/ETA 至少一项
- [ ] `missing_manifest` 出现在「跳过」行
- [ ] 服务未启动时 exit 1 + 明确提示

### 4.3 日志人话化

| 旧 | 新 |
|----|-----|
| `pending=11 idle` | `正在上传：已完成 0/11 段，剩余 11 段` |
| `segment_ok` | `第 seg_xxx 段上传完成` |
| `duplicate=True` | `第 seg_xxx 段已存在，自动跳过` |

---

## 5. P1 需求

### 5.1 34 提前判重

**触发**：`POST .../upload` 收到 `X-Session-Id` + `X-Segment-Id` 后、**读 body 前**

**条件**：

- 热层已 commit；或
- `DERIVE_ASYNC=1` 且 Raw 已 `verified|deriving|derived`

**响应**：`duplicate: true`，`framesCommitted: 0`，不调用 `streamRequestToFile`

**验收**：对已传段再 POST，34 侧无大文件落盘（查 incoming 目录无新文件 / 网络抓包 body 极小）

### 5.2 214 Web 上传面板（文案原型）

**位置**：214 `8080` 控制页新增卡片「数据上传」

| 元素 | 文案/行为 |
|------|-----------|
| 标题 | 上传到数据平台 |
| 主进度 | `{completed}/{total} 段` 进度条 |
| 副文案 | `{speed} Mb/s · 约 {eta} 分钟` |
| 状态徽章 | 传输中 / 已完成 / 等待会话 |
| 异常列表 | 红色：`seg_033 已跳过：数据不完整` |
| 主按钮 | 开始上传 → `start ecs-oak-upload-stack` |
| 次按钮 | 暂停上传 → `stop` |
| 脚注 | 上传完成≠可回放；请在 34 采集页确认派生状态 |

**数据源**：轮询 `upload-status.json` 或内部 API 封装（1s 间隔）

---

## 6. P2 需求（二期）

- 34 采集页 Tab「同步状态」：`GET .../segments` 五态简化
- 验收门禁：**全部 `derived`** 才算 W2 出口
- 告警：pending>0 超 2h、连续 `segment_fail`≥3

---

## 7. 非目标

- 不改 214 采集 / export / tar.zst 包结构
- 不做浏览器批量上传体验
- 不做字节级 tus 续传（P3 默认搁置）
- 不合并浏览器与 Agent 状态源（P2 再统一）

---

## 8. 测试计划

| 用例 | 步骤 | 期望 |
|------|------|------|
| T1 进度可见 | start upload → `ego-upload-status` | 有 N/M、状态 |
| T2 续传 | 传 3 段后 stop → start | 从第 4 段继续 |
| T3 判重 | 全量传完再 start | duplicate 跳过；P1 后低带宽 |
| T4 隔离 | 人为删某段 manifest | skip 列表出现；其他段继续 |
| T5 人话日志 | journalctl -f | 无 `pending=11 idle` 裸日志 |

---

## 9. 发布清单

**214**：

1. 同步 `upload_status.py`、`segment_upload.py`、`upload_segments_loop.py`、`cli/upload_status.py`
2. 安装 `ego-upload-status` 到 PATH（可选）
3. `systemctl --user restart ecs-upload-segments-loop`

**34**：

1. 部署 `stream-ingest.mjs` 提前判重
2. `docker-compose … restart stream-ingest`

---

## 10. 文档索引

- 操作员手册：[ego-lan-214-agent-upload-operator-guide.md](./ego-lan-214-agent-upload-operator-guide.md)
- 通道策略：[ego-lan-214-upload-channel-policy.md](./ego-lan-214-upload-channel-policy.md)
