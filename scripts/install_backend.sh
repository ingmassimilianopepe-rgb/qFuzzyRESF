#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_DIR=${1:-"$ROOT/.venv"}
python3 -m venv "$ENV_DIR"
"$ENV_DIR/bin/python" -m pip install --upgrade pip
"$ENV_DIR/bin/python" -m pip install -r "$ROOT/backend/requirements.txt"
"$ENV_DIR/bin/python" "$ROOT/backend/run_pipeline.py" --check
printf '\nSelect this interpreter in the plugin: %s/bin/python\n' "$ENV_DIR"
