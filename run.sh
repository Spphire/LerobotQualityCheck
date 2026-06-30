#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-18080}"
DATASET_PATH="${DATASET_PATH:-/mnt/nm_dataset/dataset/giftbox_0628_1912episodes}"
# Raw episode metadata roots used to resolve collector names by episode UUID.
# Set LQCP_RAW_EPISODE_ROOTS to a comma-separated list when switching to nedf3
# or other raw-data roots, for example:
# LQCP_RAW_EPISODE_ROOTS=/mnt/nm_data/data/nedf3,/mnt/nm_data/data/midtrain
# Legacy single-root overrides LQCP_RAW_NEDF_ROOT and LQCP_RAW_MIDTRAIN_ROOT
# are still supported by server.py when LQCP_RAW_EPISODE_ROOTS is unset.

TOKEN_ARGS=()
if [[ -n "${LQCP_TOKEN:-}" ]]; then
  TOKEN_ARGS=(--token "$LQCP_TOKEN")
fi

exec python3 server.py \
  --host "$HOST" \
  --port "$PORT" \
  --dataset "$DATASET_PATH" \
  "${TOKEN_ARGS[@]}"
