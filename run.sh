#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-18080}"
DATASET_PATH="${DATASET_PATH:-/mnt/nm_dataset/dataset/giftbox_0628_1912episodes}"

TOKEN_ARGS=()
if [[ -n "${LQCP_TOKEN:-}" ]]; then
  TOKEN_ARGS=(--token "$LQCP_TOKEN")
fi

exec python3 server.py \
  --host "$HOST" \
  --port "$PORT" \
  --dataset "$DATASET_PATH" \
  "${TOKEN_ARGS[@]}"
