# Agent Context Guide

This file is the fast context contract for agents working on
`LerobotQualityCheckPlatform`. Read it before changing code, settings, datasets, labels, or
services. Read [README.md](README.md) for product behavior and
[`.codex/skills`](.codex/skills) for reusable operational workflows.

## First 90 Seconds

Run these read-only checks before deciding what to change:

```bash
# Local repository
git status --short --branch
git log --oneline -1

# Remote dev and production state
ssh H200-3050 'cd /mnt/LerobotQualityCheckPlatform-dev && git status --short --branch && git rev-parse HEAD'
ssh H200-3050 'curl -fsS http://127.0.0.1:18081/api/settings?user=admin'
ssh H200-3050 'curl -fsS http://127.0.0.1:18080/api/settings?user=admin'
```

Do not infer the active dataset from `DEFAULT_DATASET`, `run.sh`, an old process command line,
or prior chat history. `qc_results/settings.json` and `/api/settings` are authoritative.

## Topology And Ownership

| Surface | Location | Role |
|---|---|---|
| Git working tree | local repository and `/mnt/LerobotQualityCheckPlatform-dev` | Source-controlled development |
| Dev service | `root@106.14.2.243:3050`, port `18081` | Test deployment, branch `server-dev-18081` |
| Production service | `/mnt/LerobotQualityCheckPlatform`, port `18080` | Live service; not a Git worktree |
| Git remote | `git@github.com:Spphire/LerobotQualityCheck.git` | `main` and `server-dev-18081` should normally point to the same tested commit |
| Project workflows | `.codex/skills/` | Versioned agent procedures |

Production and dev intentionally may use different datasets and label stores. Code parity does
not imply dataset parity.

## Non-Negotiable Invariants

1. **Preserve annotation state.** `qc_results/<dataset_id>/labels.db` is the source of truth.
   Never delete, reset, replace, or raw-copy a live SQLite database unless the user explicitly
   asks. Use SQLite backup for a live snapshot.
2. **Keep source and cache distinct.** `dataset_source` is the configured local path or SSH URI;
   `dataset_path` is the loaded local/cache path. For `ssh://` sources they are expected to differ.
3. **Treat production as live.** Do not edit, deploy, or restart `/mnt/LerobotQualityCheckPlatform`
   unless the user explicitly requests it. Use dev first.
4. **Do not use broad process commands.** Never use `pkill -f`, `killall`, or a loose `grep` to
   restart a service. Match `python3 server.py` and the exact target port.
5. **Do not delete raw datasets.** `remote_dataset_cache/`, `video_proxy/`, and `__pycache__/`
   can be regenerated only after an explicit scope/size check. Original datasets under `/mnt` are
   never cleanup targets.
6. **Keep secrets out of Git.** `.env`, `.env.*`, SSH private keys, DM3 passwords/tokens, and
   cookie/session data must not be committed or printed.

## Source Map

| Area | Primary files | Notes |
|---|---|---|
| HTTP API, settings, labels, media, proxy | `server.py` | Python standard-library server; SQLite labels; remote cache materialization |
| Desktop/admin/phone UI behavior | `web/app.js` | Dataset context, keyboard handling, video sync, curves, 3D rendering |
| UI structure | `web/index.html`, `web/admin_review.html`, `web/admin.html`, `web/rank.html`, `web/phone.html` | Routes are served directly by `server.py` |
| UI styles | `web/styles.css` | Preserve existing responsive layout constraints |
| Launch fallback | `run.sh` | `DATASET_PATH` is fallback only; persisted settings win |
| Dataset filtering | `tools/filter_lerobot_dataset.py`, `scripts/incremental_append_lerobot_accepts.py` | Match accepted labels by UUID when available |
| Agent workflows | `.codex/skills/*/SKILL.md` | Use the specialized skill before repeating an established operation |

There is no frontend build step. Static files are served directly by `server.py`.

## Runtime Data Contract

### Dataset Settings

`GET /api/settings` returns the current loaded context. After a dataset change, require all of
these to agree before calling it complete:

- `/api/settings`, `/api/health`, and `/api/episodes?page=1&page_size=1` have the same `dataset_id`.
- `dataset_source` equals the requested path or URI.
- `dataset_path` exists locally and contains ready metadata, Parquet, and video directories.
- The video proxy state reaches `complete` or reports its exact pending/failed state.

