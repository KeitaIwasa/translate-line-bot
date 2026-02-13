#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo ".venv が見つかりません。先に以下を実行してください:"
  echo "  python3 -m venv .venv"
  echo "  .venv/bin/python -m pip install -r requirements.txt"
  exit 1
fi

exec "$VENV_PYTHON" -m pytest "$@"
