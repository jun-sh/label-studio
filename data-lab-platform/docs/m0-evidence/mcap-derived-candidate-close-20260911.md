# MCAP 派生候选 — 隔离重建收尾（2026-09-11）

**状态：** 本批次完成，停止。未部署 130、未重启生产、未采集、未更新发布指针。

---

## 新候选位置

`data-storage/ego-delivery/candidates/ego-001-mcap-derived-20260911`

- 分类：`mcap_full_derive`（四段原始 MCAP → deriveUnit → publish）
- 与格式修复候选 `ego-001-m1-20260911` 并列，未覆盖 corpus / ORD / 线上 ZIP

---

## 实际执行阶段

| 阶段 | 状态 | 说明 |
|------|------|------|
| video | **recalculated** | deriveUnit H.264 remux + warmup-trim |
| table | **recalculated** | publishUnitEpisode + sync-stream-parquet |
| imu | **recalculated** | ingest-raw + merge-station-imu |
| hands | **skipped** | 不在 MCAP derive 路径 |
| depth | **skipped** | 不在 MCAP derive 路径 |
| pose | **skipped** | 不在 MCAP derive 路径 |

---

## 四集输入 / 裁切 / 输出（非硬编码 1500）

| Ep | Session | MCAP 原始帧 | 裁切后 | 输出 | 差异原因 |
|----|---------|------------|--------|------|----------|
| 0 | sess_8aa298f… | 699 | 672 | 672 | H.264 align_skip=27 |
| 1 | sess_de08859… | 325 | 299 | 299 | H.264 align_skip=26 |
| 2 | sess_2e8da6f… | 202 | 176 | 176 | H.264 align_skip=26 |
| 3 | sess_48bcaf8… | 380 | 353 | 353 | H.264 align_skip=27 |
| **合计** | | **1606** | | **1500** | 裁切为 H.264 对齐，非固定帧数 |

---

## 验证结果

| 项 | 状态 | 报告 |
|----|------|------|
| 官方 loader 全量双后端（1500 帧） | **passed** | `loader-regression-mcap-derived-20260911.json` |
| 视频 A1（全解码/帧数/PTS） | **passed** | `mcap-derived-candidate-20260911.json` |
| 视频 A2（ep2/ep3 首中尾图像抽样） | **passed** | 同上 |
| ep0/ep1 设备时间缺失 | **如实** | parquet 列 absent→0 sentinel；MCAP 无字段 |
| ep2/ep3 设备时间字段 | **passed** | 176/176、353/353 present |
| IMU 对齐 | **unverified** | align_gate waived；字段保留 ≠ 对齐验证 |
| hands/depth/pose | **skipped** | — |

**交付布局说明：** unit 派生后 data 分片需合并为 `file-000.parquet`（与格式修复候选一致）方可使官方 loader 正确映射 per-episode 视频；ep0/ep1 的 `primary_device_timestamp_ns` 在 MCAP 中不存在，合并后以 0 填充以满足 loader DataLoader collate。

---

## 与格式修复候选差异

| | ego-001-m1-20260911 | ego-001-mcap-derived-20260911 |
|--|---------------------|-------------------------------|
| 输入 | corpus 复制 + timestamp 修补 | 四段原始 MCAP 全量 derive |
| 视频/表格/IMU | 复用 corpus | **重新计算** |
| ep2/ep3 设备时间 | 无 | **MCAP→parquet 完整保留** |
| 证明范围 | 格式可读性 | 完整 derive 链 + 分项报告 |

---

## 是否具备下一步隔离联调条件

**具备。** 候选已冻结（`meta/frozen_manifest.json` + `meta/candidate_manifest.json`），loader 全量通过，视频与字段分项验证完成。IMU 对齐仍为 unverified，不阻塞隔离 ingest replay，但不应声称 IMU 对齐已验证。

---

## 证据索引

- 构建：`mcap-derived-build-20260911.json`
- 验证汇总：`mcap-derived-candidate-20260911.json`
- Loader：`loader-regression-mcap-derived-20260911.json`
- 清单：`candidates/ego-001-mcap-derived-20260911/meta/candidate_manifest.json`
