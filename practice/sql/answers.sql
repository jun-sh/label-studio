-- Reference answers. Each block starts with "-- Q<n>"; run_answers.sh executes them one by one.
-- Dataset ids: 1 = 681448_v20261124 (franka, 5 fps), 2 = 681453_v20261125 (franka, 5 fps), 3 = ego-002 (ego, 30 fps)

-- Q1
SELECT d.name, d.robot_type, count(*) AS episodes, sum(e.length) AS frames,
       round(sum(e.length)::numeric / d.fps / 60, 1) AS minutes
FROM episode e JOIN dataset d USING (dataset_id)
GROUP BY d.name, d.robot_type, d.fps
ORDER BY d.name;

-- Q2
SELECT e.episode_index, e.length, round(e.length::numeric / d.fps, 1) AS seconds, t.task_text
FROM episode e
JOIN dataset d USING (dataset_id)
LEFT JOIN task t USING (dataset_id, task_index)
WHERE d.name = '681448_v20261124'
ORDER BY e.length DESC
LIMIT 5;

-- Q3
WITH c AS (
  SELECT dataset_id, task_index, count(*) AS n FROM episode GROUP BY 1, 2
)
SELECT d.name, count(*) AS tasks_used, count(*) FILTER (WHERE n = 1) AS single_episode_tasks,
       max(n) AS max_episodes_per_task, round(avg(n), 1) AS avg_episodes_per_task
FROM c JOIN dataset d USING (dataset_id)
GROUP BY d.name ORDER BY d.name;

-- Q4
SELECT d.name, (e.length / 50) * 50 AS bucket_start, count(*) AS episodes
FROM episode e JOIN dataset d USING (dataset_id)
WHERE d.robot_type = 'franka_panda'
GROUP BY 1, 2 ORDER BY 1, 2;

-- Q5
SELECT e.dataset_id, e.episode_index, e.length, f.n, f.mn, f.mx
FROM episode e
LEFT JOIN (
  SELECT dataset_id, episode_index, count(*) AS n, min(frame_index) AS mn, max(frame_index) AS mx
  FROM frame GROUP BY 1, 2
) f USING (dataset_id, episode_index)
WHERE f.n IS NULL OR f.n <> e.length OR f.mn <> 0 OR f.mx <> e.length - 1;

-- Q6
WITH p AS (
  SELECT dataset_id,
         percentile_cont(0.05) WITHIN GROUP (ORDER BY length) AS p5,
         percentile_cont(0.95) WITHIN GROUP (ORDER BY length) AS p95
  FROM episode GROUP BY dataset_id
)
SELECT d.name, p.p5, p.p95,
       count(*) FILTER (WHERE e.length < p.p5)  AS too_short,
       count(*) FILTER (WHERE e.length > p.p95) AS too_long
FROM episode e JOIN p USING (dataset_id) JOIN dataset d USING (dataset_id)
GROUP BY d.name, p.p5, p.p95 ORDER BY d.name;

-- Q7
SELECT floor(ts)::int AS sec, count(*) AS samples,
       round(avg(m)::numeric, 3) AS avg_acc, round(min(m)::numeric, 3) AS min_acc,
       round(max(m)::numeric, 3) AS max_acc
FROM (
  SELECT ts, sqrt(imu_accel[1]^2 + imu_accel[2]^2 + imu_accel[3]^2) AS m
  FROM frame
  WHERE dataset_id = (SELECT dataset_id FROM dataset WHERE name = 'ego-002')
    AND episode_index = 1 AND imu_accel IS NOT NULL
) x
GROUP BY 1 ORDER BY 1
LIMIT 10;

-- Q8
SELECT e.task_index, left(t.task_text, 60) AS task, count(*) AS n, round(avg(e.length), 1) AS avg_len,
       round(stddev_samp(e.length), 1) AS sd,
       round(stddev_samp(e.length) / avg(e.length), 2) AS cv
FROM episode e
JOIN task t USING (dataset_id, task_index)
JOIN dataset d USING (dataset_id)
WHERE d.name = '681448_v20261124'
GROUP BY e.task_index, t.task_text
HAVING count(*) >= 10 AND stddev_samp(e.length) / avg(e.length) > 0.12
ORDER BY cv DESC;

-- Q9
WITH fp AS (
  SELECT dataset_id, episode_index,
         md5(string_agg(state::text || '|' || action::text, ';' ORDER BY frame_index)) AS fingerprint
  FROM frame WHERE dataset_id IN (1, 2)
  GROUP BY dataset_id, episode_index
), dup AS (
  SELECT dataset_id, fingerprint, array_agg(episode_index ORDER BY episode_index) AS episodes
  FROM fp GROUP BY 1, 2 HAVING count(*) > 1
)
SELECT d.name, count(*) AS dup_groups, sum(cardinality(episodes)) AS dup_episodes,
       (array_agg(episodes ORDER BY episodes[1]))[1:3] AS examples
FROM dup JOIN dataset d USING (dataset_id)
GROUP BY d.name;

