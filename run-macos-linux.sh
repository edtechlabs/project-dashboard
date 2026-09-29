#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v git >/dev/null 2>&1; then
  echo "Git is required but was not found in PATH." >&2
  exit 1
fi
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python git_dashboard.py
