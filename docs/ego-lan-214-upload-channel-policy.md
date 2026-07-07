# ego-lan-214 上传通道策略（商用可靠性）

**生效**：阶段一 W2 起  
**原则**：214 Agent 批量主通道 · 浏览器单段应急补传 · 批量验收不走浏览器

---

## 通道定位

| 通道 | 角色 | 批量生产 | 商用可靠性 |
|------|------|----------|------------|
| **214 `ecs-oak-upload-stack`** | **唯一批量主通道** | ✅ | ✅ |
| **214 CLI `upload_segments`** | Agent 同等协议 | ✅ | ✅ |
| **34 采集页「导入」** | **单段应急补传** | ❌ 禁止 | ⚠️ 仅 1 段偶发 |

---

## 214 Agent 批量上传（标准操作）

```bash
# 214，有网后
systemctl --user start ecs-oak-upload-stack.target
journalctl --user -u ecs-upload-segments-loop -f   # pending=0 后
systemctl --user stop ecs-oak-upload-stack.target
```

- 协议：`POST /lerobot/api/collection/stations/ego-lan-214/upload`（直打 stream-ingest）
- 头：`X-Session-Id`、`X-Segment-Id`、`X-Content-Sha256`、`X-Station-Token`
- **214 采集、导出、Agent 逻辑零改造**

---

## 浏览器「导入」（降级维护）

- **用途**：U 盘单段补传、实验室偶发 1 段
- **限制**：无断点续传、Django 整包代理、最多 2 路并发、**不具备批量商用可靠性**
- **维护策略**：仅保留基础可用；**不再投入**批量体验优化、批量 bug 修复
- **出现问题**：优先引导切换 214 Agent；运维侧用 `derive-retry` / segments API

`DERIVE_ASYNC=1` 时弹窗成功文案为 **「已上传，后台处理中」**（非「可回放」）。

---

## 验收与性能基准

| 场景 | 通道 |
|------|------|
| W2 灰度 21 段 | **仅 214 Agent** |
| `CHECK_DERIVE_ASYNC=1` 全量验收 | **仅 Agent 上传后** |
| 单段补传冒烟 | 浏览器（可选，非出口） |

---

## 红线

1. 不改动 214 采集、导出、Agent **核心上传协议**  
2. 不改动 LeRobot v3 数据格式  
3. `tar.zst` 为唯一可信源  
4. `DERIVE_ASYNC` 可灰度、可回退  

---

## 商用可观测（P0/P1）

| 阶段 | 交付 | 文档 |
|------|------|------|
| **P0** | `upload-status.json`、`ego-upload-status` CLI、人话日志 | [操作员手册](./ego-lan-214-agent-upload-operator-guide.md) |
| **P1** | 34 提前判重、214 Web 上传面板 | [商用 PRD](./ego-lan-214-agent-upload-commercial-prd.md) |
| **P2** | 34 采集页 segments 端到端视图 | PRD §6 |

操作员查看进度：

```bash
systemctl --user start ecs-oak-upload-stack.target
ego-upload-status    # 或 python -m ego_capture_studio.cli.upload_status
```

---

## 相关文档

- [ego-lan-214-segment-storage-and-upload.md](./ego-lan-214-segment-storage-and-upload.md)
- [ego-lan-214-p1-phase1-implementation-plan.md](./ego-lan-214-p1-phase1-implementation-plan.md)
- [ego-edge-offline-upload-and-deployment.md](./ego-edge-offline-upload-and-deployment.md)