Remote dataset paths use this form:

```text
ssh://root@106.14.2.243:<port>/mnt/workspace/<user>/.../lerobot/<dataset>
```

The 3050 host must be able to authenticate to the remote target with
`LQCP_REMOTE_DATASET_SSH_IDENTITY`. Validate SSH access before posting settings. Do not add an
SSH key to another server without user authorization.

### Labels And Concurrency

- `labels` holds one canonical state per episode; `label_events` holds history.
- Normal labels reject conflicting concurrent writes rather than silently overwriting them.
- Admin labels can force an override and ignore presence locks.
- Presence locks expire quickly; user sessions are retained for one week.
- Exports and dataset filters use canonical labels, not client-side state.

### Visual Data

- Video playback, gripper curves, and the 3D highlight share the same frame position.
- Render state and action for left/right hands. Head pose is a camera reference, not a visible 3D
  trajectory.
- Pose fallback uses `[x, y, z, qw, qx, qy, qz]`. Do not alter quaternion order or coordinate
  transforms based on visual intuition alone; inspect `device_type` and existing trajectory tests.
- `teleoperation*`, `inference_r1`, and `rollout` use the teleop compatibility transform. Preserve the
  metadata-driven branch when adding device types.

## Safe Development Loop

1. Work in the dev branch and test on `18081`.
2. Read the current dev settings before touching code; preserve its dataset and labels.
3. Make the smallest change consistent with existing patterns.
4. Run at least:

   ```bash
   python3 -m py_compile server.py
   node --check web/app.js
   node --check web/admin.js
   node --check web/rank.js
   git diff --check
   ```

5. If backend behavior changed, restart only the dev port and verify API plus UI. For static-only
   changes, refresh the dev browser page.
6. Test normal QC, `/admin/review`, `/admin`, and `/rank` when their shared dataset context or
   navigation changes.
7. Commit on `server-dev-18081`, push the requested branches atomically, and verify the SHA.

Use `develop-lerobot-qc-platform` for the detailed dev workflow.

## Production Deployment Checklist

Only perform this after explicit user approval.

1. Record `/api/settings`, `/api/health`, active label counts, and current process PID.
2. Back up active `settings.json` and `labels.db` with SQLite backup.
3. Deploy tracked files from the tested dev commit without deleting `qc_results`, remote cache,
   proxy videos, `.env*`, or other runtime state.
4. Compare production tracked-file hashes with dev before restarting.
5. Restart only `18080` from `/mnt/LerobotQualityCheckPlatform`.
6. Verify process, settings hash, label counts, proxy media range requests, trajectory API, and
   normal/admin pages. Check browser console errors.
7. Remove only the deployment backup if the user explicitly requests cleanup.

Do not claim production is synchronized merely because Git is synchronized. Verify file hashes
and the running service separately.

## Skills To Use First

| Need | Skill |
|---|---|
| Develop/test on 18081 | `develop-lerobot-qc-platform` |
| Restart or deploy 18080 | `restart-lerobot-qc-service` |
| Change local/SSH dataset | `switch-lerobot-qc-dataset` |
| Find an episode label or annotator | `query-lerobot-qc-label` |
| Build accepted-only training data | `filter-transfer-lerobot-dataset` |
| Produce assignment ranges | `generate-qc-assignment-table` |
| Validate data integrity | `check-lerobot-dataset-integrity` |

Use the repository copy under `.codex/skills` as the versioned source. Keep the local
`~/.codex/skills` copy hash-aligned after a skill update.

## Common Failure Modes

- **UI displays a cache path instead of SSH URI:** ensure the frontend consumes both
  `dataset_source` and `dataset_path`; initial settings fetch must happen before episode loading.
- **Remote dataset switch fails:** test SSH from 3050 with the configured identity first; do not
  guess an alternate copy route.
- **Users cannot see another user's label:** verify the shared canonical label, sync loop, and
  SQLite event/write response before touching browser state.
- **3D camera jumps during playback:** preserve the user-controlled Three.js camera/control state;
  do not recreate the scene every frame.
- **Production appears stale after Git push:** production is a deployed directory, not a Git
  checkout. Perform the explicit deployment checklist.