-- Q10
WITH s AS (
  SELECT dataset_id, episode_index, frame_index, sign(action[8]) AS s
  FROM frame WHERE dataset_id IN (1, 2) AND action[8] <> 0
), f AS (
  SELECT dataset_id, episode_index, count(*) FILTER (WHERE s <> prev) AS flips
  FROM (SELECT *, lag(s) OVER (PARTITION BY dataset_id, episode_index ORDER BY frame_index) AS prev FROM s) x
  GROUP BY 1, 2
)
SELECT dataset_id, count(*) AS episodes,
       count(*) FILTER (WHERE flips = 0) AS zero_flip,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY flips) AS p50,
       percentile_disc(0.9) WITHIN GROUP (ORDER BY flips) AS p90,
       max(flips) AS max_flips
FROM f GROUP BY 1 ORDER BY 1;

-- Q11
SELECT frame_index, round(ts::numeric, 3) AS ts, round(m::numeric, 3) AS acc,
       round(avg(m) OVER (ORDER BY frame_index ROWS BETWEEN 29 PRECEDING AND CURRENT ROW)::numeric, 3) AS acc_ma_1s
FROM (
  SELECT frame_index, ts, sqrt(imu_accel[1]^2 + imu_accel[2]^2 + imu_accel[3]^2) AS m
  FROM frame WHERE dataset_id = 3 AND episode_index = 1 AND imu_accel IS NOT NULL
) x
ORDER BY frame_index
OFFSET 100 LIMIT 10;

-- Q12
WITH v AS (
  SELECT dataset_id, episode_index, frame_index,
         sqrt((state[1] - lag(state[1]) OVER w)^2
            + (state[2] - lag(state[2]) OVER w)^2
            + (state[3] - lag(state[3]) OVER w)^2) / (ts - lag(ts) OVER w) AS speed
  FROM frame WHERE dataset_id IN (1, 2)
  WINDOW w AS (PARTITION BY dataset_id, episode_index ORDER BY frame_index)
)
SELECT dataset_id, episode_index,
       round(max(speed)::numeric, 3) AS max_speed_mps, round(avg(speed)::numeric, 3) AS avg_speed_mps
FROM v GROUP BY 1, 2
ORDER BY max(speed) DESC NULLS LAST
LIMIT 10;

-- Q13
WITH m AS (
  SELECT episode_index, frame_index, sqrt(imu_accel[1]^2 + imu_accel[2]^2 + imu_accel[3]^2) AS m
  FROM frame WHERE dataset_id = 3 AND imu_accel IS NOT NULL
), z AS (
  SELECT *, (m - avg(m) OVER p) / nullif(stddev_samp(m) OVER p, 0) AS z
  FROM m WINDOW p AS (PARTITION BY episode_index)
)
SELECT episode_index, count(*) AS n,
       count(*) FILTER (WHERE abs(z) > 4) AS outliers,
       round(100.0 * count(*) FILTER (WHERE abs(z) > 4) / count(*), 3) AS pct,
       round(max(abs(z))::numeric, 1) AS max_abs_z
FROM z GROUP BY episode_index
ORDER BY outliers DESC
LIMIT 10;

-- Q14
WITH d AS (
  SELECT dataset_id, episode_index, frame_index,
         sqrt((state[1] - lag(state[1]) OVER w)^2
            + (state[2] - lag(state[2]) OVER w)^2
            + (state[3] - lag(state[3]) OVER w)^2) AS dp
  FROM frame WHERE dataset_id IN (1, 2)
  WINDOW w AS (PARTITION BY dataset_id, episode_index ORDER BY frame_index)
), s AS (
  SELECT *, frame_index - row_number() OVER (PARTITION BY dataset_id, episode_index ORDER BY frame_index) AS grp
  FROM d WHERE dp < 0.001
), runs AS (
  SELECT dataset_id, episode_index, min(frame_index) AS start_f, max(frame_index) AS end_f, count(*) AS n
  FROM s GROUP BY dataset_id, episode_index, grp HAVING count(*) >= 5
)
SELECT r.dataset_id, r.episode_index, r.start_f, r.end_f, r.n AS static_frames, e.length,
       CASE WHEN r.start_f = 1 THEN 'head' WHEN r.end_f = e.length - 1 THEN 'tail' ELSE 'middle' END AS position
FROM runs r JOIN episode e USING (dataset_id, episode_index)
ORDER BY r.n DESC
LIMIT 10;

-- Q15
SELECT * FROM (
  SELECT d.name, e.episode_index, e.length,
         rank()  OVER (PARTITION BY e.dataset_id ORDER BY e.length DESC) AS rnk,
         ntile(4) OVER (PARTITION BY e.dataset_id ORDER BY e.length)    AS quartile,
         round(percent_rank() OVER (PARTITION BY e.dataset_id ORDER BY e.length)::numeric, 3) AS pct_rank
  FROM episode e JOIN dataset d USING (dataset_id)
) x
WHERE rnk <= 3
ORDER BY name, rnk;

