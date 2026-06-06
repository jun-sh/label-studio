#!/bin/bash
LOG=/tmp/strict20hz_soak_round5.log
echo "=== round5 soak start $(date -Is | tr -d '\n') pid=$$ ===" > "$LOG"
END=$(( $(date +%s) + 1800 ))
FPS_FAIL=0
while [ $(date +%s) -lt $END ]; do
  line=$(journalctl --user -u ecs-record-oak-stream --since "90 sec ago" --no-pager 2>/dev/null \
    | grep capture_fps | tail -1)
  if [ -n "$line" ]; then
    echo "$line" >> "$LOG"
    fps=$(echo "$line" | sed -n 's/.*capture_fps=\([0-9.]*\).*/\1/p')
    if [ -n "$fps" ]; then
      awk -v f="$fps" 'BEGIN { if (f < 19.9 || f > 20.1) exit 1 }' || FPS_FAIL=$((FPS_FAIL + 1))
    fi
    pend=$(echo "$line" | sed -n 's/.*pending_segments=\([0-9]*\).*/\1/p')
    if [ -n "$pend" ] && [ "$pend" -ge 30 ]; then
      echo "PENDING_HIGH pending_segments=$pend ts=$(date -Is)" >> "$LOG"
    fi
  fi
  sleep 60
done
echo "=== round5 soak end $(date -Is | tr -d '\n') fps_out_of_band=$FPS_FAIL ===" >> "$LOG"
python3 /tmp/strict20hz_round5_analyze.py >> "$LOG" 2>&1
