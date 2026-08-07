---
name: filter-transfer-lerobot-dataset
description: Generate a new LeRobot dataset from QC annotation results and place it on a training server. Use when the user wants to filter a source LeRobot dataset by accepted/rejected QC labels, auto-locate the matching annotation store for the dataset, reindex/copy accepted episodes, validate the output, or transfer/stage the filtered dataset to a specified remote host and path.
---

# Filter And Transfer LeRobot Dataset

Use this skill to create an accepted-only LeRobot dataset from QC labels and stage it on a training host.

## Multi-Dataset Catalogs

When `/api/settings` or the production settings contain multiple `dataset_paths`, annotations
remain isolated by `dataset_id`. A unified review list may show all catalog entries together, but
filtering must still run once per source dataset. Never match an `episode_index` from one source
against another source's label database.

For a six-dataset catalog, resolve each source to its own `dataset_id` and `labels.db`, then run
the normal UUID-based filter workflow six times. Use distinct outputs such as:

```text
<output-root>/<source-name>_filtered
```

The final result is six independent filtered LeRobot datasets. Preserve a manifest beside the
outputs recording `dataset_source`, `dataset_id`, output path, accepted count, and UUID match
mode. This keeps the unified QC view convenient while preserving traceability for training and
re-auditing.

Bundled script in the QC platform repo:

```bash
tools/filter_lerobot_dataset.py \
  --source /path/to/source_lerobot_dataset \
  --labels /path/to/labels.db-or-labels.json \
  --output /path/to/output_dataset \
  --workers 4
```

The script preserves LeRobot layout, keeps final `accept` episodes, drops `reject` and unlabeled/pending episodes, matches labels to the source dataset by `episode_uuid` when label UUIDs exist, rewrites episode indices to `0..N-1`, rewrites parquet `episode_index` and global `index`, copies videos/latents/instruct embeddings with real file copies, updates `meta/info.json`, and writes:

- `meta/qc_filter_summary.json`
- `meta/qc_filter_accept_mapping.jsonl`

Use real copies for filtered datasets that may later move to another machine. Do not use hardlinks for these outputs unless the user explicitly asks for a local-only staging dataset.

Do not assume `episode_index` is stable across regenerated same-name datasets. If labels contain `episode_uuid`, the filter must resolve labels to the current source dataset by UUID. Treat an index/UUID mismatch as normal for regenerated datasets, but verify that every output episode UUID has an `accept` label.

The script supports:

- `--workers N` for per-episode parallel copy/remap work. Start with `--workers 4`; reduce to `2` or `1` if the storage backend shows I/O pressure.
- `--limit-accepted N` for smoke tests. Always use this first when the source data may still be changing or the user asks not to run a full conversion.

Bundled incremental helper:

```bash
scripts/incremental_append_lerobot_accepts.py \
  --source /path/to/source_lerobot_dataset \
  --labels /path/to/current-labels-snapshot.db \
  --output /path/to/existing_qc_accept \
  --part-count 5 \
  --workers 4
```

Use it only when an accepted-only output already exists and the user asks to "incrementally supplement", "append newly accepted", or update existing filtered datasets without recopying everything.

## Required Parameters

Establish these before running commands:

- `SOURCE_DATASET`: source LeRobot dataset, usually under `/mnt/nm_dataset/dataset/...`.
- `TARGET_HOST`: training host or SSH alias, for example `H200-4056`.
- `OUTPUT_PATH`: final dataset path on the target, for example `/mnt/workspace/shenyibo/datasets/<source>_qc_accept`.
- `QC_HOST`: host where QC results live. Default for this project is `root@106.14.2.243 -p 3050` or local alias `H200-3050`.
- `QC_PROJECT`: default `/mnt/LerobotQualityCheckPlatform`.

If the user gives a target directory instead of a full output path, derive output as:

```text
<target-dir>/<source-dataset-name>_qc_accept
```

## Locate Labels

Prefer SQLite labels:

```text
$QC_PROJECT/qc_results/<dataset_id>/labels.db
```

Fall back to:

```text
$QC_PROJECT/qc_results/<dataset_id>/labels.json
```

Compute `dataset_id` exactly like the QC server:

