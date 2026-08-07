---
name: switch-lerobot-qc-dataset
description: Change the active local or SSH-backed LeRobot dataset for the production or dev QC service without clearing labels or restarting the service. Use when the user asks to load, switch, refresh, or configure a QC dataset path.
---

# Switch LeRobot QC Dataset

## Fixed Targets

- Production: `/mnt/LerobotQualityCheckPlatform`, port `18080`.
- Dev: `/mnt/LerobotQualityCheckPlatform-dev`, port `18081`.
- Control SSH host: `H200-3050`.

Choose production only when the user says production, main, `18080`, or deployment. Keep dev and
production settings independent. The setting API is authoritative; no service restart is needed
for a ready dataset.

## Accepted Inputs

- A single local path under `/mnt`, for example `/mnt/nm_dataset/dataset/example`.
- A single remote source URI, for example:

```text
ssh://root@106.14.2.243:4095/mnt/workspace/user/lerobot/example
```

- Multiple local and/or remote sources in one settings request:

```json
{
  "dataset_paths": [
    "/mnt/nm_dataset/dataset/local_a",
    "ssh://root@106.14.2.243:4095/mnt/workspace/user/lerobot/remote_b"
  ],
  "active_dataset_source": "/mnt/nm_dataset/dataset/local_a"
}
```

The server materializes every remote dataset under `remote_dataset_cache/` with
`rsync --copy-links`, validates every source, loads every catalog entry, and schedules a video
proxy build for each one. `dataset_source` is the configured source for the currently active
dataset; `dataset_path` is its loaded local/cache directory. The response field `datasets` lists
all configured datasets and their independent `dataset_id`, readiness, totals, and cache path.
Do not expect `dataset_source` and `dataset_path` to be identical for an SSH source.

## Validate Before Changing Settings

For a local source, verify `meta/info.json`, `meta/episodes.jsonl`, Parquet count, and video count
on 3050. For an SSH source, first prove that 3050 can reach the remote host using the configured
identity, normally `/root/.ssh/id_ed25519_lqcp_4110`:

```bash
ssh H200-3050 '
  ssh -i /root/.ssh/id_ed25519_lqcp_4110 -o BatchMode=yes -o ConnectTimeout=20 \
    -p 4095 root@106.14.2.243 \
    "test -f /mnt/workspace/user/lerobot/example/meta/info.json && \
     test -f /mnt/workspace/user/lerobot/example/meta/episodes.jsonl"
'
```

If authentication fails, do not call the settings API. Add the 3050 public key to the remote
host only when the user has authorized that access change.

## Persist And Verify

```bash
ssh H200-3050 '
  set -e
  port=18080
  curl -fsS -X POST "http://127.0.0.1:${port}/api/settings?user=admin" \
    -H "Content-Type: application/json" \
    --data '{"dataset_paths":["/mnt/nm_dataset/dataset/local_a","ssh://root@106.14.2.243:4095/mnt/workspace/user/lerobot/remote_b"],"active_dataset_source":"/mnt/nm_dataset/dataset/local_a"}'
  curl -fsS "http://127.0.0.1:${port}/api/settings?user=admin"
  curl -fsS "http://127.0.0.1:${port}/api/health?user=admin"
  curl -fsS "http://127.0.0.1:${port}/api/episodes?user=admin&page=1&page_size=1"
'
```

Verify that settings, health, and episodes for the active dataset return the same `dataset_id`,
that `dataset_source` equals the selected source, and that every item in `datasets` is ready with
its own `dataset_id`. Confirm every dataset's proxy build state before reporting the load complete.
To switch only the active dataset without changing the catalog, POST the existing
`dataset_paths` list with a different `active_dataset_source`.

## Safety

- Never clear `qc_results/`; each materialized dataset has an independent label store.
- Do not refresh a remote dataset unless the user explicitly asks for source refresh.
- Keep `dataset_paths` as the source list, not cache paths, so settings survive service restart.
- Remind users to refresh open normal QC and admin pages after changing the active dataset.