-- Q16
WITH c AS (
  SELECT dataset_id, episode_index, length,
         row_number() OVER (PARTITION BY dataset_id ORDER BY length DESC, episode_index) AS k,
         sum(length) OVER (PARTITION BY dataset_id ORDER BY length DESC, episode_index) AS cum,
         sum(length) OVER (PARTITION BY dataset_id) AS total,
         count(*)    OVER (PARTITION BY dataset_id) AS n_eps
  FROM episode
)
SELECT d.name, min(k) AS episodes_for_half_frames, max(n_eps) AS total_episodes,
       round(100.0 * min(k) / max(n_eps), 1) AS pct_of_episodes
FROM c JOIN dataset d USING (dataset_id)
WHERE cum >= total * 0.5
GROUP BY d.name ORDER BY d.name;

-- Q17
DROP MATERIALIZED VIEW IF EXISTS episode_features;
CREATE MATERIALIZED VIEW episode_features AS
WITH w AS (
  SELECT dataset_id, episode_index, frame_index, ts, action[8] AS grip,
         sqrt((state[1] - lag(state[1]) OVER x)^2
            + (state[2] - lag(state[2]) OVER x)^2
            + (state[3] - lag(state[3]) OVER x)^2) AS dp,
         ts - lag(ts) OVER x AS dt
  FROM frame WHERE dataset_id IN (1, 2)
  WINDOW x AS (PARTITION BY dataset_id, episode_index ORDER BY frame_index)
), g AS (
  SELECT dataset_id, episode_index, sign(grip) AS s,
         lag(sign(grip)) OVER (PARTITION BY dataset_id, episode_index ORDER BY frame_index) AS prev
  FROM w WHERE grip <> 0
)
SELECT w.dataset_id, w.episode_index,
       max(w.ts)                              AS duration_s,
       sum(w.dp)                              AS path_len_m,
       max(w.dp / w.dt)                       AS max_speed_mps,
       avg((w.dp < 0.001)::int)               AS static_ratio,
       coalesce(max(gf.flips), 0)             AS gripper_flips
FROM w
LEFT JOIN (SELECT dataset_id, episode_index, count(*) FILTER (WHERE s <> prev) AS flips
           FROM g GROUP BY 1, 2) gf USING (dataset_id, episode_index)
GROUP BY w.dataset_id, w.episode_index;
CREATE UNIQUE INDEX ON episode_features (dataset_id, episode_index);
SELECT dataset_id, count(*) AS episodes,
       round(avg(duration_s)::numeric, 1) AS avg_s, round(avg(path_len_m)::numeric, 3) AS avg_path_m,
       round(avg(static_ratio)::numeric, 3) AS avg_static, round(avg(gripper_flips), 2) AS avg_flips
FROM episode_features GROUP BY 1 ORDER BY 1;

-- Q18
WITH q AS (
  SELECT dataset_id, episode_index,
         array_remove(ARRAY[
           CASE WHEN duration_s < 5          THEN 'too_short'     END,
           CASE WHEN max_speed_mps > 0.25    THEN 'speed_spike'   END,
           CASE WHEN static_ratio > 0.3      THEN 'mostly_static' END,
           CASE WHEN gripper_flips = 0       THEN 'no_grasp'      END
         ], NULL) AS reasons
  FROM episode_features
)
SELECT d.name, count(*) AS episodes,
       count(*) FILTER (WHERE cardinality(reasons) = 0) AS pass,
       round(100.0 * count(*) FILTER (WHERE cardinality(reasons) = 0) / count(*), 1) AS pass_pct,
       count(*) FILTER (WHERE 'too_short'     = ANY (reasons)) AS too_short,
       count(*) FILTER (WHERE 'speed_spike'   = ANY (reasons)) AS speed_spike,
       count(*) FILTER (WHERE 'mostly_static' = ANY (reasons)) AS mostly_static,
       count(*) FILTER (WHERE 'no_grasp'      = ANY (reasons)) AS no_grasp
FROM q JOIN dataset d USING (dataset_id)
GROUP BY d.name ORDER BY d.name;

-- Q19
DROP INDEX IF EXISTS frame_episode_idx;
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON) SELECT count(*) FROM frame WHERE episode_index = 5;
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON) SELECT count(*) FROM frame WHERE dataset_id = 3 AND episode_index = 5;
CREATE INDEX frame_episode_idx ON frame (episode_index);
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON) SELECT count(*) FROM frame WHERE episode_index = 5;
DROP INDEX frame_episode_idx;

-- Q20
WITH u AS (
  SELECT a.dim, a.v
  FROM frame f, unnest(f.action) WITH ORDINALITY AS a(v, dim)
  WHERE f.dataset_id = 1
), s AS (
  SELECT dim, min(v) AS min, max(v) AS max, avg(v) AS mean, stddev_samp(v) AS std
  FROM u GROUP BY dim
)
SELECT jsonb_build_object(
         'min',  jsonb_agg(round(min::numeric, 4)  ORDER BY dim),
         'max',  jsonb_agg(round(max::numeric, 4)  ORDER BY dim),
         'mean', jsonb_agg(round(mean::numeric, 4) ORDER BY dim),
         'std',  jsonb_agg(round(std::numeric, 4)  ORDER BY dim)) AS action_stats
FROM s;
