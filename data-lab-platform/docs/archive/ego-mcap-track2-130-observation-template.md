# Track 2 VPU-H.264 — 130 功耗与存储观测记录表

> **用途：** POC Round 1 对比 Track 1 HW-JPEG 基线（`ego-mcap-pilot` / `sess_b4bc`）  
> **填写：** 每次 Track 2 录制会话一行；功耗/温升在录前、录中、录后各采一次  
> **分支：** `feat/ego-mcap-track2-vpuh264` · station `ego-mcap-track2`

---

## 0. Track 1 参考基线（固定对照，勿改）

| 项 | Track 1 基线值 | 来源 |
|----|---------------|------|
| 编码 | `OAK_HW_JPEG=1` `OAK_H264=0` | `z-mcap-pilot.conf` |
| 分辨率 | 1280×800 × 4 路 | 与 Track 2 对齐 |
| 样本 session | `sess_b4bc00f5eafd40b3b9f80e5c0ebf7e10` | 1042 帧 |
| derive_total_s (O1) | ~47 s | pilot 短段 |
| mux_encode_s (O2) | _(试点每日填写)_ | JPEG→libx264 |
| MCAP 体积 | _(试点段 `.mcap.zst` 填写)_ | — |
| 130 功耗 | _(待补；历史 POC §14.2 无 in-run 采样)_ | — |

---

## 1. 会话记录表（主表）

| 日期 | session_id | 段数 | 总帧数 | 录制时长_s | MCAP 原始_MB | MCAP.zst_MB | MB/千帧 | 4路均码率_kbps | persist_q 峰值 | persist_q 30s后 | ffprobe 4/4 parity | derive_total_s | MUX 阶段 | 备注 |
|------|------------|------|--------|-----------|-------------|-------------|---------|---------------|---------------|----------------|-------------------|---------------|----------|------|
| 2026-08-31 | `sess_5a86abed47324de78fd587302e3a1e98` | 1 | 1585 | 61.9 | 155.88 | _(待上传)_ | ~98.4 (原始) | ~5037 | 1 | _(待补)_ | ✅ 4/4 parity | **57.5** (P4b remux) | **MUX_REMUX** | Round3 @ `55affc1`；capture_fps 25.6；Foxglove schema ✅；视觉核验待人工；4×remux ~200ms；归一化 1042 帧 ≈37.8s vs T1 47s |
| YYYY-MM-DD | sess_… | 1 | | | | | | | | | ☐ | | remux / encode | |
| | | | | | | | | | | | | | | |
| | | | | | | | | | | | | | | |

**列说明：**

| 列 | 采集命令 / 方法 |
|----|----------------|
| MCAP 原始_MB | `stat -c%s segment.mcap` ÷ 1048576 |
| MCAP.zst_MB | 上传包体大小 |
| MB/千帧 | `MCAP.zst_MB × 1024 / 总帧数 × 1000` |
| 4路均码率_kbps | `(H.264 载荷总字节 × 8) / 录制时长_s / 4 / 1000`；或 ffprobe `bit_rate` 均值 |
| persist_q 峰值 | 录段日志 `persist_q=` 最大值 |
| persist_q 30s后 | 段关闭 +30s 再读 `persist_queue_depth` |
| ffprobe 4/4 parity | 各路 `ffprobe -count_frames` = rows；✅ = 4/4 零差 |
| derive_total_s | 34 `unit.json` → `derive.elapsed_ms` ÷ 1000 |
| MUX 阶段 | Track2 期望 **`MUX_REMUX` 或空**；若出现 `MUX_ENCODE` 则 ❌ |

---

## 2. 功耗与温升（每次录制附表）

| 日期 | session_id | 阶段 | 墙电功率_W | CPU_% | 封装温度_C | 采样方式 | 备注 |
|------|------------|------|-----------|-------|-----------|----------|------|
| | | 录前 idle 60s | | | | powertop / rapl / 外接表 | |
| | | 录中 steady | | | | 录制第 15–25s | |
| | | 录后 idle 60s | | | | 停录后 | |

**建议采样命令（130 GPD，无功率计时）：**

```bash
# CPU 占用（录中另开终端）
top -bn1 | head -5

# Intel RAPL 能耗（若可用，单位 µJ）
grep -r . /sys/class/powercap/intel-rapl/ 2>/dev/null | head

# 热区（摄氏度）
cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | awk '{print $1/1000}'
```

---

## 3. 本地存储可存时长估算

**磁盘配额（填写一次）：**

| 项 | 值 |
|----|-----|
| `EGO_SEGMENT_ROOT` 路径 | `/home/server/cache/ego-mcap-track2/segments` |
| 可用空间_GB | `df -BG …` |
| 保留余量_% | 建议 20% |

**估算公式（每行会话填入 MB/千帧 后自动算）：**

```text
可存千帧数 ≈ (可用空间_GB × 1024 × (1 - 保留余量)) / MB/千帧
可存小时数   ≈ 可存千帧数 / (平均帧率 × 3600 / 1000)
```

| 轨道 | MB/千帧（实测） | 30fps 可存小时（同盘） | 备注 |
|------|----------------|----------------------|------|
| Track 1 JPEG | _(pilot 段待填)_ | | `sess_b4bc` 1042 帧 |
| Track 2 H.264 | **~98.4** | _(同盘估算待补)_ | Round3 1585 帧 / 155.88 MB 原始 |
| 比值 Track2/Track1 | | | <1 表示 H.264 更省空间 |

---

## 4. POC Gate 快速勾选（每 session）

| Gate | 通过 | 证据 |
|------|------|------|
| G-T2-01 GOP / 4路 parity | ✅ 初过 | 1585×4 对齐；Foxglove schema 通过；视觉 GOP 待人工 |
| G-T2-02 persist_q | ✅ 初过 | 峰值 **1** / 30s后 _(待补)_ |
| G-T2-03 rollover copy-only | ⏳ 专项 | 留到 `EGO_SEGMENT_MAX_FRAMES` 触发测试 |
| G-T2-04 单路帧差 = 0 | ✅ | FL/FR/RL/RR: 1585 / 1585 / 1585 / 1585 |
| G-T2-05 parquet 无 gap | ✅ 初过 | P4b derive unit READY；G1/G3 pass |
| G-T2-07 derive ↓50%+ vs Track1 | ⏳ 部分 | 57.5s@1585帧；归一化1042帧≈37.8s（↓19% vs 47s）；MUX 阶段 ↓99% |

---

## 5. rollover 专项记录（触发 `EGO_SEGMENT_MAX_FRAMES` 时另填）

| 日期 | 前段 seg_id | 后段 seg_id | rollover 触发帧 | front_right trim | persist_q 曲线 | libx264 调用 |
|------|-------------|-------------|----------------|------------------|---------------|-------------|
| | seg_000000 | seg_000001 | | ☐无 ☐失败 | | ☐无 ☐有 |

---

*模板版本：v0.1 · 配套 `ego-mcap-track2-p4b-poc-checklist.md`*
