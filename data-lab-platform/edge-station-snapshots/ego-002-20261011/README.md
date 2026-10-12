# ego-002 edge snapshot (10.10.11.52)

Pulled from **server-ego-002** on **2026-10-11** for git backup / diff against repo SSOT.

| Path | Source on 52 |
|------|----------------|
| `ego-web/` | `/home/server/ego-web` (8080) |
| `ego-web-staging/` | `/home/server/ego-web-staging` (8081) |
| `ego-studio-src/` | `/home/server/workspace/ego-studio/src/ego_capture_studio` |
| `systemd-user/` | `~/.config/systemd/user/` (MCAP + web + preview drop-ins) |
| `ego-station.env.d/` | `~/.config/ego-station.env.d/` (`station.conf`, topology) |
| `STATION_META.txt` | kernel / ego-studio git HEAD if present |

Refresh:

```bash
RC_CAPTURE_PASS=1 bash data-lab-platform/scripts/ego-130-pull-from-station.sh server@10.10.11.52
# or rsync this tree — see data-lab-platform/scripts/ego-pull-edge-snapshot.sh
```

Merge policy: **repo** (`ego-stream-client`, `ego-local-web`, `station-profiles`) remains SSOT; this tree is a **point-in-time mirror** for audit and 003/004 drift checks.
