---
name: query-lerobot-qc-label
description: Read a LeRobot QC episode's current canonical label, annotator, timestamp, UUID, and recent label events from the production label database. Use when the user asks who labeled an episode or what its accept, reject, pending, or unlabeled result is.
---

# Query LeRobot QC Labels

Use this skill read-only. Do not restart services, change settings, clear labels, or edit
`qc_results` while answering a lookup.

## Defaults

- Control host: `H200-3050`.
- Production project: `/mnt/LerobotQualityCheckPlatform`.
- Active dataset and label database are resolved from `qc_results/settings.json`.

## Query One Episode

From the repository root, run the bundled script:

```powershell
py .codex/skills/query-lerobot-qc-label/scripts/query_label.py 497
```

Use `--dataset-id <id>` only when the user explicitly asks about a non-current historical dataset.
The output contains `current_label`, `recent_events`, `dataset_source`, `dataset_path`,
`dataset_id`, and the database path.

## Report

Report the active dataset, episode index, status, annotator/user, update time, and UUID. Explain
that `accept`, `reject`, and explicit `pending` are recorded states; a null `current_label` means
the episode has not been labeled in that dataset.
