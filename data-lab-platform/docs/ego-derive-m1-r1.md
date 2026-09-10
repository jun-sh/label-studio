# ego-001 Derive M1+R1 — One-shot Merge & Progress Heartbeat

**版本：** deploy-release 首包核心迭代  
**目标：** 消除大 session MP4 pairwise merge 瓶颈；derive 全程可观测、可超时、不永久堵队列。

---

## 变更摘要

| 代号 | 内容 | 默认 |
|------|------|------|
| **M1** | chunk MP4 **单次 ffconcat** 合并（替代 O(n) pairwise） | `DERIVE_MUX_MERGE_MODE=oneshot` |
| **R1** | `live/derive/progress/{sessionId}.json` 阶段进度 + heartbeat 超时 + ffmpeg 超时 | 见下表 |

---

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `DERIVE_MUX_MERGE_MODE` | `oneshot` | `oneshot` \| `pairwise`（回滚旧逻辑） |
| `DERIVE_HEARTBEAT_ENABLED` | `1` | `0` 关闭 worker 看门狗 |
| `DERIVE_HEARTBEAT_TIMEOUT_S` | `900` | progress 无更新则 abort session |
| `DERIVE_FFMPEG_TIMEOUT_S` | `3600` | 单次 legacy spawn ffmpeg 上限 |
| `DERIVE_WORKER_ID` | `derive-worker-1` | 写入 progress，便于多 worker 期识别 |

P0 参数（`DERIVE_CPU_*`、`DERIVE_MUX_CHUNK_FRAMES`）保持不变。

---

## 部署

```bash
cd /path/to/data-lab
docker build -t data-lab-lerobot-studio:v0.1.3 data-lab-platform/lerobot-studio

docker-compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  -f data-lab-platform/docker-compose.v0.1.3.yml \
  -f data-lab-platform/docker-compose.v0.1.3-async.yml \
  up -d --force-recreate derive-worker stream-ingest
```

验证：

```bash
docker exec data-lab-derive-worker-1 printenv DERIVE_MUX_MERGE_MODE DERIVE_HEARTBEAT_TIMEOUT_S
curl -s http://127.0.0.1:8080/lerobot/api/collection/stations/ego-001/derive-status | python3 -m json.tool
# 关注 progress.unitProgress.{phase,done,total,updatedAt}
```

---

## S0 应急（已完成模板）

**场景：** 单条巨 session hang 占锁，队列全堵。

```bash
STATION_ROOT="/path/to/data-lab/data-storage/stream/ego-001"
SID="sess_6e852e2c117c4bc2930b9171ffda5fac"

# 1) 释放锁 + 清除 DERIVING
rm -f "$STATION_ROOT/state/deriver.lock"
rm -f "$STATION_ROOT/state/sessions/$SID/session.DERIVING"

# 2) 删除 hung tmp unit（可选，重跑会重建）
rm -rf "$STATION_ROOT/derived/_tmp."*

# 3) 临时 defer 巨 session（避免 M1 前再次堵队列）
python3 - <<'PY'
import json
from pathlib import Path
from datetime import datetime, timezone
root = Path("'"$STATION_ROOT"'")
sid = "'"$SID"'"
p = root / "state/sessions" / sid / "session.FAILED"
p.write_text(json.dumps({
  "sessionId": sid, "marker": "session.FAILED",
  "at": datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
  "phase": "FAILED",
  "reason": {"code": "STALE_DERIVE_DEFERRED", "message": "deferred until M1+R1", "category": "ops"},
}, indent=2) + "\n")
PY

# 4) 重启 worker
docker-compose ... restart derive-worker
```

**M1+R1 上线后重跑 deferred session：**

```bash
rm -f "$STATION_ROOT/state/sessions/$SID/session.FAILED"
ego-derive run --station ego-001 --session-id "$SID"
# 或等待 worker 自动消费 DONE_UPLOAD
```

---

## 观测

| 位置 | 内容 |
|------|------|
| `data-storage/stream/ego-001/live/derive/progress/sess_*.json` | 实时阶段 |
| `manifest/journal.jsonl` | `derive_started` / `unit_ready` / `derive_failed` |
| API | `GET .../derive-status` → `progress.unitProgress` |
| 日志 | `docker logs data-lab-derive-worker-1 \| grep derive_watch` |

**progress.phase：** `EXTRACT` → `TABLE` → `MUX_ENCODE` → `MUX_MERGE` → `READY`

---

## SLA 自测清单

- [ ] 小 session（<500 帧）：wall time **< 90s**，`unit_ready` journal 存在
- [ ] 大 session（>10k 帧）：merge 阶段 **< 15 min/4 相机**（M1 前后对比）
- [ ] derive-status 在 DERIVING 时 **`unitProgress.updatedAt` < 30s 前**
- [ ] 模拟 stall：`DERIVE_HEARTBEAT_TIMEOUT_S=120` + 停 progress → session **FAILED**，下一条 pending 开跑
- [ ] 回滚：`DERIVE_MUX_MERGE_MODE=pairwise` recreate worker，行为恢复旧 merge

---

## 回滚

```bash
# compose / env
DERIVE_MUX_MERGE_MODE=pairwise
DERIVE_HEARTBEAT_ENABLED=0

docker-compose ... up -d --force-recreate derive-worker
```

---

## 后续（本 PR 不含）

- **Q1：** 多 worker session claim + scale
- **P1：** 段 extract 并行 + 130 `MAX_SEGMENTS_PER_SESSION`
