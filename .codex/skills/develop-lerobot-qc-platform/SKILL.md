---
name: develop-lerobot-qc-platform
description: Develop, test, inspect, or synchronize the LeRobot Quality Check Platform without interrupting production. Use when changing platform code, using the dev worktree, testing port 18081, inspecting the dev service, or preparing a production deployment.
---

# Develop LeRobot QC Platform

## Fixed Environment

- SSH control host: `root@106.14.2.243`, port `3050` (`H200-3050` locally).
- Production directory: `/mnt/LerobotQualityCheckPlatform`, public port `18080`.
- Dev worktree: `/mnt/LerobotQualityCheckPlatform-dev`, branch `server-dev-18081`, port `18081`.
- Git remote: `git@github.com:Spphire/LerobotQualityCheck.git`.
- Production is not a Git worktree. Treat dev as the source-controlled deployment source.

The active dataset is persisted in `qc_results/settings.json`. Never infer it from the
`--dataset` fallback argument or a hard-coded default. Read `/api/settings` before and after
any service operation.

## Guardrails

- Make code changes in dev first. Do not edit or restart production unless the user explicitly requests a production deployment or restart.
- Never delete `qc_results/`, `labels.db`, `labels.json`, or `labels.jsonl` unless the user explicitly requests a reset.
- Do not stop, kill, or bind over port `18080` while developing.
- Use a narrow `--port 18081` process match for dev restarts. Do not use `pkill -f` or `killall`.
- Treat `video_proxy/`, `remote_dataset_cache/`, `*.tmp.mp4`, `server.dev.log`, and `__pycache__/` as regenerable only after checking scope and size.
- Preserve separately configured dev and production datasets. Do not assume they should match.

## Read-Only Status

```bash
ssh H200-3050 '
  cd /mnt/LerobotQualityCheckPlatform-dev
  git status --short --branch
  git log --oneline -1
  curl -fsS http://127.0.0.1:18081/api/settings?user=admin
  curl -fsS http://127.0.0.1:18081/api/health?user=admin
'
```

Also verify production remains healthy before beginning a substantial dev task:

```bash
ssh H200-3050 '
  ps -eo pid,ppid,args | awk "/python3 server.py/ && /--port 18080/"
  curl -fsS http://127.0.0.1:18080/api/health?user=admin
'
```

## Test A Change

1. Run `python3 -m py_compile server.py`, `node --check web/app.js`, and `git diff --check` locally.
2. Copy or pull only tracked source files into `/mnt/LerobotQualityCheckPlatform-dev`.
3. Restart only the existing `18081` service when backend code changed. Static-only changes can be tested by refreshing the dev page.
4. Verify `/api/settings`, `/api/health`, `/api/episodes?page=1&page_size=1`, normal QC, `/admin/review`, `/admin`, and `/rank` as applicable.
5. Check browser console errors and preserve the active dev settings and labels.

When a dev restart is required, use the persisted settings as the dataset authority:

```bash
ssh H200-3050 '
  set -e
  cd /mnt/LerobotQualityCheckPlatform-dev
  old=$(ps -eo pid,args | awk "/python3 server.py/ && /--port 18081/ {print \$1}")
  test -n "$old" && kill "$old" || true
  sleep 1
  setsid -f env HOST=0.0.0.0 PORT=18081 ./run.sh >server.dev.log 2>&1 </dev/null
  sleep 2
  curl -fsS http://127.0.0.1:18081/api/health?user=admin
'
```

## Git And Deployment

- Commit on `server-dev-18081`, push the requested branches, and verify the remote SHA.
- If the user asks to deploy production, back up the active label store first, copy only tracked source files, then use `$restart-lerobot-qc-service`.
- Do not claim production is synchronized merely because GitHub and dev are synchronized. Check the production file hashes and running process separately.