```python
import hashlib, re
from pathlib import Path
source = "/mnt/nm_dataset/dataset/giftbox_0628_1912episodes"
digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]
basename = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(source).name).strip("_") or "dataset"
print(f"{basename}-{digest}")
```

If that exact directory is missing, search by basename:

```bash
find "$QC_PROJECT/qc_results" -maxdepth 1 -type d -name "$(basename "$SOURCE_DATASET")-*"
```

Then inspect the candidate label store and prefer the one whose stored `dataset_path` matches `SOURCE_DATASET`. If there are multiple plausible matches, stop and report the candidates instead of guessing.

For a live SQLite DB, copy it with SQLite backup rather than raw `cp`:

```bash
python3 - "$DB" "$SNAPSHOT" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1:3]
con = sqlite3.connect(src)
out = sqlite3.connect(dst)
con.backup(out)
out.close()
con.close()
PY
```

## Choose The Run Host

Avoid transferring a generated 40GB+ dataset when possible.

1. Check whether `TARGET_HOST` can directly read `SOURCE_DATASET` and write `OUTPUT_PATH`:

```bash
ssh "$TARGET_HOST" "test -d '$SOURCE_DATASET' && mkdir -p '$(dirname "$OUTPUT_PATH")' && df -h '$SOURCE_DATASET' '$(dirname "$OUTPUT_PATH")'"
```

2. If the target can access both, run the filter script on `TARGET_HOST`.
3. If the target cannot access the source, look for a runner host that can access both the source dataset and the target/shared workspace. In the H200 environment, `H200-4057` may see `/mnt/nm_dataset` and the same `/mnt/workspace` as `H200-4056`; verify with `df -h` and `findmnt` before assuming.
4. Use large rsync of a generated dataset only as a last resort after proving no host can read source and write destination. If rsync is necessary, sync into a hidden temporary directory and rename atomically when complete.

## Generate

Copy the script and label snapshot to the chosen run host:

```bash
REMOTE_LABELS=/tmp/qc_labels.db     # use /tmp/qc_labels.json when falling back to JSON
scp tools/filter_lerobot_dataset.py "$RUN_HOST:/tmp/filter_lerobot_dataset.py"
scp "$LABEL_SNAPSHOT" "$RUN_HOST:$REMOTE_LABELS"
```

Before a full run, make a small test output and remove it after validation:

```bash
TEST_OUTPUT="${OUTPUT_PATH}_test5"
ssh "$RUN_HOST" "rm -rf '$TEST_OUTPUT' '$TEST_OUTPUT'.tmp.* && \
python3 /tmp/filter_lerobot_dataset.py \
  --source '$SOURCE_DATASET' \
  --labels '$REMOTE_LABELS' \
  --output '$TEST_OUTPUT' \
  --limit-accepted 5 \
  --workers 4"
```

Validate the small output with the checks below, including UUID acceptance checks, then delete it. Stop here if the user asked not to run a full conversion yet.

Run full generation on the host that can access source and destination only after the user confirms the data is ready:

```bash
ssh "$RUN_HOST" "python3 -c 'import pyarrow' && \
python3 /tmp/filter_lerobot_dataset.py \
  --source '$SOURCE_DATASET' \
  --labels '$REMOTE_LABELS' \
  --output '$OUTPUT_PATH' \
  --workers 4"
```

Do not pass `--overwrite` unless the user explicitly asks to replace an existing output dataset. The script writes to a timestamped temporary output directory and renames it to the final path only after success.

## Incremental Update Existing Outputs

Use this when the output dataset already exists and newer labels may contain more `accept` episodes.

Important safety rules:

- Always make a fresh SQLite backup snapshot of live `labels.db` first.
- First run the incremental helper with `--dry-run`.
- The helper is append-only. It refuses to run if any episode already present in the filtered output is no longer `accept`; in that case, regenerate the full output instead of appending.
- It resolves labels to the source dataset by `episode_uuid` when labels contain UUIDs, matching the full filter workflow.
- It backs up each output dataset's critical meta files under `meta/.incremental_backup_<timestamp>/` before writing.
- It appends newly accepted source episodes to the existing output, rewrites parquet `episode_index` and global `index`, copies videos/latents/latent sidecars/instruction embeddings, and updates `meta/info.json`, `meta/episodes.jsonl`, `meta/episodes_stats.jsonl`, `episode_uuids/episode_uuids_part_001.txt`, `meta/qc_filter_accept_mapping.jsonl`, and `meta/qc_filter_summary.json`.
- With `--part-count 5`, it also updates sibling datasets named `<output>_part01` through `<output>_part05`, greedily distributing new episodes by current frame totals so shard durations stay balanced.

