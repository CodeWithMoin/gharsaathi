#!/usr/bin/env bash
# Install local dependencies and prepare configuration without overwriting secrets.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' "Missing uv. Install it before running setup." >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  printf '%s\n' "Missing npm/Node.js. Install Node.js 20+ before running setup." >&2
  exit 1
fi

if [[ ! -f "${REPO_ROOT}/.env" ]]; then
  cp "${REPO_ROOT}/.env.example" "${REPO_ROOT}/.env"
  printf '%s\n' "Created .env from .env.example. Add your local keys before live mode."
else
  printf '%s\n' "Keeping existing .env unchanged."
fi

if [[ ! -f "${REPO_ROOT}/frontend/.env.local" ]]; then
  cp "${REPO_ROOT}/frontend/.env.local.example" "${REPO_ROOT}/frontend/.env.local"
  printf '%s\n' "Created frontend/.env.local from its example."
else
  printf '%s\n' "Keeping existing frontend/.env.local unchanged."
fi

printf '%s\n' "Installing frontend dependencies"
(cd "${REPO_ROOT}/frontend" && npm install)

printf '%s\n' "Preparing backend dependencies"
(cd "${REPO_ROOT}/backend" && uv run --with-requirements requirements.txt \
  python -c "import app.main; print('Backend dependencies ready')")

printf '%s\n' "Setup complete. Run 'make dev' for safe demo mode or 'make dev-live' deliberately."
