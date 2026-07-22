---
name: check-lerobot-dataset-integrity
description: Validate local or remote LeRobot datasets end to end, including v2/v2.1 metadata, episode numbering and UUIDs, Parquet readability and frame indexes, video presence and full decoding, and optional latent, instruction-embedding, and sidecar caches. Use when Codex is asked to check dataset integrity, completeness, corruption, episode/video counts, training readiness, or diagnose missing LeRobot files on an SSH server.
---

# Check LeRobot Dataset Integrity

Run the bundled deterministic checker on the machine that stores the dataset. Keep the operation read-only and report each dataset separately.

## Workflow

1. Resolve and verify the SSH endpoint before inspecting data. Use `ssh -G <alias>` and a short `hostname` command. For the currently known `H200-4075` endpoint, use `root@106.14.2.243 -p 4075` if the local alias incorrectly resolves `h200-4075`.
2. Verify remote dependencies with `python3 -c "import pyarrow; import av; import torch"`. `pyarrow` is required for every level, `av` for video decoding, and `torch` for full cache validation.
3. Run `scripts/check_lerobot_dataset.py` on the data host. Prefer `--level full` for a requested integrity check. Use `--level structure` only when the user explicitly wants a fast preflight, and clearly state that videos and caches were not fully read.
4. Preserve the script exit code: `0` means no integrity errors, `1` means one or more integrity failures, and `2` means the checker itself could not run.
5. Summarize errors first. Include the declared and observed episode/frame/video counts, whether every MP4 decoded, and whether optional caches were complete. Never describe a dataset as complete when only a structure check ran.

## Run Remotely

Copy the checker to a temporary path, run it, then remove only that temporary file:

```powershell
scp -P 4075 "$env:USERPROFILE\.codex\skills\check-lerobot-dataset-integrity\scripts\check_lerobot_dataset.py" root@106.14.2.243:/tmp/check_lerobot_dataset.py
ssh -p 4075 root@106.14.2.243 "python3 /tmp/check_lerobot_dataset.py --level full --jobs 8 /mnt/dataset/example"
ssh -p 4075 root@106.14.2.243 "rm -f /tmp/check_lerobot_dataset.py"
```

Pass multiple dataset roots in one invocation. Add `--json-out /tmp/report.json` when a machine-readable report is useful. Do not install packages or alter dataset files unless the user explicitly asks.

## Interpret Results

- Treat missing `meta/info.json`, core metadata JSONL, Parquet files, or declared video files as errors.
- Treat non-contiguous episode/frame/global indexes, mismatched UUID sets, declared total mismatches, unreadable Parquet content, video decode failures, and video/Parquet frame-count mismatches as errors.
- Optional cache families are not required when absent. If a cache family exists, require it to cover all applicable episodes/cameras and validate every cache file at `--level full`.
- Report warnings separately. Warnings do not change a passing exit code, but may identify noncanonical extras or metadata that could not be compared.
