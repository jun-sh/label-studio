# SQL 练习：机器人数据集时序分析（20 题）

数据全部来自本项目 `data-storage` 下的真实 LeRobot v3 数据集，导入一个独立的练习库，不碰平台库（`datalab-db-1` 是 Label Studio 的库）。

## 启动

```bash
docker run -d --name sql-practice-pg -e POSTGRES_USER=practice -e POSTGRES_PASSWORD=practice \
  -e POSTGRES_DB=robodata -p 127.0.0.1:55432:5432 -v sql-practice-pgdata:/var/lib/postgresql/data postgres:16-alpine
python3 load.py                     # ~20s, rebuilds schema.sql and reloads everything
docker exec -it sql-practice-pg psql -U practice -d robodata     # 交互练习
./run_answers.sh 13                 # 看第 13 题参考答案的输出；不带参数跑全部
```

（也可以用 `docker-compose -p sql-practice up -d`，配置在 `docker-compose.yml`。）

## 表结构（`schema.sql`）

| 表 | 行数 | 说明 |
|---|---|---|
| `dataset` | 3 | 1 = `681448_v20261124`（franka，5 fps），2 = `681453_v20261125`（franka，5 fps），3 = `ego-002`（ego 头戴，30 fps） |
| `task` | 249 | `(dataset_id, task_index)` → 任务的自然语言描述 |
| `episode` | 700 | 每段录制：`length`（帧数）、`task_index`、`station_id`、`annotation_status` |
| `frame` | 204,066 | 每帧：`ts`（秒）、`state real[]`、`action real[]`；ego 还有 `imu_accel real[3]`（m/s²） |

约定：franka 的 `state[1:3]` 当作末端执行器 xyz（米），`action[8]` 当作夹爪指令（正负号代表开或合）。PostgreSQL 数组下标从 1 开始。

---

## 一、基础查询与聚合（1–5）

**Q1 数据集概览。** 列出每个数据集的 episode 数、总帧数和总时长（分钟）。
> 考点：`JOIN … USING`、`GROUP BY`、整数除法要先转 `numeric`。

**Q2 最长的 episode。** 数据集 `681448_v20261124` 中最长的 5 个 episode，给出时长（秒）和任务文本。
> 考点：`LEFT JOIN`、`ORDER BY … LIMIT`。

**Q3 任务覆盖度。** 每个数据集用到了多少个任务、只有 1 个 episode 的任务有几个、单个任务最多有几个 episode。
> 考点：CTE（`WITH`）、`count(*) FILTER (WHERE …)`。思考：两个交付批次的“任务多样性”差别有多大？

**Q4 长度直方图。** 两个 franka 数据集的 episode 长度，按 50 帧分桶计数。
> 考点：分桶表达式（`length / 50 * 50` 或 `width_bucket`）。

**Q5 一致性校验。** 找出 `episode.length` 与 `frame` 实际行数不一致、或 `frame_index` 不从 0 开始、或不连续的 episode。
> 考点：子查询聚合 + `LEFT JOIN` + `IS NULL`。**返回 0 行就是正确结果**，入库前的数据校验就是这样写的。

## 二、过滤、分位数与集合（6–10）

**Q6 长度异常。** 每个数据集用 P5 和 P95 作为阈值，统计过短和过长的 episode 数。
> 考点：`percentile_cont(…) WITHIN GROUP (ORDER BY …)`。

**Q7 按秒聚合传感器。** ego-002 的 episode 1：按秒统计 IMU 加速度模长 `sqrt(x²+y²+z²)` 的 avg/min/max（只看前 10 秒）。
> 考点：数组下标、`floor(ts)` 做时间分桶。静止时模长约 9.8，也就是重力加速度。

**Q8 任务稳定性。** 数据集 `681448` 中，episode 数 ≥10 且长度变异系数（CV = sd / avg）> 0.12 的任务。
> 考点：`HAVING` 里使用聚合函数、`stddev_samp`。

**Q9 重复 episode 检测。** 对每个 franka episode 的全部 `state` 和 `action` 生成指纹，找出内容完全相同的 episode。
> 考点：`string_agg(… ORDER BY …)` + `md5`，`GROUP BY … HAVING count(*) > 1`。
> ⚠ 这题在真实数据里**有结果**：`681453_v20261125` 有 36 对重复（例如 episode 0 和 74），这是一个真实的交付质量问题。

