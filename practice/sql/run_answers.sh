#!/usr/bin/env bash
# Run reference answers. Usage: ./run_answers.sh        (all)
#                               ./run_answers.sh 13     (only Q13)
set -euo pipefail
cd "$(dirname "$0")"
only="${1:-}"
python3 - "$only" <<'PY'
import re, subprocess, sys
only = sys.argv[1]
blocks = re.split(r"^-- Q(\d+)\n", open("answers.sql").read(), flags=re.M)[1:]
for num, sql in zip(blocks[0::2], blocks[1::2]):
    if only and num != only:
        continue
    print(f"===== Q{num}")
    r = subprocess.run(["docker", "exec", "-i", "sql-practice-pg", "psql", "-U", "practice", "-d", "robodata",
                        "-P", "pager=off", "-v", "ON_ERROR_STOP=1", "-q"], input=sql, text=True, capture_output=True)
    print(r.stdout.rstrip())
    if r.returncode:
        print("ERROR:", r.stderr.strip()); sys.exit(1)
PY
