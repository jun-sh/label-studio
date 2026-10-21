3090 (server@server-System-Product-Name) — paths use /home/server/workspace, NOT /home/user01/...

Quick start (on 3090):
  1. Copy this folder to the machine, e.g.:
       mkdir -p /home/server/workspace
       cp -r isaaclab-arena-3090 /home/server/workspace/
  2. Install (first time only; needs hf auth login, disk ~200GB+, long uv sync):
       cd /home/server/workspace/isaaclab-arena-3090
       chmod +x *.sh
       ./00-preflight.sh
       ./01-setup-arena.sh
       ./02-download-locomanip-assets.sh
     If uv sync fails on robomimic/openpi/mimic, see notes in data-lab chat or use
     rsync of a working tree from the A100 host (below).
  3. GUI replay (first run downloads ~1.5GB Arena USD via SimReady CDN; 5–10 min):
       export DISPLAY=:0
       /home/server/workspace/isaaclab-arena-3090/run-replay-gui.sh
     Log: /home/server/workspace/replay-gui.log — success line: "Finished replaying 1 episode."

After rsync .venv from A100:
  ./fix-venv-paths-3090.sh

If ~/.cache/warp or ~/.cache/ov is root-owned (PermissionError):
  sudo chown -R server:server ~/.cache/warp ~/.cache/ov

env.sh sets ISAACSIM_ASSET_REGION_PROFILE=china and WARP_CACHE_PATH under workspace.

Optional — copy a finished install from A100 (replace A100_HOST):
  rsync -avP user01@A100_HOST:/home/user01/workspace/isaaclab-arena-run/ \
    /home/server/workspace/isaaclab-arena-run/
  # Then symlink or set:
  export WORKSPACE=/home/server/workspace
  export ARENA_DIR=$WORKSPACE/isaaclab-arena-run/IsaacLab-Arena
  # Edit run-replay-gui on A100 copy paths, or use deploy run-replay-gui.sh after
  # pointing ARENA_DIR to $WORKSPACE/isaaclab-arena-run/IsaacLab-Arena
