---
name: restart-lerobot-qc-service
description: Safely health-check, back up, or restart the production LeRobot Quality Check Platform on port 18080. Use only when the user explicitly asks to restart, relaunch, recover, or deploy the production service.
---

# Restart Production LeRobot QC

## Safety Boundary

- Restart only after an explicit production restart, recovery, or deployment request.
- Preserve `qc_results/`, the active SQLite label database, remote dataset cache, and settings.
- Do not hard-code an old dataset path. The active dataset comes from `qc_results/settings.json` and `/api/settings`.
- Do not use `pkill -f`, `killall`, broad process matching, `git pull`, or destructive Git commands in production.

## Fixed Environment

- SSH: `H200-3050` or `root@106.14.2.243 -p 3050`.
- Production directory: `/mnt/LerobotQualityCheckPlatform`.
- Service: `0.0.0.0:18080`.
- Health: `http://127.0.0.1:18080/api/health?user=admin`.

## Preflight

```bash
ssh H200-3050 '
  cd /mnt/LerobotQualityCheckPlatform
  ps -eo pid,ppid,args | awk "/python3 server.py/ && /--port 18080/"
  curl -fsS http://127.0.0.1:18080/api/settings?user=admin
  curl -fsS http://127.0.0.1:18080/api/health?user=admin
'
```

If the restart follows a code deployment, compare source hashes against the intended Git commit
before stopping the service. Production is not a Git worktree.

## Back Up Active Labels

Run this before a deployment or any maintenance beyond a simple health check. It backs up the
current SQLite source of truth without locking out writers for a raw file copy.

```bash
ssh H200-3050 '
  set -e
  cd /mnt/LerobotQualityCheckPlatform
  python3 - <<"PY"
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

settings = json.loads(Path("qc_results/settings.json").read_text())
dataset_id = settings["dataset_id"]
db = Path("qc_results") / dataset_id / "labels.db"
stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
backup = db.with_name(f"labels.db.bak_{stamp}")
src = sqlite3.connect(db)
dst = sqlite3.connect(backup)
src.backup(dst)
dst.close()
src.close()
print(backup)
PY
'
```

## Restart

Use the narrow port match. `run.sh` supplies only a fallback dataset; server startup reads the
persisted setting, so the current local or SSH-backed dataset remains active.

```bash
ssh H200-3050 '
  set -e
  cd /mnt/LerobotQualityCheckPlatform
  old=$(ps -eo pid,args | awk "/python3 server.py/ && /--port 18080/ {print \$1}")
  test -n "$old" && kill "$old" || true
  sleep 1
  setsid -f env HOST=0.0.0.0 PORT=18080 ./run.sh >server.log 2>&1 </dev/null
  sleep 2
  ps -eo pid,ppid,args | awk "/python3 server.py/ && /--port 18080/"
  curl -fsS http://127.0.0.1:18080/api/settings?user=admin
  curl -fsS http://127.0.0.1:18080/api/health?user=admin
'
```

If the new process or health check fails, inspect `tail -80 server.log`; do not keep retrying
without identifying the failure.
