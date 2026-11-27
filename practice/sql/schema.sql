-- Practice schema: LeRobot v3 dataset metadata + per-frame time series.
DROP TABLE IF EXISTS frame, episode, task, dataset CASCADE;

CREATE TABLE dataset (
  dataset_id   serial PRIMARY KEY,
  name         text UNIQUE NOT NULL,
  robot_type   text,
  fps          int NOT NULL,
  delivered_on date              -- parsed from the delivery folder name (vYYYYMMDD); NULL for live streams
);

CREATE TABLE task (
  dataset_id  int REFERENCES dataset,
  task_index  int,
  task_text   text,
  PRIMARY KEY (dataset_id, task_index)
);

CREATE TABLE episode (
  dataset_id        int REFERENCES dataset,
  episode_index     int,
  task_index        int,
  length            int,          -- number of frames
  station_id        text,
  embodiment        text,
  annotation_status text,
  PRIMARY KEY (dataset_id, episode_index)
);

CREATE TABLE frame (
  dataset_id      int,
  episode_index   int,
  frame_index     int,
  ts              real,           -- seconds since episode start
  state           real[],         -- observation.state
  action          real[],
  hand_conf_left  real,           -- ego only
  hand_conf_right real,           -- ego only
  imu_accel       real[],         -- ego only, m/s^2 xyz
  head_pose       real[],         -- ego only, xyz + quaternion
  PRIMARY KEY (dataset_id, episode_index, frame_index),
  FOREIGN KEY (dataset_id, episode_index) REFERENCES episode
);
