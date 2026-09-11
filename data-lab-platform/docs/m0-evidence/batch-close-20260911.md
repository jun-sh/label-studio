# Ego-001 M1 批次结案（2026-09-11，冻结版）

**冻结：** 本报告与关联证据版本为本批最终表述；代码与本批报告不再扩展。未重跑未变化「格式修复候选」验证；不覆盖线上 corpus / ORD / 发布指针。

完成标准：**必要软件修复及其可获得的证据已闭合**（非 30fps / 6DoF / 手部三维商业验收）。

---

## A. 已通过（报告 + 数据版本）

| 项目 | 报告 | 数据/版本 |
|------|------|-----------|
| **格式修复候选 loader** | `loader-regression-candidate-full.json` | `ego-001-m1-20260911`，1500 帧；LeRobot `e40b58a8` / 0.6.1；**未重跑** |
| **证据范围** | `candidate-evidence-scope-20260911.json` | 分类：**格式修复候选**（corpus 时间戳修补，非 MCAP 全量重派生） |
| **H.264 materialize 可解码性** | `h264-e2e-ego-001-ep2.json` | ep2 MCAP → materialize 中间流；四路解码帧数与 rows 一致（**非**逐帧图像核验） |
| **子集导出独立性** | `export-subset-verify-ego-001.json` | 候选 ep2 子集 |
| **上传封存（单元级）** | `ingest/ingest.test.mjs` | seal 门禁逻辑 + 单测 |

### H.264 生产派生输出（`h264-derive-output-ego-001.json`）— 三项分开记录

以下为离线 `deriveUnit` 在隔离目录的**抽样/计数**验证，**不等于** ep2/ep3 每一帧内容均已人工或自动全量比对。

| 验证子项 | ep2 | ep3 | 说明 |
|----------|-----|-----|------|
| **A1. 全视频解码与帧数** | 四路 MP4 各 176 帧；PTS 自 0.0s 起；warmup 裁切后无额外前导帧 | 四路各 353 帧；同上 | ffprobe 计数 + 全片解码帧数；**未**逐帧内容审计 |
| **A2. 首/中/尾图像对应抽样** | 每路 3 点（0 / 87 / 175）与 MCAP 内容帧 RGB 比对，MAE&lt;8 | 每路 3 点（0 / 50 / 100），MAE&lt;8 | **共 12 点/集**；非全帧覆盖 |
| **A3. 官方 loader 抽样** | head_mid_tail + PyAV/TorchCodec | 同左 | LeRobot 0.6.1 官方取样；**非** 1500 帧全量 |

**本批代码修复（已冻结，不再扩展）：**
- `mcap-materialize.py`：decode warmup 参考包（中间流可解码）
- `unit.mjs` + `mux-exec.mjs`：remux 后按 `decode_warmup_packets` 裁掉 MP4 前导帧
- `sync-stream-parquet.py`：`primary_device_timestamp_ns` / `timestamp_ns` 写入 parquet 的丢失点修复

---

## B. 已修但未验证（缺证据）

| 项目 | 现状 | 缺什么 |
|------|------|--------|
| **IMU 字段保留** | MCAP ep2/ep3 含 `primary_device_timestamp_ns`；materialize 与修复后 sync **可**写入 parquet | 存量 stream jsonl / corpus **未回填**；ep0/ep1 MCAP **无**该字段（0/699、0/325） |
| **IMU 时间对齐** | `imu_align_verified=false`；门禁 **unverified** | **字段保留 ≠ 相机—IMU 对齐已验收**；未证明最近邻对齐达商业容差（`imu-clock-audit-ego-001.json`） |
| **格式修复候选** | 仅 timestamp 修补 | 不能代表完整 derive 链 |
| **上传封存** | 服务端 seal 单测通过 | 见下文 D「隔离服务重放」 |

---

## C. 未修（影响）

| 项目 | 影响 |
|------|------|
| 线上 corpus `ego_001` | ep2/3 全局 timestamp 缺陷仍在 |
| ORD / 线上 ZIP / 发布指针 | 未覆盖、未更新 |
| 严格 30fps / 6DoF / 手部三维 | 本批范围外 |

---

## D. 后续事项（分类列示，互不混用）

### D1. 存量 MCAP 重派生（不需新采集）

用本批冻结代码，在**隔离目录**从已有四段 `.mcap.zst` 生成新的「完整派生候选」，与「格式修复候选」区分。  
具体步骤见 **`plan-mcap-rederive-candidate-20260911.md`**（方案待确认，本批不执行）。

### D2. 隔离服务重放（不需新采集）

在**非生产** stream-ingest 实例或本地进程中，用已有段文件重放上传 + seal，验证 `DONE_UPLOAD` 门禁。  
**不要求** 130 部署或新采集；**会**触及 ingest 进程，但应使用隔离数据目录，不读写生产 `data-storage/stream/ego-001` 热路径。

### D3. 发布指针 / ORD 更新（不需新采集）

更新交付 ZIP、ORD manifest、发布指针等运维操作；输入可为已验收候选包。  
**与是否新采集无关**；本批未执行。

### D4. 新采集（仅当要提高数据标准）

若要求 **四集均含** `primary_device_timestamp_ns` 或严格 30fps/6DoF 商业验收，需新采集或接受 ep0/ep1 无设备时钟。  
**ep2/ep3 的 IMU 字段与对齐验证不依赖此项。**

---

## 验证分层（冻结表述）

| 层级 | 状态 | 备注 |
|------|------|------|
| 格式修复候选 loader | **已通过** | 1500 帧全取样；未重跑 |
| MCAP→materialize H.264 | **已通过** | ep2；解码帧数级 |
| deriveUnit→MP4 帧数/PTS | **已通过** | ep2 全路计数 + ep3 计数 |
| deriveUnit→图像对应 | **抽样通过** | ep2 12 点 + ep3 12 点 |
| deriveUnit→loader | **抽样通过** | head_mid_tail |
| IMU 字段传递（代码路径） | **已修复** | 存量产物未回填 |
| IMU 相机—时钟对齐 | **unverified** | 门禁保持 |

---

## 边界

未部署 130、未重启线上服务、未启动采集、未更新正式发布指针；未全库重建、未覆盖线上数据。

**下一步：** 仅 `plan-mcap-rederive-candidate-20260911.md` 待确认后执行；本批不再改代码。