**Q10 夹爪开合次数。** 统计每个 franka episode 中 `action[8]` 的符号翻转次数（忽略 0），按数据集给出 P50、P90、最大值，以及从未翻转的 episode 数。
> 考点：`lag()`、`percentile_disc`。

## 三、窗口函数（11–16）

**Q11 滑动平均。** ego-002 episode 1，对 IMU 模长求 1 秒（30 帧）的滑动平均，输出第 100–109 帧。
> 考点：`avg() OVER (ORDER BY … ROWS BETWEEN 29 PRECEDING AND CURRENT ROW)`。追问：`ROWS` 和 `RANGE` 有什么区别？

**Q12 末端速度。** franka 每帧的末端速度 = 相邻帧 xyz 的位移 / dt。找出最大速度 Top 10 的 episode。
> 考点：`WINDOW w AS (PARTITION BY … ORDER BY …)` 复用窗口定义。

**Q13 z-score 异常检测。** ego-002 每个 episode 内，IMU 模长 |z| > 4 的帧数及占比，取 Top 10。
> 考点：`avg() OVER p` 和 `stddev_samp() OVER p`（窗口聚合，行数不减少）、`nullif` 防止除零。
> **面试话术就从这题来：“我用 SQL 对设备时序数据做过异常检测的特征提取。”**

**Q14 静止段检测（gaps and islands）。** franka 中帧间位移 < 1 mm、且连续 ≥ 5 帧（1 秒）的静止段，标出它在 episode 的开头、中间还是结尾。
> 考点：`frame_index - row_number()` 分组法，这是经典面试题。

**Q15 排名。** 每个数据集内按长度做 `rank()`、`ntile(4)` 和 `percent_rank()`，取每个数据集的前 3 名。
> 考点：并列时 `rank` 和 `row_number` 的区别（数据集 2 就有并列）。

**Q16 帕累托。** 每个数据集中，最长的多少个 episode 加起来占全部帧数的 50%？
> 考点：累计 `sum() OVER (ORDER BY …)` 和全量 `sum() OVER ()` 的组合。

## 四、综合与工程（17–20）

**Q17 特征表。** 为每个 franka episode 生成一行特征：时长、路径长度、最大速度、静止占比、夹爪翻转次数，存成 `MATERIALIZED VIEW episode_features`，并加唯一索引。
> 考点：多个 CTE 串联、物化视图（可以接着思考 `REFRESH … CONCURRENTLY` 为什么需要唯一索引）。

**Q18 QC 规则引擎。** 基于 Q17 给每个 episode 打失败原因标签（`too_short` < 5 s、`speed_spike` > 0.25 m/s、`mostly_static` > 30%、`no_grasp`），统计每个数据集的合格率和各原因的数量。
> 考点：`array_remove(ARRAY[CASE …], NULL)`、`= ANY(array)`。这就是平台 QC 的 SQL 版本。

**Q19 执行计划与索引。** 分别 `EXPLAIN ANALYZE` 两个查询：`WHERE episode_index = 5` 和 `WHERE dataset_id = 3 AND episode_index = 5`。然后在 `episode_index` 上建索引，再对比一次。
> 考点：复合主键的**最左前缀**。不带 `dataset_id` 时只能扫整个索引（实测约 4.4 ms），单独建索引后约 0.36 ms。

**Q20 生成 stats.json。** 把数据集 1 的 `action` 数组展开成长表，按维度算 min/max/mean/std，再拼成 LeRobot `stats.json` 的 JSON 格式。
> 考点：`unnest … WITH ORDINALITY`、`jsonb_build_object`、`jsonb_agg(… ORDER BY …)`。

---

参考答案在 `answers.sql`，全部已在真实数据上跑通。建议先自己写，再用 `./run_answers.sh N` 对照结果。

## 加分：平台库（Label Studio）只读练习

`datalab-db-1` 中的 `project` / `task` / `task_completion` / `htx_user` 目前数据很少（4 个 project、5 个 task），适合练 **jsonb**：
`task.data->>'video'`、`jsonb_array_elements(task_completion.result)`，以及按标注员统计 `lead_time`。只做 SELECT，不要写入。
