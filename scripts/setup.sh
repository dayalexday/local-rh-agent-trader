#!/bin/bash
# Create the venv and install the package. Safe to re-run.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "${HOME}/.local/bin/python3.12" ]]; then
  PY="${HOME}/.local/bin/python3.12"
elif command -v python3.12 >/dev/null 2>&1; then
  PY="$(command -v python3.12)"
else
  PY="$(command -v python3)"
fi

echo "Using $PY"
"$PY" -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e .

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Wrote .env from .env.example (DRY_RUN=1)."
fi

echo "Setup done. Next: source .venv/bin/activate && python -m local_rh_agent status"
