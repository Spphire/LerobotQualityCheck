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

- Local path under `/mnt`, for example `/mnt/nm_dataset/dataset/example`.
- Remote source URI, for example:

```text
ssh://root@106.14.2.243:4095/mnt/workspace/user/lerobot/example
```

The server materializes remote datasets under `remote_dataset_cache/` with `rsync --copy-links`.
`dataset_source` is the configured local path or SSH URI. `dataset_path` is the actual loaded
local/cache directory. Do not expect them to be identical for an SSH source.

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
  source="ssh://root@106.14.2.243:4095/mnt/workspace/user/lerobot/example"
  curl -fsS -X POST "http://127.0.0.1:${port}/api/settings?user=admin" \
    -H "Content-Type: application/json" \
    --data "{\"dataset_path\":\"${source}\"}"
  curl -fsS "http://127.0.0.1:${port}/api/settings?user=admin"
  curl -fsS "http://127.0.0.1:${port}/api/health?user=admin"
  curl -fsS "http://127.0.0.1:${port}/api/episodes?user=admin&page=1&page_size=1"
'
```

Verify that settings, health, and episodes return the same `dataset_id`; that `dataset_source`
equals the requested source; and that `dataset_path` is a ready local/cache directory. Confirm the
proxy build state before reporting the switch complete.

## Safety

- Never clear `qc_results/`; each materialized dataset has an independent label store.
- Do not refresh a remote dataset unless the user explicitly asks for source refresh.
- Remind users to refresh open normal QC and admin pages after a switch.
