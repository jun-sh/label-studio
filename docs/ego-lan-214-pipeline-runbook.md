# ego-lan-214 交付手册（一页）

**原则：** 一条命令交付 · 无内参/未去畸变禁止发布 Viewer · 断点可续跑

---

## 录完后（34 平台）

```bash
ego-deliver ego-lan-214
```

自动完成：214 上传 → 派生 → 畸变校正 / 手部骨架 / 深度 → 部署 Viewer。

**验收：** <http://10.10.10.34:8080/data/ego_214_hand_pose>

---

## 重跑数据（保留 214 源段）

```bash
ego-deliver ego-lan-214 --reset-34
```

仅清 34（stream / pipeline / corpus / samples），**不删 214**，然后从 214 重新上传并走完整流程。

---

## 整轮从零（214 + 34 全清，慎用）

```bash
ego-deliver ego-lan-214 --reset-full
```

---

## 排障

| 命令 | 用途 |
|------|------|
| `ego-pipeline-sessions.py doctor ego-lan-214` | 检查陈旧 `.status` marker |
| `ego-pipeline-sessions.py reconcile-markers ego-lan-214` | 清理无效 marker |
| `ego-viewer-publish-gate.sh ego_214_hand_pose` | 手动跑发布门禁 |

**门禁规则（失败则 Viewer 不更新）：**

- `stream/meta/camera_intrinsics.json` 必须存在且有效  
- 每个 session 的 `rectify` 不得 `skipped`  
- 原有 B7（四路视频 / zip / 深度）检查  

---

## 内部命令（运维）

- `ego-run-pipeline ego-lan-214` — 仅后处理 + 部署（假设已上传并派生完成）  
- `ego-export`（214）— 离线导出 tar.zst  

日常请只用 **`ego-deliver`**。