Dry-run:

```bash
python3 scripts/incremental_append_lerobot_accepts.py \
  --source "$SOURCE_DATASET" \
  --labels "$LABEL_SNAPSHOT" \
  --output "$OUTPUT_PATH" \
  --part-count 5 \
  --workers 4 \
  --dry-run
```

Apply:

```bash
python3 scripts/incremental_append_lerobot_accepts.py \
  --source "$SOURCE_DATASET" \
  --labels "$LABEL_SNAPSHOT" \
  --output "$OUTPUT_PATH" \
  --part-count 5 \
  --workers 4
```

The helper writes or refreshes:

```text
<output-parent>/<output-name>_split_summary.json
```

For a custom split summary path, pass `--summary /path/to/summary.json`. If shard names are not `<output>_part01..05`, pass `--part-prefix NAME_part`.

## Validate

After generation, validate on the target-visible host:

```bash
ssh "$TARGET_HOST" "python3 - <<'PY'
from pathlib import Path
import json
out = Path('$OUTPUT_PATH')
info = json.loads((out/'meta/info.json').read_text())
summary = json.loads((out/'meta/qc_filter_summary.json').read_text())
print('output', out)
print('episodes', info.get('total_episodes'), 'frames', info.get('total_frames'), 'videos', info.get('total_videos'))
print('accepted', summary.get('accepted'), 'rejected', summary.get('rejected'))
print('data parquet', len(list((out/'data').glob('chunk-*/episode_*.parquet'))))
print('videos', len(list((out/'videos').glob('chunk-*/*/episode_*.mp4'))))
print('latents', len(list((out/'latents').glob('chunk-*/*/*.pth'))))
print('mapping exists', (out/'meta/qc_filter_accept_mapping.jsonl').exists())
print('match mode', summary.get('match', {}).get('match_mode'))
print('index/uuid mismatches', summary.get('match', {}).get('index_uuid_mismatch_count'))
PY
du -sh '$OUTPUT_PATH'"
```

Confirm `info.total_episodes == summary.accepted`, `meta/episodes.jsonl` line count matches accepted episodes, and episode indices are continuous.

When using labels from a same-name regenerated dataset, validate UUID semantics explicitly:

```bash
ssh "$TARGET_HOST" "python3 - <<'PY'
from pathlib import Path
import json
out = Path('$OUTPUT_PATH')
labels_path = Path('$REMOTE_LABELS')  # or the original labels path if visible on this host
labels = json.loads(labels_path.read_text()).get('labels') or {}
uuid_status = {str(v.get('episode_uuid')): v.get('status') for v in labels.values() if isinstance(v, dict) and v.get('episode_uuid')}
mapping = [json.loads(line) for line in (out/'meta/qc_filter_accept_mapping.jsonl').read_text().splitlines() if line.strip()]
bad = [row for row in mapping if uuid_status.get(str(row.get('episode_uuid'))) != 'accept']
print('mapping rows', len(mapping), 'bad uuid rows', len(bad))
assert not bad
PY"
```

## Transfer Fallback

Only use this if direct generation on a host with source and destination access is impossible:

- Generate on a host that can read `SOURCE_DATASET`.
- Transfer to `TARGET_HOST:$OUTPUT_PATH.transfer` or a hidden `.name.transfer` directory.
- Use `rsync -aH --numeric-ids --partial --info=progress2`.
- Rename to `OUTPUT_PATH` only after validation.
- Clean temporary SSH keys, temp scripts, and partial transfer directories after completion or failure.

If throughput is only a few MB/s for a tens-of-GB dataset, stop the transfer and reassess run-host placement instead of waiting blindly.
