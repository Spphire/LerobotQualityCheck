#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys


REMOTE_QUERY = r"""
set -e
cd "$PROJECT_DIR"
python3 - "$EPISODE_INDEX" "$DATASET_ID_OVERRIDE" <<'PY'
import json
import sqlite3
import sys
from pathlib import Path

episode_index = int(sys.argv[1])
dataset_id_override = sys.argv[2].strip()
settings_path = Path("qc_results/settings.json")
settings = json.load(settings_path.open(encoding="utf-8")) if settings_path.is_file() else {}
dataset_path = settings.get("dataset_path") or ""
dataset_source = settings.get("dataset_source") or dataset_path
dataset_id = dataset_id_override or settings.get("dataset_id") or ""
if not dataset_id:
    raise SystemExit("dataset_id is missing; pass --dataset-id")
db_path = Path("qc_results") / dataset_id / "labels.db"
if not db_path.is_file():
    raise SystemExit(f"labels.db not found: {db_path}")

con = sqlite3.connect(db_path)
con.row_factory = sqlite3.Row
current = con.execute(
    "SELECT dataset_id, episode_index, episode_name, episode_uuid, user, annotator, "
    "status, issues_json, note, updated_at "
    "FROM labels WHERE dataset_id = ? AND episode_index = ?",
    (dataset_id, episode_index),
).fetchone()
events = con.execute(
    "SELECT id, user, old_status, new_status, created_at, label_json "
    "FROM label_events WHERE dataset_id = ? AND episode_index = ? "
    "ORDER BY id DESC LIMIT 20",
    (dataset_id, episode_index),
).fetchall()
con.close()

payload = {
    "dataset_path": dataset_path,
    "dataset_source": dataset_source,
    "dataset_id": dataset_id,
    "labels_db": str(db_path),
    "episode_index": episode_index,
    "current_label": dict(current) if current else None,
    "recent_events": [dict(row) for row in events],
}
print(json.dumps(payload, ensure_ascii=False, indent=2))
PY
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query a LeRobot QC episode label from the production server.")
    parser.add_argument("episode_index", type=int, help="Episode index to query, for example 497.")
    parser.add_argument("--host", default="root@106.14.2.243", help="SSH host.")
    parser.add_argument("--port", default="3050", help="SSH port.")
    parser.add_argument("--project-dir", default="/mnt/LerobotQualityCheckPlatform", help="Remote production project directory.")
    parser.add_argument("--dataset-id", default="", help="Optional qc_results dataset id override.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    remote_env = (
        f"PROJECT_DIR={json.dumps(args.project_dir)} "
        f"EPISODE_INDEX={json.dumps(str(args.episode_index))} "
        f"DATASET_ID_OVERRIDE={json.dumps(args.dataset_id)} "
        "bash -s"
    )
    proc = subprocess.run(
        ["ssh", "-p", str(args.port), args.host, remote_env],
        input=REMOTE_QUERY.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"),
    )
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
