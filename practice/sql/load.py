#!/usr/bin/env python3
"""Load real LeRobot datasets from data-storage into the practice Postgres.

Usage: python3 load.py            (container sql-practice-pg must be running)
"""
import csv
import glob
import io
import json
import math
import os
import re
import subprocess

import pyarrow.parquet as pq

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data-storage"))
HERE = os.path.dirname(os.path.abspath(__file__))
CONTAINER = "sql-practice-pg"
DATASETS = [
    "lerobot-qc/delivery/681448_v20261124",
    "lerobot-qc/delivery/681453_v20261125",
    "stream/ego-002",
]


def psql(args, stdin=None):
    cmd = ["docker", "exec", "-i", CONTAINER, "psql", "-q", "-v", "ON_ERROR_STOP=1",
           "-U", "practice", "-d", "robodata"] + args
    return subprocess.run(cmd, input=stdin, check=True, capture_output=True, text=True).stdout


def copy(table, cols, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerows(rows)
    psql(["-c", f"\\copy {table} ({','.join(cols)}) FROM STDIN WITH (FORMAT csv)"], buf.getvalue())


def arr(v):
    if v is None:
        return None
    if not isinstance(v, (list, tuple)):
        v = [v]
    return "{" + ",".join("NaN" if (x is None or math.isnan(x)) else repr(float(x)) for x in v) + "}"


def num(v):
    if v is None:
        return None
    if isinstance(v, list):
        v = v[0] if v else None
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def read_dir(pattern):
    files = sorted(glob.glob(pattern))
    return [r for f in files for r in pq.read_table(f).to_pylist()]


def main():
    with open(os.path.join(HERE, "schema.sql")) as f:
        psql([], f.read())

    for i, rel in enumerate(DATASETS, start=1):
        d = os.path.join(ROOT, rel)
        info = json.load(open(os.path.join(d, "meta", "info.json")))
        m = re.search(r"_v(\d{8})$", rel)
        delivered = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else None
        copy("dataset", ["dataset_id", "name", "robot_type", "fps", "delivered_on"],
             [[i, os.path.basename(rel), info.get("robot_type"), info["fps"], delivered]])

        eps = read_dir(os.path.join(d, "meta", "episodes", "*", "*.parquet"))
        tasks_file = os.path.join(d, "meta", "tasks.parquet")
        if os.path.exists(tasks_file):
            t = pq.read_table(tasks_file).to_pylist()
            tasks = {r["task_index"]: r.get("task") or r.get("__index_level_0__") for r in t}
        else:
            tasks = {}
            for e in eps:
                tasks.setdefault(e.get("task_index", 0), (e.get("tasks") or [""])[0])
        copy("task", ["dataset_id", "task_index", "task_text"], [[i, k, v] for k, v in tasks.items()])

        frames = read_dir(os.path.join(d, "data", "*", "*.parquet"))
        first_task = {}
        for fr in frames:
            first_task.setdefault(fr["episode_index"], fr["task_index"])
        copy("episode", ["dataset_id", "episode_index", "task_index", "length", "station_id",
                         "embodiment", "annotation_status"],
             [[i, e["episode_index"], e.get("task_index", first_task.get(e["episode_index"])),
               e["length"], e.get("station_id"), e.get("embodiment"), e.get("annotation_status")]
              for e in eps])

        copy("frame", ["dataset_id", "episode_index", "frame_index", "ts", "state", "action",
                       "hand_conf_left", "hand_conf_right", "imu_accel", "head_pose"],
             [[i, fr["episode_index"], fr["frame_index"], fr["timestamp"],
               arr(fr.get("observation.state")), arr(fr.get("action")),
               num(fr.get("observation.hand_conf_left")), num(fr.get("observation.hand_conf_right")),
               arr(fr.get("observation.imu_accel")), arr(fr.get("observation.head_pose"))]
              for fr in frames])
        print(f"{rel}: {len(eps)} episodes, {len(frames)} frames, {len(tasks)} tasks")

    psql(["-c", "ANALYZE"])


if __name__ == "__main__":
    main()
